"""Tests de integracion del InboxConsumer (requieren Postgres y Redis).
Se saltan si falta CE_V5_DATABASE_URL o CE_V5_REDIS_URL. NUNCA datos
reales: servicios de juguete (DOC_ENTREGABLES sec.5).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
import redis

from ce_v5.core.bus import BusMessage
from ce_v5.infra.bus_redis import RedisBusConfig, RedisEventBus, create_client
from ce_v5.infra.db.config import DbConfig
from ce_v5.infra.db.inbox_consumer import InboxConsumer
from ce_v5.infra.db.migrations.runner import apply_migrations
from ce_v5.infra.db.ports import Database, Session
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase

_DSN = os.environ.get("CE_V5_DATABASE_URL")
_URL = os.environ.get("CE_V5_REDIS_URL")
pytestmark = pytest.mark.skipif(
    _DSN is None or _URL is None,
    reason="requiere CE_V5_DATABASE_URL y CE_V5_REDIS_URL",
)

_TOPIC = "component"
_GROUP = "g1"
_HANDLER = "demo"


def _message(event_id: str, idempotency_key: str) -> BusMessage:
    return BusMessage(
        event_id=event_id,
        event_type="component.demo",
        stream_key="stream-1",
        idempotency_key=idempotency_key,
        envelope=b"{}",
    )


@pytest.fixture
def db() -> Iterator[Database]:
    assert _DSN is not None
    database = PsycopgDatabase(DbConfig(dsn=_DSN))
    apply_migrations(database)
    with database.transaction() as session:
        session.execute("TRUNCATE inbox")
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


def _count(db: Database, query: str, params: list[object] | None = None) -> int:
    with db.transaction() as session:
        row = session.fetchone(query, params)
    assert row is not None
    value = row[0]
    assert isinstance(value, int)
    return value


def test_idempotencia_real_end_to_end(db: Database, bus: RedisEventBus) -> None:
    with db.transaction() as session:
        session.execute("CREATE TEMP TABLE demo_efecto (ik text PRIMARY KEY)")

    def handler(session: Session, message: BusMessage) -> None:
        session.execute(
            "INSERT INTO demo_efecto (ik) VALUES (%s)", [message.idempotency_key]
        )

    consumer = InboxConsumer(
        db=db,
        bus=bus,
        handler=handler,
        consumer_group=_GROUP,
        handler_name=_HANDLER,
    )
    bus.publish(_TOPIC, _message("e1", "idem-1"))
    bus.publish(_TOPIC, _message("e2", "idem-1"))
    result = consumer.run_once(_TOPIC, "c1", block_ms=0)
    assert result.processed == 1
    assert result.deduplicated == 1
    assert _count(db, "SELECT count(*) FROM demo_efecto") == 1
    assert (
        _count(
            db,
            "SELECT count(*) FROM inbox WHERE idempotency_key = %s",
            ["idem-1"],
        )
        == 1
    )


def test_dlq_tras_reintentos_reales(
    db: Database, bus: RedisEventBus, client: redis.Redis, config: RedisBusConfig
) -> None:
    def failing_handler(session: Session, message: BusMessage) -> None:
        raise RuntimeError("efecto fallido")

    consumer = InboxConsumer(
        db=db,
        bus=bus,
        handler=failing_handler,
        consumer_group=_GROUP,
        handler_name=_HANDLER,
    )
    bus.publish(_TOPIC, _message("e1", "idem-x"))
    first = consumer.run_once(_TOPIC, "c1", block_ms=0, min_idle_ms=0, max_attempts=1)
    assert first.failed == 1
    second = consumer.run_once(_TOPIC, "c1", block_ms=0, min_idle_ms=0, max_attempts=1)
    assert second.dead_lettered == 1
    entries = client.xrange(f"{config.namespace}:{_TOPIC}:dlq")
    assert entries is not None
    assert len(entries) == 1
