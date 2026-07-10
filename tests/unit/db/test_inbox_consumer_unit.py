"""Tests unitarios del InboxConsumer (sin Postgres ni Redis)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from ce_v5.core.bus import BusMessage, EventBus
from ce_v5.infra.db.inbox_consumer import InboxConsumer
from ce_v5.infra.db.ports import Session, SqlParams

_TOPIC = "component"
_GROUP = "g1"
_HANDLER = "demo"


class _FakeSession:
    def __init__(self, committed: set[object]) -> None:
        self._committed = committed
        self._pending: set[object] = set()

    def execute(self, query: str, params: SqlParams = None) -> None:
        return None

    def fetchone(
        self, query: str, params: SqlParams = None
    ) -> tuple[object, ...] | None:
        assert isinstance(params, Mapping)
        ik = params["ik"]
        if ik in self._committed or ik in self._pending:
            return None
        self._pending.add(ik)
        return (ik,)

    def fetchall(
        self, query: str, params: SqlParams = None
    ) -> list[tuple[object, ...]]:
        return []

    def commit(self) -> None:
        self._committed.update(self._pending)


class _FakeDatabase:
    def __init__(self) -> None:
        self._committed: set[object] = set()

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        session = _FakeSession(self._committed)
        yield session
        session.commit()

    def close(self) -> None:
        return None


def _message(event_id: str, idempotency_key: str) -> BusMessage:
    return BusMessage(
        event_id=event_id,
        event_type="component.demo",
        stream_key="stream-1",
        idempotency_key=idempotency_key,
        envelope=b"{}",
    )


def _consumer(
    bus: EventBus, applied: list[str], *, fail: bool = False
) -> InboxConsumer:
    def handler(session: Session, message: BusMessage) -> None:
        if fail:
            raise RuntimeError("efecto fallido")
        applied.append(message.idempotency_key)

    return InboxConsumer(
        db=_FakeDatabase(),
        bus=bus,
        handler=handler,
        consumer_group=_GROUP,
        handler_name=_HANDLER,
    )


def test_procesa_y_confirma(in_memory_bus: EventBus) -> None:
    applied: list[str] = []
    consumer = _consumer(in_memory_bus, applied)
    in_memory_bus.publish(_TOPIC, _message("e1", "idem-1"))
    result = consumer.run_once(_TOPIC, "c1", block_ms=0)
    assert result.processed == 1
    assert applied == ["idem-1"]
    again = consumer.run_once(_TOPIC, "c1", block_ms=0)
    assert again.processed == 0


def test_deduplica_misma_idempotency_key(in_memory_bus: EventBus) -> None:
    applied: list[str] = []
    consumer = _consumer(in_memory_bus, applied)
    in_memory_bus.publish(_TOPIC, _message("e1", "idem-dup"))
    in_memory_bus.publish(_TOPIC, _message("e2", "idem-dup"))
    result = consumer.run_once(_TOPIC, "c1", block_ms=0)
    assert result.processed == 1
    assert result.deduplicated == 1
    assert applied == ["idem-dup"]


def test_dlq_tras_max_intentos(in_memory_bus: EventBus) -> None:
    applied: list[str] = []
    consumer = _consumer(in_memory_bus, applied)
    in_memory_bus.publish(_TOPIC, _message("e1", "idem-1"))
    result = consumer.run_once(_TOPIC, "c1", block_ms=0, max_attempts=0)
    assert result.dead_lettered == 1
    assert applied == []


def test_handler_que_falla_no_confirma(in_memory_bus: EventBus) -> None:
    applied: list[str] = []
    consumer = _consumer(in_memory_bus, applied, fail=True)
    in_memory_bus.publish(_TOPIC, _message("e1", "idem-1"))
    result = consumer.run_once(_TOPIC, "c1", block_ms=0)
    assert result.failed == 1
    assert applied == []
    pending = in_memory_bus.claim_stale(
        _TOPIC, _GROUP, "c2", min_idle_ms=0, max_messages=10
    )
    assert len(pending) == 1
