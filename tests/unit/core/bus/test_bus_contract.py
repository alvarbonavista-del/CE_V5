"""Contract tests for the EventBus port DTOs and Protocol (ADR-013)."""

from __future__ import annotations

import dataclasses

import pytest

from ce_v5.core.bus import (
    BusMessage,
    Delivery,
    DlqReason,
    EventBus,
    Offset,
    ReceivedMessage,
)


def _sample_message() -> BusMessage:
    return BusMessage(
        event_id="evt-1",
        event_type="market.candle_closed",
        stream_key="binance:BTCUSDT:candle:1m",
        idempotency_key="binance:BTCUSDT:candle:1m:1720000000000",
        envelope=b'{"event_id": "evt-1"}',
    )


def test_bus_message_is_frozen() -> None:
    message = _sample_message()
    with pytest.raises(dataclasses.FrozenInstanceError):
        message.stream_key = "other"  # type: ignore[misc]


def test_received_message_carries_delivery() -> None:
    delivery = Delivery(
        topic="market",
        consumer_group="rules",
        offset=Offset("1720000000000-0"),
        delivery_count=1,
    )
    received = ReceivedMessage(message=_sample_message(), delivery=delivery)
    assert received.delivery.offset == Offset("1720000000000-0")
    assert received.message.idempotency_key.endswith("1720000000000")


def test_dlq_reason_fields() -> None:
    reason = DlqReason(reason_code="handler_error", attempts=5, detail="boom")
    assert reason.attempts == 5


class _RecordingBus:
    """Minimal in-test double proving the EventBus port is implementable."""

    def __init__(self) -> None:
        self.published: list[tuple[str, BusMessage]] = []
        self.latest_offset_calls: list[str] = []

    def publish(self, topic: str, message: BusMessage) -> Offset:
        self.published.append((topic, message))
        return Offset(str(len(self.published)))

    def ensure_group(self, topic: str, consumer_group: str) -> None:
        return None

    def latest_offset(self, topic: str) -> Offset | None:
        """El offset de la ULTIMA entrada de ese topic, o None si no hay ninguna."""
        self.latest_offset_calls.append(topic)
        for posicion in range(len(self.published), 0, -1):
            if self.published[posicion - 1][0] == topic:
                return Offset(str(posicion))
        return None

    def poll(
        self,
        topic: str,
        consumer_group: str,
        consumer_name: str,
        *,
        max_messages: int,
        block_ms: int,
    ) -> tuple[ReceivedMessage, ...]:
        return ()

    def ack(self, delivery: Delivery) -> None:
        return None

    def claim_stale(
        self,
        topic: str,
        consumer_group: str,
        consumer_name: str,
        *,
        min_idle_ms: int,
        max_messages: int,
    ) -> tuple[ReceivedMessage, ...]:
        return ()

    def dead_letter(self, received: ReceivedMessage, reason: DlqReason) -> None:
        return None

    def replay(
        self,
        topic: str,
        *,
        start: Offset | None,
        max_messages: int,
    ) -> tuple[ReceivedMessage, ...]:
        return ()


def _use_port(bus: EventBus, message: BusMessage) -> Offset:
    return bus.publish("market", message)


def test_recording_bus_satisfies_port() -> None:
    bus = _RecordingBus()
    assert isinstance(bus, EventBus)
    offset = _use_port(bus, _sample_message())
    assert offset == Offset("1")
    assert bus.published[0][0] == "market"


def test_recording_bus_latest_offset() -> None:
    # Un implementador del puerto no puede quedarse a medias: latest_offset (CA-12) es
    # parte del contrato, y el isinstance de arriba lo caza si falta.
    bus = _RecordingBus()
    assert bus.latest_offset("market") is None

    _use_port(bus, _sample_message())
    _use_port(bus, _sample_message())

    assert bus.latest_offset("market") == Offset("2")
    # Un topic sin entradas propias no hereda el offset de otro.
    assert bus.latest_offset("signal") is None
    assert bus.latest_offset_calls == ["market", "market", "signal"]
