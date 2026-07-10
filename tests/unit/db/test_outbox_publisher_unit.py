"""Tests unitarios del OutboxPublisher (sin Postgres ni Redis)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from ce_v5.core.bus import EventBus
from ce_v5.infra.db.outbox_publisher import (
    OutboxPublisher,
    OutboxPublishError,
    topic_for,
)
from ce_v5.infra.db.ports import Session, SqlParams

_VALID_ENVELOPE: dict[str, object] = {
    "event_type": "component.demo",
    "envelope_version": 1,
    "event_schema_version": 1,
    "source": "test",
    "idempotency_key": "idem-1",
    "stream_key": "stream-1",
    "scope": "system",
    "correlation_id": "corr-1",
    "payload": {},
}


def _row(idx: int, envelope: dict[str, object]) -> tuple[object, ...]:
    return (idx, uuid.uuid4(), "component.demo", "stream-1", f"idem-{idx}", envelope)


class _FakeSession:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows
        self.executed: list[tuple[str, SqlParams]] = []

    def execute(self, query: str, params: SqlParams = None) -> None:
        self.executed.append((query, params))

    def fetchone(
        self, query: str, params: SqlParams = None
    ) -> tuple[object, ...] | None:
        return None

    def fetchall(
        self, query: str, params: SqlParams = None
    ) -> list[tuple[object, ...]]:
        return self._rows


class _FakeDatabase:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.session = _FakeSession(rows)

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        yield self.session

    def close(self) -> None:
        return None


def test_topic_for_usa_la_familia() -> None:
    assert topic_for("market.candle_closed") == "market"


def test_drain_publica_y_marca(in_memory_bus: EventBus) -> None:
    db = _FakeDatabase([_row(1, dict(_VALID_ENVELOPE)), _row(2, dict(_VALID_ENVELOPE))])
    publisher = OutboxPublisher(db=db, bus=in_memory_bus)
    in_memory_bus.ensure_group("component", "g1")
    assert publisher.drain_once(batch_size=10) == 2
    assert any("UPDATE outbox" in query for query, _ in db.session.executed)
    received = in_memory_bus.poll("component", "g1", "c1", max_messages=10, block_ms=0)
    assert len(received) == 2


def test_drain_vacio_devuelve_cero(in_memory_bus: EventBus) -> None:
    publisher = OutboxPublisher(db=_FakeDatabase([]), bus=in_memory_bus)
    assert publisher.drain_once() == 0


def test_envelope_invalido_lanza(in_memory_bus: EventBus) -> None:
    publisher = OutboxPublisher(
        db=_FakeDatabase([_row(1, {"foo": "bar"})]), bus=in_memory_bus
    )
    with pytest.raises(OutboxPublishError):
        publisher.drain_once()
