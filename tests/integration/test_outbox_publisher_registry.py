"""Tests de integracion del OutboxPublisher con el registro de payloads (CA-06).

Demuestran que el publisher valida contra la clase CONCRETA del payload: un
policy.* con contenido real SALE al bus con el payload INTACTO; un component.*
tambien (no esta hardcodeado a policy); un payload invalido, un tipo no
registrado, un tipo diferido o un event_schema_version incoherente FALLAN sin
publicar ni marcar la fila. Requieren Postgres y Redis.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator, Mapping
from decimal import Decimal

import pytest
import redis

from ce_v5.infra.bus_redis import RedisBusConfig, RedisEventBus, create_client
from ce_v5.infra.db.outbox import OutboxEvent, write_atomically
from ce_v5.infra.db.outbox_publisher import (
    OutboxPublisher,
    OutboxPublishError,
    topic_for,
)
from ce_v5.infra.db.ports import Database
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from source.families import registry
from source.families.market import (
    CandleClosedPayload,
    MarketCandleEventType,
    MarketType,
    Timeframe,
)
from source.families.registry import (
    DEFERRED_STATUS,
    DeferredEventType,
    DeferredEventTypeError,
    UnknownEventTypePayloadError,
)
from source.time import MaturityState

_DSN = os.environ.get("CE_V5_DATABASE_URL")
_URL = os.environ.get("CE_V5_REDIS_URL")
pytestmark = pytest.mark.skipif(
    _DSN is None or _URL is None,
    reason="requiere CE_V5_DATABASE_URL y CE_V5_REDIS_URL",
)

# Tipo de ejemplo para probar el GUARDIA de diferidos sin usar ningun market.*:
# desde P07 no queda ningun tipo diferido real, pero el mecanismo sigue vivo.
_DEFERRED_ET = "datasource.demo_deferred"

# Ventana 1m alineada (2026-07-14T00:00:00Z): una vela desalineada la rechaza el
# contrato.
_OPEN_TIME = 1_784_073_600_000

_POLICY_PAYLOAD = {
    "tenant_id": "t1",
    "reason": "plan_changed",
    "policy_version": "v1",
}
_COMPONENT_PAYLOAD = {
    "component_id": "sample",
    "component_version": "1.0.0",
    "component_instance_id": "inst-1",
    "lifecycle_scope": "global",
    "new_state": "running",
    "health_status": "healthy",
    "readiness_status": "ready",
}


def _envelope(
    event_type: str, idempotency_key: str, payload: Mapping[str, object]
) -> dict[str, object]:
    return {
        "event_type": event_type,
        "envelope_version": 1,
        "event_schema_version": 1,
        "source": "test",
        "idempotency_key": idempotency_key,
        "stream_key": "stream-demo",
        "scope": "system",
        "correlation_id": "corr-1",
        "payload": payload,
    }


def _enqueue(db: Database, envelope: dict[str, object]) -> None:
    write_atomically(
        db,
        business=[],
        event=OutboxEvent(
            event_id=uuid.uuid4(),
            idempotency_key=str(envelope["idempotency_key"]),
            stream_key=str(envelope["stream_key"]),
            event_type=str(envelope["event_type"]),
            envelope=envelope,
        ),
    )


def _unpublished(db: Database) -> int:
    with db.transaction() as session:
        row = session.fetchone("SELECT count(*) FROM outbox WHERE published_at IS NULL")
    assert row is not None
    value = row[0]
    assert isinstance(value, int)
    return value


@pytest.fixture
def db(app_db: PsycopgDatabase) -> Iterator[Database]:
    with app_db.transaction() as session:
        session.execute("DELETE FROM outbox")
    yield app_db


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


def _received_envelope(bus: RedisEventBus, event_type: str) -> dict[str, object]:
    topic = topic_for(event_type)
    bus.ensure_group(topic, "g1")
    received = bus.poll(topic, "g1", "c1", max_messages=10, block_ms=0)
    assert len(received) == 1
    parsed = json.loads(received[0].message.envelope)
    assert isinstance(parsed, dict)
    return parsed


def test_b_policy_con_contenido_sale_al_bus_intacto(
    db: Database, bus: RedisEventBus, publisher: OutboxPublisher
) -> None:
    _enqueue(db, _envelope("policy.subject_invalidated", "idem-b", _POLICY_PAYLOAD))
    assert publisher.drain_once() == 1
    assert _unpublished(db) == 0
    got = _received_envelope(bus, "policy.subject_invalidated")
    assert got["payload"] == _POLICY_PAYLOAD  # payload intacto, campo a campo


def test_e_regresion_defecto_ca06_payload_con_contenido(
    db: Database, publisher: OutboxPublisher
) -> None:
    # REGRESION CA-06: este payload con CONTENIDO habria sido RECHAZADO antes
    # contra EventPayload base (extra=forbid), aunque es valido; ahora pasa
    # contra su clase concreta y se publica.
    _enqueue(db, _envelope("policy.subject_invalidated", "idem-e", _POLICY_PAYLOAD))
    assert publisher.drain_once() == 1
    assert _unpublished(db) == 0


def test_c_payload_con_campo_extra_no_publica_ni_marca(
    db: Database, publisher: OutboxPublisher
) -> None:
    payload = {**_POLICY_PAYLOAD, "campo_de_mas": "x"}
    _enqueue(db, _envelope("policy.subject_invalidated", "idem-c1", payload))
    with pytest.raises(OutboxPublishError):
        publisher.drain_once()
    assert _unpublished(db) == 1


def test_c_payload_sin_campo_requerido_no_publica_ni_marca(
    db: Database, publisher: OutboxPublisher
) -> None:
    _enqueue(
        db, _envelope("policy.subject_invalidated", "idem-c2", {"tenant_id": "t1"})
    )
    with pytest.raises(OutboxPublishError):
        publisher.drain_once()
    assert _unpublished(db) == 1


def test_d_tipo_no_registrado_no_publica_ni_marca(
    db: Database, publisher: OutboxPublisher
) -> None:
    _enqueue(db, _envelope("component.demo", "idem-d", {}))
    with pytest.raises(UnknownEventTypePayloadError):
        publisher.drain_once()
    assert _unpublished(db) == 1


def test_tipo_diferido_no_publica_ni_marca(
    db: Database, publisher: OutboxPublisher, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Desde P07 NO queda ningun tipo diferido real (los tres market.* ya tienen
    # payload y productor). El GUARDIA sigue siendo necesario para las piezas que
    # vengan, asi que se prueba con un diferido INYECTADO: un tipo cuyo payload y
    # productor no existen no se publica NI se marca la fila. Misma intencion que
    # antes; solo cambia el ejemplo.
    monkeypatch.setitem(
        registry.DEFERRED_EVENT_TYPES,
        _DEFERRED_ET,
        DeferredEventType(
            event_type=_DEFERRED_ET,
            family="datasource",
            motivo="tipo de ejemplo para probar el guardia de diferidos (CA-06)",
            owner_piece="P08",
            dependency_reason="su payload y su productor los define una pieza futura",
            exit_rule="al cerrar la pieza duena se registra o se elimina",
            status=DEFERRED_STATUS,
        ),
    )
    _enqueue(db, _envelope(_DEFERRED_ET, "idem-def", {}))
    with pytest.raises(DeferredEventTypeError, match="P08"):
        publisher.drain_once()
    assert _unpublished(db) == 1


def test_market_candle_closed_publica_con_payload_real(
    db: Database, bus: RedisEventBus, publisher: OutboxPublisher
) -> None:
    # El hecho NUEVO de P07: market.candle_closed ya NO esta diferido. Tiene payload
    # real, y una vela valida sale al bus INTACTA por la misma via de outbox.
    payload = CandleClosedPayload(
        maturity_state=MaturityState.CLOSED,
        exchange="binance",
        market_type=MarketType.SPOT,
        symbol="BTC-USDT",
        timeframe=Timeframe.M1,
        open_time=_OPEN_TIME,
        close_time=_OPEN_TIME + 59_999,
        open=Decimal("100.00"),
        high=Decimal("110.00"),
        low=Decimal("95.00"),
        close=Decimal("105.00"),
        volume=Decimal("12.5"),
    )
    event_type = MarketCandleEventType.CANDLE_CLOSED.value
    body = payload.model_dump(mode="json")
    envelope = _envelope(
        event_type, payload.idempotency_key(MarketCandleEventType.CANDLE_CLOSED), body
    )
    # Los publicos NO llevan tenant (ADR-011): scope=public_market y sin tenant_id.
    envelope["scope"] = "public_market"
    envelope["stream_key"] = payload.stream_key()

    _enqueue(db, envelope)
    assert publisher.drain_once() == 1
    assert _unpublished(db) == 0
    got = _received_envelope(bus, event_type)
    assert got["payload"] == body
    assert got["stream_key"] == "market:candles:binance:spot:BTC-USDT:1m"


def test_h_version_incoherente_no_publica_ni_marca(
    db: Database, publisher: OutboxPublisher
) -> None:
    envelope = _envelope("policy.subject_invalidated", "idem-h", _POLICY_PAYLOAD)
    envelope["event_schema_version"] = 2  # el registro espera 1
    _enqueue(db, envelope)
    with pytest.raises(OutboxPublishError, match="event_schema_version"):
        publisher.drain_once()
    assert _unpublished(db) == 1


def test_j_component_por_outbox_sale_al_bus_intacto(
    db: Database, bus: RedisEventBus, publisher: OutboxPublisher
) -> None:
    # No hardcodeado a policy: OTRA familia real (component.*) por la misma via.
    _enqueue(db, _envelope("component.running", "idem-j", _COMPONENT_PAYLOAD))
    assert publisher.drain_once() == 1
    got = _received_envelope(bus, "component.running")
    assert got["payload"] == _COMPONENT_PAYLOAD
