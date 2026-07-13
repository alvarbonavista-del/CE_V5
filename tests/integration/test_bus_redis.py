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

from ce_v5.core.bus import BusMessage, DlqReason, EventBus, UnknownOffsetError
from ce_v5.infra.bus_redis import RedisBusConfig, RedisEventBus, create_client
from support.inmemory_bus import InMemoryEventBus, LogicalClock

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


@pytest.fixture
def in_memory_bus() -> EventBus:
    """El MISMO doble que usan los tests unitarios (no una copia que se separe)."""
    return InMemoryEventBus(clock=LogicalClock())


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


# --- latest_offset (CA-12) ----------------------------------------------------------


def test_latest_offset_de_un_topic_vacio_es_none(bus: RedisEventBus) -> None:
    assert bus.latest_offset(_TOPIC) is None


def test_latest_offset_apunta_al_ultimo_publicado(bus: RedisEventBus) -> None:
    offsets = [bus.publish(_TOPIC, _message(seq, "A")) for seq in range(1, 6)]
    assert bus.latest_offset(_TOPIC) == offsets[-1]

    # Y el cursor es EXCLUSIVO: desde el final no llega nada... hasta que llega algo.
    cursor = bus.latest_offset(_TOPIC)
    assert bus.replay(_TOPIC, start=cursor, max_messages=10) == ()
    bus.publish(_TOPIC, _message(99, "A"))
    nuevos = bus.replay(_TOPIC, start=cursor, max_messages=10)
    assert [r.message.idempotency_key for r in nuevos] == ["A:99"]


def test_equivalencia_de_latest_offset_entre_los_dos_buses(
    bus: RedisEventBus, in_memory_bus: EventBus
) -> None:
    """Punto 5 de CA-12: el doble en memoria y el motor real dicen LO MISMO.

    No se comparan las cadenas de offset (son especificas de cada backend: Redis usa
    'particion|id-de-entrada' y el doble un numero de secuencia), sino la POSICION que
    designan: el ultimo mensaje publicado, y nada mas que el.
    """
    assert bus.latest_offset(_TOPIC) is None
    assert in_memory_bus.latest_offset(_TOPIC) is None

    for seq in range(1, 8):
        bus.publish(_TOPIC, _message(seq, "A"))
        in_memory_bus.publish(_TOPIC, _message(seq, "A"))

    cursor_redis = bus.latest_offset(_TOPIC)
    cursor_memoria = in_memory_bus.latest_offset(_TOPIC)
    assert cursor_redis is not None
    assert cursor_memoria is not None

    # Misma posicion: desde ella, los dos buses estan al dia...
    assert bus.replay(_TOPIC, start=cursor_redis, max_messages=10) == ()
    assert in_memory_bus.replay(_TOPIC, start=cursor_memoria, max_messages=10) == ()

    # ...y los dos entregan exactamente el siguiente, y solo ese.
    bus.publish(_TOPIC, _message(42, "A"))
    in_memory_bus.publish(_TOPIC, _message(42, "A"))
    desde_redis = bus.replay(_TOPIC, start=cursor_redis, max_messages=10)
    desde_memoria = in_memory_bus.replay(_TOPIC, start=cursor_memoria, max_messages=10)
    assert [r.message.idempotency_key for r in desde_redis] == ["A:42"]
    assert [r.message.idempotency_key for r in desde_memoria] == ["A:42"]
