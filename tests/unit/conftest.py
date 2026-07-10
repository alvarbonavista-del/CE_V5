"""Shared unit-test doubles and fixtures for the EventBus port (ADR-013).

``InMemoryEventBus`` is a faithful in-memory reference implementation of
the ``EventBus`` port, for unit tests that must not depend on Redis or
Docker. It is test-only support and is never wired into the application.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pytest

from ce_v5.core.bus import (
    BusMessage,
    ConsumeError,
    Delivery,
    DlqReason,
    EventBus,
    Offset,
    ReceivedMessage,
    UnknownOffsetError,
)


class _LogicalClock:
    """Deterministic millisecond clock advanced explicitly by tests."""

    def __init__(self) -> None:
        self.now_ms = 0

    def advance(self, ms: int) -> None:
        self.now_ms += ms


@dataclass
class _Entry:
    seq: int
    message: BusMessage


@dataclass
class _Pending:
    entry: _Entry
    delivery_count: int
    last_delivered_ms: int
    owner: str


@dataclass
class _GroupState:
    last_delivered_seq: int = 0
    pending: dict[str, _Pending] = field(default_factory=dict)


@dataclass
class _DeadLetter:
    message: BusMessage
    reason: DlqReason
    first_seen_ms: int
    last_seen_ms: int


class InMemoryEventBus:
    """In-memory reference implementation of the EventBus port.

    Models append-only topic logs, durable consumer groups with a pending
    list (delivered-but-unacked), stale reclaim by idle time, replay by
    offset and a dead-letter store. Delivery order is preserved per topic,
    and therefore per ``stream_key``.
    """

    def __init__(self, clock: _LogicalClock) -> None:
        self._clock = clock
        self._logs: dict[str, list[_Entry]] = {}
        self._groups: dict[tuple[str, str], _GroupState] = {}
        self.dead_letters: list[_DeadLetter] = []

    def _group(self, topic: str, consumer_group: str) -> _GroupState:
        state = self._groups.get((topic, consumer_group))
        if state is None:
            raise ConsumeError(
                f"unknown consumer group {consumer_group!r} on topic {topic!r}"
            )
        return state

    def publish(self, topic: str, message: BusMessage) -> Offset:
        log = self._logs.setdefault(topic, [])
        seq = len(log) + 1
        log.append(_Entry(seq=seq, message=message))
        return Offset(str(seq))

    def ensure_group(self, topic: str, consumer_group: str) -> None:
        self._logs.setdefault(topic, [])
        self._groups.setdefault((topic, consumer_group), _GroupState())

    def poll(
        self,
        topic: str,
        consumer_group: str,
        consumer_name: str,
        *,
        max_messages: int,
        block_ms: int,
    ) -> tuple[ReceivedMessage, ...]:
        _ = block_ms
        state = self._group(topic, consumer_group)
        received: list[ReceivedMessage] = []
        for entry in self._logs.get(topic, []):
            if entry.seq <= state.last_delivered_seq:
                continue
            if len(received) >= max_messages:
                break
            state.last_delivered_seq = entry.seq
            state.pending[str(entry.seq)] = _Pending(
                entry=entry,
                delivery_count=1,
                last_delivered_ms=self._clock.now_ms,
                owner=consumer_name,
            )
            received.append(
                ReceivedMessage(
                    message=entry.message,
                    delivery=Delivery(
                        topic=topic,
                        consumer_group=consumer_group,
                        offset=Offset(str(entry.seq)),
                        delivery_count=1,
                    ),
                )
            )
        return tuple(received)

    def ack(self, delivery: Delivery) -> None:
        state = self._groups.get((delivery.topic, delivery.consumer_group))
        if state is not None:
            state.pending.pop(delivery.offset.value, None)

    def claim_stale(
        self,
        topic: str,
        consumer_group: str,
        consumer_name: str,
        *,
        min_idle_ms: int,
        max_messages: int,
    ) -> tuple[ReceivedMessage, ...]:
        state = self._group(topic, consumer_group)
        now = self._clock.now_ms
        received: list[ReceivedMessage] = []
        ordered = sorted(state.pending.values(), key=lambda p: p.entry.seq)
        for pending in ordered:
            if len(received) >= max_messages:
                break
            if now - pending.last_delivered_ms < min_idle_ms:
                continue
            pending.delivery_count += 1
            pending.last_delivered_ms = now
            pending.owner = consumer_name
            received.append(
                ReceivedMessage(
                    message=pending.entry.message,
                    delivery=Delivery(
                        topic=topic,
                        consumer_group=consumer_group,
                        offset=Offset(str(pending.entry.seq)),
                        delivery_count=pending.delivery_count,
                    ),
                )
            )
        return tuple(received)

    def dead_letter(self, received: ReceivedMessage, reason: DlqReason) -> None:
        now = self._clock.now_ms
        self.dead_letters.append(
            _DeadLetter(
                message=received.message,
                reason=reason,
                first_seen_ms=now,
                last_seen_ms=now,
            )
        )
        self.ack(received.delivery)

    def replay(
        self,
        topic: str,
        *,
        start: Offset | None,
        max_messages: int,
    ) -> tuple[ReceivedMessage, ...]:
        log = self._logs.get(topic, [])
        max_seq = len(log)
        if start is None:
            after = 0
        else:
            try:
                after = int(start.value)
            except ValueError:
                raise UnknownOffsetError(
                    f"offset {start.value!r} is not a known position"
                ) from None
            if after < 0 or after > max_seq:
                raise UnknownOffsetError(
                    f"offset {start.value!r} is beyond retained history"
                )
        received: list[ReceivedMessage] = []
        for entry in log:
            if entry.seq <= after:
                continue
            if len(received) >= max_messages:
                break
            received.append(
                ReceivedMessage(
                    message=entry.message,
                    delivery=Delivery(
                        topic=topic,
                        consumer_group="",
                        offset=Offset(str(entry.seq)),
                        delivery_count=0,
                    ),
                )
            )
        return tuple(received)


@pytest.fixture
def _bus_clock() -> _LogicalClock:
    return _LogicalClock()


@pytest.fixture
def in_memory_bus(_bus_clock: _LogicalClock) -> EventBus:
    return InMemoryEventBus(clock=_bus_clock)


@pytest.fixture
def advance_time(_bus_clock: _LogicalClock) -> Callable[[int], None]:
    return _bus_clock.advance
