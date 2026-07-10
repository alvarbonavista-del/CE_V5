"""Tests de integracion del OutboxPublisher (requieren Postgres y Redis).
Se saltan si falta CE_V5_DATABASE_URL o CE_V5_REDIS_URL. NUNCA datos
reales: servicios de juguete (DOC_ENTREGABLES sec.5).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
import redis

from ce_v5.infra.bus_redis import RedisBusConfig, RedisEventBus, create_client
from ce_v5.infra.db.config import DbConfig
from ce_v5.infra.db.migrations.runner import apply_migrations
from ce_v5.infra.db.outbox import OutboxEvent, write_atomically
from ce_v5.infra.db.outbox_publisher import (
    OutboxPublisher,
    OutboxPublishError,
    topic_for,
)
from ce_v5.infra.db.ports import Database
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase

_DSN = os.environ.get("CE_V5_DATABASE_URL")
_URL = os.environ.get("CE_V5_REDIS_URL")
pytestmark = pytest.mark.skipif(
    _DSN is None or _URL is None,
    reason="requiere CE_V5_DATABASE_URL y CE_V5_REDIS_URL",
)


def _valid_envelope(idempotency_key: str) -> dict[str, object]:
    return {
        "event_type": "component.demo",
        "envelope_version": 1,
        "event_schema_version": 1,
        "source": "test",
        "idempotency_key": idempotency_key,
        "stream_key": "stream-demo",
        "scope": "system",
        "correlation_id": "corr-1",
        "payload": {},
    }


def _event(envelope: dict[str, object]) -> OutboxEvent:
    return OutboxEvent(
        event_id=uuid.uuid4(),
        idempotency_key=str(envelope["idempotency_key"]),
        stream_key="stream-demo",
        event_type="component.demo",
        envelope=envelope,
    )


@pytest.fixture
def db() -> Iterator[Database]:
    assert _DSN is not None
    database = PsycopgDatabase(DbConfig(dsn=_DSN))
    apply_migrations(database)
    with database.transaction() as session:
        session.execute("TRUNCATE outbox")
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def config() -> RedisBusConfig:
    assert _URL is not None
    return RedisBusConfig(url=_URL, namespace="test-" + uuid.uuid4().hex)


@pytest.fixture
def client(config: RedisBusConfig) -> Iterator[redis.Redis]:
    conn = create_client(config)
    try:
        yield conn
    finally:
        for key in conn.scan_iter(match=f"{config.namespace}:*"):
            conn.delete(key)
        conn.close()


@pytest.fixture
def bus(client: redis.Redis, config: RedisBusConfig) -> RedisEventBus:
    return RedisEventBus(client, config)


@pytest.fixture
def publisher(db: Database, bus: RedisEventBus) -> OutboxPublisher:
    return OutboxPublisher(db=db, bus=bus)


def _unpublished(db: Database) -> int:
    with db.transaction() as session:
        row = session.fetchone("SELECT count(*) FROM outbox WHERE published_at IS NULL")
    assert row is not None
    value = row[0]
    assert isinstance(value, int)
    return value


def test_drena_publica_y_marca(
    db: Database, bus: RedisEventBus, publisher: OutboxPublisher
) -> None:
    event = _event(_valid_envelope("idem-1"))
    write_atomically(db, business=[], event=event)
    assert publisher.drain_once() == 1
    assert _unpublished(db) == 0
    topic = topic_for(event.event_type)
    bus.ensure_group(topic, "g1")
    received = bus.poll(topic, "g1", "c1", max_messages=10, block_ms=0)
    assert len(received) == 1
    assert received[0].message.idempotency_key == "idem-1"


def test_drena_es_idempotente(db: Database, publisher: OutboxPublisher) -> None:
    write_atomically(db, business=[], event=_event(_valid_envelope("idem-2")))
    assert publisher.drain_once() == 1
    assert publisher.drain_once() == 0


def test_envelope_invalido_no_publica_ni_marca(
    db: Database, publisher: OutboxPublisher
) -> None:
    event = OutboxEvent(
        event_id=uuid.uuid4(),
        idempotency_key="idem-3",
        stream_key="stream-demo",
        event_type="component.demo",
        envelope={"foo": "bar"},
    )
    write_atomically(db, business=[], event=event)
    with pytest.raises(OutboxPublishError):
        publisher.drain_once()
    assert _unpublished(db) == 1
