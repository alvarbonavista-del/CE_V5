"""Tests de integracion del historico de footprint (ADR-013, ADR-007, regla 5.20).

Contra PostgreSQL REAL y con el rol de INGESTA. Lo que se prueba aqui NO lo puede probar
un doble en memoria:

- La ATOMICIDAD historico+outbox (ADR-013) la garantiza el MOTOR, no nuestro codigo: o
  estan las dos filas (footprint y outbox con el MISMO idempotency_key), o ninguna.
- El DEDUP por idempotency_key (PK + UNIQUE de la outbox): reprocesar el mismo footprint
  no duplica ni reencola.
- is_complete viaja y vuelve (columna de la migracion 0019): una barra incompleta se
  persiste COMO incompleta.
- Las CELDAS (jsonb) conservan el Decimal EXACTO: el footprint es la suma de volumenes
  trade a trade; un float perderia digitos en silencio.
- END TO END: el evento encolado es PUBLICABLE -- el publisher lo valida contra el
  registro CA-06 (market.footprint_closed -> FootprintClosedPayload) y lo saca al bus
  con el rol de INGESTA (cuyas policies de outbox admiten los market.footprint_*, 0017).

Base de JUGUETE: nunca datos reales (DOC_ENTREGABLES sec.5).
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Iterator
from decimal import Decimal

import pytest
import redis

from ce_v5.infra.bus_redis import RedisBusConfig, RedisEventBus, create_client
from ce_v5.infra.db.market_footprint import PostgresFootprintWriter
from ce_v5.infra.db.outbox_publisher import OutboxPublisher, topic_for
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from source.envelope import Envelope
from source.envelope.enums import Scope
from source.families.footprint import (
    FootprintCell,
    FootprintClosedPayload,
    FootprintCorrectedPayload,
    FootprintPayload,
    MarketFootprintEventType,
)
from source.families.market import MarketType, Timeframe
from source.families.registry import expected_event_schema_version
from source.time import MaturityState

_DSN = os.environ.get("CE_V5_DATABASE_URL")
_URL = os.environ.get("CE_V5_REDIS_URL")
pytestmark = pytest.mark.skipif(
    _DSN is None or _URL is None,
    reason="requiere CE_V5_DATABASE_URL y CE_V5_REDIS_URL",
)

_TF = Timeframe.M1
_OPEN = 1_784_073_600_000
_CLOSE = _OPEN + _TF.duration_ms
_EVENT_TIME = _OPEN + 42

Persistir = Callable[[FootprintPayload, MarketFootprintEventType, int], bool]


@pytest.fixture
def limpiar_footprint(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """Footprint y outbox: sin FK a nadie, se acumularian entre ejecuciones."""

    def _wipe() -> None:
        with migrator_db.transaction() as session:
            session.execute("DELETE FROM market_footprint")
            session.execute("DELETE FROM outbox")

    _wipe()
    yield
    _wipe()


def _cells() -> tuple[FootprintCell, ...]:
    # Dos niveles de precio con Decimal de muchos digitos: la prueba de que el jsonb no
    # los redondea. Ordenadas por precio ascendente (lo exige el contrato).
    return (
        FootprintCell(
            price=Decimal("100.12345678"),
            buy_volume=Decimal("1.5"),
            sell_volume=Decimal("0.25"),
            delta=Decimal("1.25"),
        ),
        FootprintCell(
            price=Decimal("100.99999999"),
            buy_volume=Decimal("0"),
            sell_volume=Decimal("3.0"),
            delta=Decimal("-3.0"),
        ),
    )


def _closed(is_complete: bool = True) -> FootprintClosedPayload:  # noqa: FBT001, FBT002
    cells = _cells()
    buy = sum((c.buy_volume for c in cells), Decimal(0))
    sell = sum((c.sell_volume for c in cells), Decimal(0))
    return FootprintClosedPayload(
        maturity_state=MaturityState.CLOSED,
        exchange="binance",
        market_type=MarketType.SPOT,
        symbol="BTC-USDT",
        timeframe=_TF,
        open_time=_OPEN,
        close_time=_CLOSE,
        cells=cells,
        bar_buy_volume=buy,
        bar_sell_volume=sell,
        bar_delta=buy - sell,
        trade_count=3,
        is_complete=is_complete,
    )


def _corrected(revision: int, corrige: str) -> FootprintCorrectedPayload:
    cells = _cells()
    buy = sum((c.buy_volume for c in cells), Decimal(0))
    sell = sum((c.sell_volume for c in cells), Decimal(0))
    return FootprintCorrectedPayload(
        maturity_state=MaturityState.CORRECTION,
        corrects_idempotency_key=corrige,
        correction_revision=revision,
        exchange="binance",
        market_type=MarketType.SPOT,
        symbol="BTC-USDT",
        timeframe=_TF,
        open_time=_OPEN,
        close_time=_CLOSE,
        cells=cells,
        bar_buy_volume=buy,
        bar_sell_volume=sell,
        bar_delta=buy - sell,
        trade_count=3,
        is_complete=True,
    )


def _envelope_de(
    payload: FootprintPayload, event_type: MarketFootprintEventType, event_time: int
) -> bytes:
    """El sobre canonico del footprint, como lo construye el motor (ADR-003/007)."""
    envelope = Envelope[FootprintPayload](
        event_type=event_type.value,
        event_schema_version=expected_event_schema_version(event_type.value),
        source="worker_footprint",
        idempotency_key=payload.idempotency_key(event_type),
        stream_key=payload.stream_key(),
        scope=Scope.PUBLIC_MARKET,  # los publicos NO llevan tenant (ADR-011).
        event_time=event_time,
        ingestion_time=event_time,
        processing_time=event_time,
        correlation_id=payload.stream_key(),
        payload=payload,
    )
    return envelope.model_dump_json().encode()


@pytest.fixture
def persistir_footprint(ingestion_db: PsycopgDatabase) -> Persistir:
    """Escribe un footprint por el camino REAL: historico+outbox atomico (INGESTA)."""
    writer = PostgresFootprintWriter(ingestion_db)

    def _persistir(
        payload: FootprintPayload, event_type: MarketFootprintEventType, event_time: int
    ) -> bool:
        return writer.persist_and_enqueue(
            envelope_json=_envelope_de(payload, event_type, event_time),
            payload=payload,
            event_type=event_type.value,
            stream_key=payload.stream_key(),
            idempotency_key=payload.idempotency_key(event_type),
        )

    return _persistir


def _contar(db: PsycopgDatabase, sql: str, params: tuple[object, ...] = ()) -> int:
    with db.transaction() as session:
        row = session.fetchone(sql, params)
    assert row is not None
    valor = row[0]
    assert isinstance(valor, int)
    return valor


class TestAtomicidad:
    def test_historico_y_outbox_o_los_dos_o_ninguno(
        self,
        ingestion_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # ADR-013 contra el MOTOR: tras un persist_and_enqueue con exito hay UNA fila en
        # market_footprint Y UNA en outbox, con el MISMO idempotency_key.
        payload = _closed()
        clave = payload.idempotency_key(MarketFootprintEventType.FOOTPRINT_CLOSED)

        assert (
            persistir_footprint(
                payload, MarketFootprintEventType.FOOTPRINT_CLOSED, _EVENT_TIME
            )
            is True
        )

        assert (
            _contar(
                ingestion_db,
                "SELECT count(*) FROM market_footprint WHERE idempotency_key = %s",
                (clave,),
            )
            == 1
        )
        assert (
            _contar(
                ingestion_db,
                "SELECT count(*) FROM outbox WHERE idempotency_key = %s",
                (clave,),
            )
            == 1
        )


class TestDedup:
    def test_el_mismo_footprint_dos_veces_no_duplica_ni_reencola(
        self,
        ingestion_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # Reprocesar el mismo candle_closed reconstruye la MISMA clave: idempotente.
        payload = _closed()

        assert (
            persistir_footprint(
                payload, MarketFootprintEventType.FOOTPRINT_CLOSED, _EVENT_TIME
            )
            is True
        )
        assert (
            persistir_footprint(
                payload, MarketFootprintEventType.FOOTPRINT_CLOSED, _EVENT_TIME
            )
            is False
        )

        assert _contar(ingestion_db, "SELECT count(*) FROM market_footprint") == 1
        assert _contar(ingestion_db, "SELECT count(*) FROM outbox") == 1


class TestIsCompleteViajaYVuelve:
    def test_una_barra_incompleta_se_persiste_como_incompleta(
        self,
        ingestion_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # La columna de la 0019: is_complete=False se guarda tal cual, sin perderse por
        # el DEFAULT. Una barra incompleta se persiste Y SE VE (0018 lo anticipo).
        payload = _closed(is_complete=False)
        clave = payload.idempotency_key(MarketFootprintEventType.FOOTPRINT_CLOSED)
        persistir_footprint(
            payload, MarketFootprintEventType.FOOTPRINT_CLOSED, _EVENT_TIME
        )

        with ingestion_db.transaction() as session:
            row = session.fetchone(
                "SELECT is_complete FROM market_footprint WHERE idempotency_key = %s",
                (clave,),
            )
        assert row is not None
        assert row[0] is False


class TestCeldasExactas:
    def test_el_jsonb_conserva_el_decimal_sin_redondear(
        self,
        ingestion_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # El Decimal viaja EN TEXTO dentro del jsonb: 100.12345678 vuelve intacto. Un
        # float binario lo habria corrompido, y el footprint mentiria al sumar trades.
        payload = _closed()
        clave = payload.idempotency_key(MarketFootprintEventType.FOOTPRINT_CLOSED)
        persistir_footprint(
            payload, MarketFootprintEventType.FOOTPRINT_CLOSED, _EVENT_TIME
        )

        with ingestion_db.transaction() as session:
            row = session.fetchone(
                "SELECT cells FROM market_footprint WHERE idempotency_key = %s",
                (clave,),
            )
        assert row is not None
        raw = row[0]
        cells = raw if isinstance(raw, list) else json.loads(str(raw))
        assert cells[0]["price"] == "100.12345678"
        assert cells[1]["price"] == "100.99999999"
        assert cells[0]["delta"] == "1.25"


class TestCorreccionAppendOnly:
    def test_una_correccion_convive_con_el_cerrado(
        self,
        ingestion_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # APPEND-ONLY (ADR-007): la correccion es un hecho NUEVO que apunta al cerrado
        # de la misma barra. Los dos conviven, con claves distintas.
        cerrado = _closed()
        clave_cerrado = cerrado.idempotency_key(
            MarketFootprintEventType.FOOTPRINT_CLOSED
        )
        persistir_footprint(
            cerrado, MarketFootprintEventType.FOOTPRINT_CLOSED, _EVENT_TIME
        )

        correccion = _corrected(1, clave_cerrado)
        assert (
            persistir_footprint(
                correccion, MarketFootprintEventType.FOOTPRINT_CORRECTED, _EVENT_TIME
            )
            is True
        )

        assert _contar(ingestion_db, "SELECT count(*) FROM market_footprint") == 2
        assert (
            _contar(
                ingestion_db,
                "SELECT count(*) FROM market_footprint WHERE maturity_state = %s",
                ("correction",),
            )
            == 1
        )


class TestElEventoEncoladoEsPublicable:
    def test_el_publisher_lo_valida_y_lo_saca_al_bus(
        self,
        ingestion_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # END TO END: si el envelope no cumpliera el registro event_type -> payload
        # (CA-06), el publisher lo RECHAZARIA. Que salga demuestra que el sobre del
        # footprint es valido de verdad. El publisher corre con el ROL DE INGESTA, cuyas
        # policies de outbox admiten los market.footprint_* (0017).
        assert _URL is not None
        config = RedisBusConfig(url=_URL, namespace="test-" + uuid.uuid4().hex)
        client: redis.Redis = create_client(config)
        try:
            bus = RedisEventBus(client, config)
            payload = _closed()
            persistir_footprint(
                payload, MarketFootprintEventType.FOOTPRINT_CLOSED, _EVENT_TIME
            )

            publisher = OutboxPublisher(db=ingestion_db, bus=bus)
            assert publisher.drain_once() == 1

            topic = topic_for(MarketFootprintEventType.FOOTPRINT_CLOSED.value)
            bus.ensure_group(topic, "g1")
            recibidos = bus.poll(topic, "g1", "c1", max_messages=10, block_ms=0)
            assert len(recibidos) == 1

            envelope = json.loads(recibidos[0].message.envelope)
            assert envelope["event_type"] == "market.footprint_closed"
            assert envelope["scope"] == "public_market"
            assert envelope["tenant_id"] is None
            assert envelope["event_time"] == _EVENT_TIME
            assert envelope["payload"]["is_complete"] is True
            assert envelope["payload"]["cells"][0]["price"] == "100.12345678"

            assert (
                _contar(
                    ingestion_db,
                    "SELECT count(*) FROM outbox WHERE published_at IS NULL",
                )
                == 0
            )
        finally:
            for key in client.scan_iter(match=f"{config.namespace}:*"):
                client.delete(key)
            client.close()
