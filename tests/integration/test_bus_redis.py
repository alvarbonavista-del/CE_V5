"""Tests de integracion del EventBus sobre Redis Streams (ADR-013).
Requieren un Redis local; se saltan si no esta definido CE_V5_REDIS_URL.
NUNCA datos reales: Redis de juguete (DOC_ENTREGABLES sec.5).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import redis

from ce_v5.core.bus import BusMessage, DlqReason, UnknownOffsetError
from ce_v5.infra.bus_redis import RedisBusConfig, RedisEventBus, create_client

_URL = os.environ.get("CE_V5_REDIS_URL")
pytestmark = pytest.mark.skipif(
    _URL is None, reason="requiere CE_V5_REDIS_URL (Redis local)"
)

_TOPIC = "market"
_GROUP = "rules"


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


def _message(seq: int, stream_key: str) -> BusMessage:
    return BusMessage(
        event_id=f"evt-{seq}",
        event_type="market.candle_closed",
        stream_key=stream_key,
        idempotency_key=f"{stream_key}:{seq}",
        envelope=f"seq-{seq}".encode(),
    )


def test_publish_y_consume_idempotente(bus: RedisEventBus) -> None:
    bus.ensure_group(_TOPIC, _GROUP)
    bus.publish(_TOPIC, _message(1, "A"))
    first = bus.poll(_TOPIC, _GROUP, "c1", max_messages=10, block_ms=0)
    assert len(first) == 1
    bus.ack(first[0].delivery)
    second = bus.poll(_TOPIC, _GROUP, "c1", max_messages=10, block_ms=0)
    assert second == ()


def test_orden_por_stream_key(bus: RedisEventBus) -> None:
    bus.ensure_group(_TOPIC, _GROUP)
    bus.publish(_TOPIC, _message(1, "A"))
    bus.publish(_TOPIC, _message(2, "B"))
    bus.publish(_TOPIC, _message(3, "A"))
    received = bus.poll(_TOPIC, _GROUP, "c1", max_messages=10, block_ms=0)
    a_events = [r.message.event_id for r in received if r.message.stream_key == "A"]
    assert a_events == ["evt-1", "evt-3"]


def test_reclaim_de_mensaje_sin_ack(bus: RedisEventBus) -> None:
    bus.ensure_group(_TOPIC, _GROUP)
    bus.publish(_TOPIC, _message(1, "A"))
    bus.poll(_TOPIC, _GROUP, "c1", max_messages=10, block_ms=0)
    reclaimed = bus.claim_stale(_TOPIC, _GROUP, "c2", min_idle_ms=0, max_messages=10)
    assert len(reclaimed) == 1
    assert reclaimed[0].delivery.delivery_count == 2


def test_ack_evita_reclaim(bus: RedisEventBus) -> None:
    bus.ensure_group(_TOPIC, _GROUP)
    bus.publish(_TOPIC, _message(1, "A"))
    received = bus.poll(_TOPIC, _GROUP, "c1", max_messages=10, block_ms=0)
    bus.ack(received[0].delivery)
    reclaimed = bus.claim_stale(_TOPIC, _GROUP, "c2", min_idle_ms=0, max_messages=10)
    assert reclaimed == ()


def test_dlq_recibe_entrada_observable(
    bus: RedisEventBus, client: redis.Redis, config: RedisBusConfig
) -> None:
    bus.ensure_group(_TOPIC, _GROUP)
    bus.publish(_TOPIC, _message(1, "A"))
    received = bus.poll(_TOPIC, _GROUP, "c1", max_messages=10, block_ms=0)
    bus.dead_letter(
        received[0], DlqReason(reason_code="handler_error", attempts=5, detail="boom")
    )
    entries: Any = client.xrange(f"{config.namespace}:{_TOPIC}:dlq")
    assert len(entries) == 1
    fields = entries[0][1]
    assert fields[b"reason_code"] == b"handler_error"
    assert fields[b"owner"] == b"ops"
    assert fields[b"attempts"] == b"5"
    reclaimed = bus.claim_stale(_TOPIC, _GROUP, "c2", min_idle_ms=0, max_messages=10)
    assert reclaimed == ()


def test_replay_desde_inicio_y_offset(bus: RedisEventBus) -> None:
    first = bus.publish(_TOPIC, _message(1, "A"))
    bus.publish(_TOPIC, _message(2, "A"))
    bus.publish(_TOPIC, _message(3, "A"))
    todos = bus.replay(_TOPIC, start=None, max_messages=10)
    cola = bus.replay(_TOPIC, start=first, max_messages=10)
    assert [r.message.event_id for r in todos] == ["evt-1", "evt-2", "evt-3"]
    assert [r.message.event_id for r in cola] == ["evt-2", "evt-3"]


def test_replay_offset_trimmed_lanza_error(
    bus: RedisEventBus, client: redis.Redis, config: RedisBusConfig
) -> None:
    first = bus.publish(_TOPIC, _message(1, "A"))
    bus.publish(_TOPIC, _message(2, "A"))
    client.xtrim(f"{config.namespace}:{_TOPIC}:0", maxlen=1, approximate=False)
    with pytest.raises(UnknownOffsetError):
        bus.replay(_TOPIC, start=first, max_messages=10)


def test_dlq_entrada_tiene_todos_los_campos_adr013(
    bus: RedisEventBus, client: redis.Redis, config: RedisBusConfig
) -> None:
    bus.ensure_group(_TOPIC, _GROUP)
    bus.publish(_TOPIC, _message(1, "A"))
    received = bus.poll(_TOPIC, _GROUP, "c1", max_messages=10, block_ms=0)
    bus.dead_letter(
        received[0], DlqReason(reason_code="handler_error", attempts=5, detail="boom")
    )
    entries: Any = client.xrange(f"{config.namespace}:{_TOPIC}:dlq")
    assert len(entries) == 1
    fields = entries[0][1]
    for field in (
        b"owner",
        b"reason_code",
        b"attempts",
        b"detail",
        b"procedure",
        b"first_seen_at",
        b"last_seen_at",
        b"origin_topic",
        b"origin_offset",
    ):
        assert field in fields
    assert int(fields[b"first_seen_at"]) > 0
    assert int(fields[b"last_seen_at"]) > 0
    assert fields[b"origin_topic"] == _TOPIC.encode()
