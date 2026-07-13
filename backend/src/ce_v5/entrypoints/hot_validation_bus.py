"""Arnes de validacion en caliente del EventBus (P03, ADR-013).

Composition root minimo: cablea los adapters concretos (PostgreSQL de P02b
y Redis Streams) y ejerce el publisher y el consumer para demostrar el
reinicio de consumidor SIN perder ni duplicar. En un solo proceso se simula
la caida del consumidor A (muere tras persistir el efecto y ANTES del ACK) y
el arranque del consumidor B, que reclama lo pendiente y termina. La
idempotencia real (inbox de P02b) evita repetir el efecto ya aplicado por A.

Uso: python -m ce_v5.entrypoints.hot_validation_bus
Requiere CE_V5_DATABASE_URL y CE_V5_REDIS_URL (servicios de juguete; NUNCA
datos reales).
"""

from __future__ import annotations

import uuid

from ce_v5.core.bus import (
    BusMessage,
    Delivery,
    DlqReason,
    EventBus,
    Offset,
    ReceivedMessage,
)
from ce_v5.infra.bus_redis import RedisBusConfig, RedisEventBus, create_client
from ce_v5.infra.db.config import DbConfig
from ce_v5.infra.db.inbox_consumer import InboxConsumer
from ce_v5.infra.db.migrations.runner import apply_migrations
from ce_v5.infra.db.outbox import OutboxEvent, write_atomically
from ce_v5.infra.db.outbox_publisher import OutboxPublisher
from ce_v5.infra.db.ports import Database, Session
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase

_TOPIC = "component"
_GROUP = "bus-demo"
_HANDLER = "bus-demo"
_EVENT_COUNT = 20


class _CrashBeforeAckBus:
    """Envuelve un EventBus real pero revienta en el ACK (simula una caida)."""

    def __init__(self, inner: EventBus) -> None:
        self._inner = inner

    def publish(self, topic: str, message: BusMessage) -> Offset:
        return self._inner.publish(topic, message)

    def ensure_group(self, topic: str, consumer_group: str) -> None:
        self._inner.ensure_group(topic, consumer_group)

    def poll(
        self,
        topic: str,
        consumer_group: str,
        consumer_name: str,
        *,
        max_messages: int,
        block_ms: int,
    ) -> tuple[ReceivedMessage, ...]:
        return self._inner.poll(
            topic,
            consumer_group,
            consumer_name,
            max_messages=max_messages,
            block_ms=block_ms,
        )

    def ack(self, delivery: Delivery) -> None:
        raise RuntimeError("caida simulada: el consumidor A muere antes del ACK")

    def claim_stale(
        self,
        topic: str,
        consumer_group: str,
        consumer_name: str,
        *,
        min_idle_ms: int,
        max_messages: int,
    ) -> tuple[ReceivedMessage, ...]:
        return self._inner.claim_stale(
            topic,
            consumer_group,
            consumer_name,
            min_idle_ms=min_idle_ms,
            max_messages=max_messages,
        )

    def dead_letter(self, received: ReceivedMessage, reason: DlqReason) -> None:
        self._inner.dead_letter(received, reason)

    def latest_offset(self, topic: str) -> Offset | None:
        return self._inner.latest_offset(topic)

    def replay(
        self, topic: str, *, start: Offset | None, max_messages: int
    ) -> tuple[ReceivedMessage, ...]:
        return self._inner.replay(topic, start=start, max_messages=max_messages)


def _handler(session: Session, message: BusMessage) -> None:
    session.execute(
        "INSERT INTO bus_demo_results (idempotency_key) VALUES (%s)",
        [message.idempotency_key],
    )


def _demo_event(index: int) -> OutboxEvent:
    idempotency_key = f"bus-demo-{index}"
    stream_key = f"stream-{index % 4}"
    envelope: dict[str, object] = {
        "event_type": "component.demo",
        "envelope_version": 1,
        "event_schema_version": 1,
        "source": "hot-validation",
        "idempotency_key": idempotency_key,
        "stream_key": stream_key,
        "scope": "system",
        "correlation_id": "corr-demo",
        "payload": {},
    }
    return OutboxEvent(
        event_id=uuid.uuid4(),
        idempotency_key=idempotency_key,
        stream_key=stream_key,
        event_type="component.demo",
        envelope=envelope,
    )


def _count(db: Database, query: str) -> int:
    with db.transaction() as session:
        row = session.fetchone(query)
    if row is None:
        return 0
    value = row[0]
    assert isinstance(value, int)
    return value


def _run(db: Database, bus: RedisEventBus) -> None:
    apply_migrations(db)
    with db.transaction() as session:
        session.execute("TRUNCATE outbox")
        session.execute("TRUNCATE inbox")
        session.execute(
            "CREATE TEMP TABLE bus_demo_results (idempotency_key text PRIMARY KEY)"
        )

    for index in range(_EVENT_COUNT):
        write_atomically(db, business=[], event=_demo_event(index))

    publisher = OutboxPublisher(db=db, bus=bus)
    published = 0
    while True:
        drained = publisher.drain_once(batch_size=100)
        published += drained
        if drained == 0:
            break

    consumer_a = InboxConsumer(
        db=db,
        bus=_CrashBeforeAckBus(bus),
        handler=_handler,
        consumer_group=_GROUP,
        handler_name=_HANDLER,
    )
    crashed = False
    try:
        consumer_a.run_once(_TOPIC, "A", block_ms=0, min_idle_ms=0)
    except RuntimeError:
        crashed = True
    effects_after_a = _count(db, "SELECT count(*) FROM bus_demo_results")

    consumer_b = InboxConsumer(
        db=db,
        bus=bus,
        handler=_handler,
        consumer_group=_GROUP,
        handler_name=_HANDLER,
    )
    processed = 0
    deduplicated = 0
    for _ in range(5):
        result = consumer_b.run_once(_TOPIC, "B", block_ms=0, min_idle_ms=0)
        processed += result.processed
        deduplicated += result.deduplicated
        if result.processed == 0 and result.deduplicated == 0:
            break

    effects_total = _count(db, "SELECT count(*) FROM bus_demo_results")
    distinct = _count(
        db, "SELECT count(DISTINCT idempotency_key) FROM bus_demo_results"
    )

    print("=== Validacion en caliente P03: reinicio de consumidor ===")
    print(f"Eventos sembrados y publicados al bus: {published}/{_EVENT_COUNT}")
    print(
        f"Consumidor A cae antes del ACK: {crashed}; efectos tras A: {effects_after_a}"
    )
    print(
        f"Consumidor B (reinicio): procesados={processed}, deduplicados={deduplicated}"
    )
    print(
        f"Efectos persistidos: {effects_total} (idempotency_key distintos: {distinct})"
    )
    ok = (
        published == _EVENT_COUNT
        and effects_total == _EVENT_COUNT
        and distinct == _EVENT_COUNT
        and deduplicated >= 1
    )
    print("RESULTADO:", "OK: ningun evento perdido ni duplicado" if ok else "FALLO")


def main() -> None:
    base = RedisBusConfig.from_env()
    bus_config = RedisBusConfig(url=base.url, namespace="bus-demo-" + uuid.uuid4().hex)
    client = create_client(bus_config)
    bus = RedisEventBus(client, bus_config)
    db = PsycopgDatabase(DbConfig.from_env())
    try:
        _run(db, bus)
    finally:
        for key in client.scan_iter(match=f"{bus_config.namespace}:*"):
            client.delete(key)
        client.close()
        db.close()


if __name__ == "__main__":
    main()
