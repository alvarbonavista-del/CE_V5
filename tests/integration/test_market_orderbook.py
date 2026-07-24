"""Tests de integracion de la persistencia de orderbook (P07c; ADR-013, ADR-006, 5.20).

Contra PostgreSQL REAL y con el rol de INGESTA. Lo que se prueba aqui NO lo hace un
doble en memoria: lo hace el MOTOR.

- ATOMICIDAD (ADR-013): el frontier (o el resync) y su outbox, o los dos o ninguno. Se
  demuestra con la propiedad y con el ROLLBACK real: un event_type prohibido por la
  policy hace que PostgreSQL aborte y el snapshot NO se quede a medias.
- SAMPLE SIN OUTBOX: como los trades, la muestra intra-ventana se persiste y NO se
  encola.
- DEDUP por idempotency_key (PK / UNIQUE): reprocesar no duplica ni reencola.
- is_complete viaja y vuelve (cond.3): una foto incompleta se persiste COMO incompleta.
- Los NIVELES (jsonb) conservan el Decimal EXACTO.
- FRONTERA 5.20 (negativos bidireccionales, leidos por el motor): la API no escribe el
  libro, el ingestor no encola familias ajenas, y el historico es append-only para
  TODOS.
- END TO END: el frontier encolado es PUBLICABLE (el publisher lo valida contra CA-06).

Base de JUGUETE: nunca datos reales (DOC_ENTREGABLES sec.5).
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from decimal import Decimal

import pytest
import redis

from ce_v5.infra.bus_redis import RedisBusConfig, RedisEventBus, create_client
from ce_v5.infra.db.market_orderbook import PostgresOrderbookWriter
from ce_v5.infra.db.outbox_publisher import OutboxPublisher, topic_for
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from source.envelope import Envelope
from source.envelope.enums import Scope
from source.families.market import MarketType, Timeframe
from source.families.orderbook import (
    MarketOrderbookEventType,
    MarketOrderbookSnapshotKind,
    OrderbookLevel,
    OrderbookResyncedPayload,
    OrderbookSnapshotPayload,
)
from source.families.registry import expected_event_schema_version

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


def _levels(pairs: list[tuple[str, str]]) -> tuple[OrderbookLevel, ...]:
    return tuple(OrderbookLevel(price=Decimal(p), size=Decimal(s)) for p, s in pairs)


def _frontier(is_complete: bool = True) -> OrderbookSnapshotPayload:  # noqa: FBT001, FBT002
    return OrderbookSnapshotPayload(
        exchange="binance",
        market_type=MarketType.SPOT,
        symbol="BTC-USDT",
        depth_k=25,
        bids=_levels([("100.12345678", "2.5"), ("100.00000001", "1")]),
        asks=_levels([("100.99999999", "1.5"), ("101.5", "3")]),
        sequence=987654,
        kind=MarketOrderbookSnapshotKind.FRONTIER,
        timeframe=_TF,
        open_time=_OPEN,
        close_time=_CLOSE,
        cadence_ms=1000,
        formula_version=1,
        is_complete=is_complete,
    )


def _sample() -> OrderbookSnapshotPayload:
    return OrderbookSnapshotPayload(
        exchange="binance",
        market_type=MarketType.SPOT,
        symbol="BTC-USDT",
        depth_k=25,
        bids=_levels([("100.5", "2")]),
        asks=_levels([("100.6", "1")]),
        sequence=987655,
        kind=MarketOrderbookSnapshotKind.SAMPLE,
        timeframe=_TF,
        open_time=_OPEN,
        close_time=_CLOSE,
        sample_time=_OPEN + 30_000,
        cadence_ms=1000,
        formula_version=1,
        is_complete=True,
    )


def _resync() -> OrderbookResyncedPayload:
    return OrderbookResyncedPayload(
        exchange="binance",
        market_type=MarketType.SPOT,
        symbol="BTC-USDT",
        from_sequence=500,
        to_sequence=540,
        reason="gap",
        event_time=_EVENT_TIME,
    )


def _envelope_frontier(payload: OrderbookSnapshotPayload) -> bytes:
    event = MarketOrderbookEventType.ORDERBOOK_FRONTIER
    envelope = Envelope[OrderbookSnapshotPayload](
        event_type=event.value,
        event_schema_version=expected_event_schema_version(event.value),
        source="worker_orderbook",
        idempotency_key=payload.idempotency_key(payload.kind),
        stream_key=payload.stream_key(),
        scope=Scope.PUBLIC_MARKET,
        event_time=_EVENT_TIME,
        ingestion_time=_EVENT_TIME,
        processing_time=_EVENT_TIME,
        correlation_id=payload.stream_key(),
        payload=payload,
    )
    return envelope.model_dump_json().encode()


def _envelope_resync(payload: OrderbookResyncedPayload) -> bytes:
    event = MarketOrderbookEventType.ORDERBOOK_RESYNCED
    envelope = Envelope[OrderbookResyncedPayload](
        event_type=event.value,
        event_schema_version=expected_event_schema_version(event.value),
        source="worker_orderbook",
        idempotency_key=payload.idempotency_key(),
        stream_key=payload.stream_key(),
        scope=Scope.PUBLIC_MARKET,
        event_time=payload.event_time,
        ingestion_time=payload.event_time,
        processing_time=payload.event_time,
        correlation_id=payload.stream_key(),
        payload=payload,
    )
    return envelope.model_dump_json().encode()


@pytest.fixture
def limpiar_orderbook(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """Snapshot, discontinuidad y outbox: sin FK, se acumularian entre corridas."""

    def _wipe() -> None:
        with migrator_db.transaction() as session:
            session.execute("DELETE FROM market_orderbook_snapshot")
            session.execute("DELETE FROM market_orderbook_discontinuity")
            session.execute("DELETE FROM outbox")

    _wipe()
    yield
    _wipe()


@pytest.fixture
def writer(ingestion_db: PsycopgDatabase) -> PostgresOrderbookWriter:
    return PostgresOrderbookWriter(ingestion_db)


def _contar(db: PsycopgDatabase, sql: str, params: tuple[object, ...] = ()) -> int:
    with db.transaction() as session:
        row = session.fetchone(sql, params)
    assert row is not None
    valor = row[0]
    assert isinstance(valor, int)
    return valor


class TestAtomicidadFrontier:
    def test_snapshot_y_outbox_o_los_dos_o_ninguno(
        self,
        ingestion_db: PsycopgDatabase,
        writer: PostgresOrderbookWriter,
        limpiar_orderbook: None,
    ) -> None:
        payload = _frontier()
        clave = payload.idempotency_key(payload.kind)
        assert (
            writer.persist_and_enqueue(
                envelope_json=_envelope_frontier(payload),
                payload=payload,
                event_type=MarketOrderbookEventType.ORDERBOOK_FRONTIER.value,
                stream_key=payload.stream_key(),
                idempotency_key=clave,
                event_time=_EVENT_TIME,
            )
            is True
        )
        assert (
            _contar(
                ingestion_db,
                "SELECT count(*) FROM market_orderbook_snapshot "
                "WHERE idempotency_key = %s",
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


class TestAtomicidadResync:
    def test_discontinuidad_y_outbox_atomicos(
        self,
        ingestion_db: PsycopgDatabase,
        writer: PostgresOrderbookWriter,
        limpiar_orderbook: None,
    ) -> None:
        payload = _resync()
        clave = payload.idempotency_key()
        assert (
            writer.persist_and_enqueue(
                envelope_json=_envelope_resync(payload),
                payload=payload,
                event_type=MarketOrderbookEventType.ORDERBOOK_RESYNCED.value,
                stream_key=payload.stream_key(),
                idempotency_key=clave,
                event_time=payload.event_time,
            )
            is True
        )
        assert (
            _contar(
                ingestion_db,
                "SELECT count(*) FROM market_orderbook_discontinuity "
                "WHERE from_sequence = %s AND to_sequence = %s",
                (payload.from_sequence, payload.to_sequence),
            )
            == 1
        )
        assert (
            _contar(
                ingestion_db,
                "SELECT count(*) FROM outbox WHERE idempotency_key = %s AND "
                "event_type = %s",
                (clave, MarketOrderbookEventType.ORDERBOOK_RESYNCED.value),
            )
            == 1
        )

    def test_el_mismo_hueco_no_duplica_ni_reencola(
        self,
        ingestion_db: PsycopgDatabase,
        writer: PostgresOrderbookWriter,
        limpiar_orderbook: None,
    ) -> None:
        payload = _resync()
        clave = payload.idempotency_key()
        for esperado in (True, False):
            assert (
                writer.persist_and_enqueue(
                    envelope_json=_envelope_resync(payload),
                    payload=payload,
                    event_type=MarketOrderbookEventType.ORDERBOOK_RESYNCED.value,
                    stream_key=payload.stream_key(),
                    idempotency_key=clave,
                    event_time=payload.event_time,
                )
                is esperado
            )
        assert (
            _contar(ingestion_db, "SELECT count(*) FROM market_orderbook_discontinuity")
            == 1
        )
        assert _contar(ingestion_db, "SELECT count(*) FROM outbox") == 1


class TestRollbackOutbox:
    def test_si_el_outbox_falla_el_snapshot_hace_rollback(
        self,
        ingestion_db: PsycopgDatabase,
        writer: PostgresOrderbookWriter,
        limpiar_orderbook: None,
    ) -> None:
        # ADR-013 contra el MOTOR: un event_type PROHIBIDO por la policy de outbox
        # (execution.*, rechazado por el WITH CHECK de 0020) aborta la transaccion. Si
        # no hubiera atomicidad, el snapshot ya estaria persistido y el evento no
        # existiria.
        payload = _frontier()
        clave = payload.idempotency_key(payload.kind)
        with pytest.raises(Exception) as excinfo:
            writer.persist_and_enqueue(
                envelope_json=b"{}",
                payload=payload,
                event_type="execution.order_placed",  # ajeno: la policy lo rechaza.
                stream_key=payload.stream_key(),
                idempotency_key=clave,
                event_time=_EVENT_TIME,
            )
        assert "row-level security" in str(excinfo.value).lower()
        # ROLLBACK: NI el snapshot NI el evento quedaron. El motor no dejo un hecho a
        # medias del que nadie se enterase.
        assert (
            _contar(ingestion_db, "SELECT count(*) FROM market_orderbook_snapshot") == 0
        )
        assert _contar(ingestion_db, "SELECT count(*) FROM outbox") == 0


class TestSampleSinOutbox:
    def test_la_muestra_se_persiste_y_no_se_encola(
        self,
        ingestion_db: PsycopgDatabase,
        writer: PostgresOrderbookWriter,
        limpiar_orderbook: None,
    ) -> None:
        payload = _sample()
        assert writer.persist_sample(payload, _EVENT_TIME) is True
        assert (
            _contar(
                ingestion_db,
                "SELECT count(*) FROM market_orderbook_snapshot WHERE kind = 'sample'",
            )
            == 1
        )
        # SIN OUTBOX: la muestra no se publica (como los trades). Cero filas en outbox.
        assert _contar(ingestion_db, "SELECT count(*) FROM outbox") == 0

    def test_la_misma_muestra_dos_veces_no_duplica(
        self,
        ingestion_db: PsycopgDatabase,
        writer: PostgresOrderbookWriter,
        limpiar_orderbook: None,
    ) -> None:
        payload = _sample()
        assert writer.persist_sample(payload, _EVENT_TIME) is True
        assert writer.persist_sample(payload, _EVENT_TIME) is False
        assert (
            _contar(ingestion_db, "SELECT count(*) FROM market_orderbook_snapshot") == 1
        )


class TestDedupFrontier:
    def test_el_mismo_frontier_dos_veces_no_duplica_ni_reencola(
        self,
        ingestion_db: PsycopgDatabase,
        writer: PostgresOrderbookWriter,
        limpiar_orderbook: None,
    ) -> None:
        payload = _frontier()
        clave = payload.idempotency_key(payload.kind)
        for esperado in (True, False):
            assert (
                writer.persist_and_enqueue(
                    envelope_json=_envelope_frontier(payload),
                    payload=payload,
                    event_type=MarketOrderbookEventType.ORDERBOOK_FRONTIER.value,
                    stream_key=payload.stream_key(),
                    idempotency_key=clave,
                    event_time=_EVENT_TIME,
                )
                is esperado
            )
        assert (
            _contar(ingestion_db, "SELECT count(*) FROM market_orderbook_snapshot") == 1
        )
        assert _contar(ingestion_db, "SELECT count(*) FROM outbox") == 1


class TestIsCompleteViajaYVuelve:
    def test_una_foto_incompleta_se_persiste_como_incompleta(
        self,
        ingestion_db: PsycopgDatabase,
        writer: PostgresOrderbookWriter,
        limpiar_orderbook: None,
    ) -> None:
        payload = _frontier(is_complete=False)
        clave = payload.idempotency_key(payload.kind)
        writer.persist_and_enqueue(
            envelope_json=_envelope_frontier(payload),
            payload=payload,
            event_type=MarketOrderbookEventType.ORDERBOOK_FRONTIER.value,
            stream_key=payload.stream_key(),
            idempotency_key=clave,
            event_time=_EVENT_TIME,
        )
        with ingestion_db.transaction() as session:
            row = session.fetchone(
                "SELECT is_complete FROM market_orderbook_snapshot "
                "WHERE idempotency_key = %s",
                (clave,),
            )
        assert row is not None
        assert row[0] is False


class TestNivelesExactos:
    def test_el_jsonb_conserva_el_decimal_sin_redondear(
        self,
        ingestion_db: PsycopgDatabase,
        writer: PostgresOrderbookWriter,
        limpiar_orderbook: None,
    ) -> None:
        payload = _frontier()
        clave = payload.idempotency_key(payload.kind)
        writer.persist_and_enqueue(
            envelope_json=_envelope_frontier(payload),
            payload=payload,
            event_type=MarketOrderbookEventType.ORDERBOOK_FRONTIER.value,
            stream_key=payload.stream_key(),
            idempotency_key=clave,
            event_time=_EVENT_TIME,
        )
        with ingestion_db.transaction() as session:
            row = session.fetchone(
                "SELECT bids, asks FROM market_orderbook_snapshot "
                "WHERE idempotency_key = %s",
                (clave,),
            )
        assert row is not None
        bids = row[0] if isinstance(row[0], list) else json.loads(str(row[0]))
        asks = row[1] if isinstance(row[1], list) else json.loads(str(row[1]))
        assert bids[0]["price"] == "100.12345678"
        assert bids[1]["price"] == "100.00000001"
        assert asks[0]["price"] == "100.99999999"


class TestFrontera520:
    @pytest.mark.parametrize(
        "tabla", ["market_orderbook_snapshot", "market_orderbook_discontinuity"]
    )
    def test_la_api_no_puede_escribir_el_libro(
        self, app_db: PsycopgDatabase, tabla: str, limpiar_orderbook: None
    ) -> None:
        # Mitad (a): la API esta expuesta a internet; si pudiera fabricar un snapshot
        # del libro, alimentaria reglas de orderflow -> senales -> en M5, ordenes. Solo
        # LEE.
        with pytest.raises(Exception) as excinfo:
            with app_db.transaction() as session:
                session.execute(f"INSERT INTO {tabla} (exchange) VALUES ('binance')")  # noqa: S608
        assert "permission denied" in str(excinfo.value).lower()

    @pytest.mark.parametrize(
        "tabla", ["market_orderbook_snapshot", "market_orderbook_discontinuity"]
    )
    @pytest.mark.parametrize("operacion", ["UPDATE", "DELETE"])
    def test_el_ingestor_no_reescribe_el_libro(
        self,
        ingestion_db: PsycopgDatabase,
        tabla: str,
        operacion: str,
        limpiar_orderbook: None,
    ) -> None:
        # Append-only real, tambien para QUIEN lo escribe: un resync borrado seria un
        # hueco del que nadie se entera.
        sentencia = (
            f"UPDATE {tabla} SET exchange = 'x' WHERE exchange = 'y'"  # noqa: S608
            if operacion == "UPDATE"
            else f"DELETE FROM {tabla} WHERE exchange = 'y'"  # noqa: S608
        )
        with pytest.raises(Exception) as excinfo:
            with ingestion_db.transaction() as session:
                session.execute(sentencia)
        assert "permission denied" in str(excinfo.value).lower()

    @pytest.mark.parametrize(
        "event_type", ["market.orderbook_frontier", "market.orderbook_resynced"]
    )
    def test_el_ingestor_puede_encolar_los_dos_orderbook(
        self, ingestion_db: PsycopgDatabase, event_type: str, limpiar_orderbook: None
    ) -> None:
        # La 0020 RECREA las policies con los SIETE market.*. Sin esto, el ingestor
        # podria persistir el frontier y no poder publicarlo (WITH CHECK lo rechazaria).
        with ingestion_db.transaction() as session:
            session.execute(
                "INSERT INTO outbox (event_id, idempotency_key, stream_key, "
                "event_type, envelope) VALUES (%s, %s, %s, %s, '{}')",
                (
                    str(uuid.uuid4()),
                    f"idem-{uuid.uuid4().hex}",
                    "market:orderbook:binance:spot:BTC-USDT",
                    event_type,
                ),
            )
            row = session.fetchone(
                "SELECT count(*) FROM outbox WHERE event_type = %s", (event_type,)
            )
        assert row is not None
        valor = row[0]
        assert isinstance(valor, int) and valor == 1

    def test_el_ingestor_no_puede_encolar_una_familia_ajena(
        self, ingestion_db: PsycopgDatabase, limpiar_orderbook: None
    ) -> None:
        with pytest.raises(Exception) as excinfo:
            with ingestion_db.transaction() as session:
                session.execute(
                    "INSERT INTO outbox (event_id, idempotency_key, stream_key, "
                    "event_type, envelope) VALUES (%s, %s, %s, %s, '{}')",
                    (
                        str(uuid.uuid4()),
                        f"idem-{uuid.uuid4().hex}",
                        "execution:stream",
                        "execution.order_placed",
                    ),
                )
        assert "row-level security" in str(excinfo.value).lower()


class TestElFrontierEncoladoEsPublicable:
    def test_el_publisher_lo_valida_y_lo_saca_al_bus(
        self,
        ingestion_db: PsycopgDatabase,
        writer: PostgresOrderbookWriter,
        limpiar_orderbook: None,
    ) -> None:
        # END TO END: si el envelope no cumpliera CA-06 (market.orderbook_frontier ->
        # OrderbookSnapshotPayload), el publisher lo RECHAZARIA. Que salga demuestra que
        # el sobre del frontier es valido de verdad, con el rol de INGESTA.
        assert _URL is not None
        config = RedisBusConfig(url=_URL, namespace="test-" + uuid.uuid4().hex)
        client: redis.Redis = create_client(config)
        try:
            bus = RedisEventBus(client, config)
            payload = _frontier()
            writer.persist_and_enqueue(
                envelope_json=_envelope_frontier(payload),
                payload=payload,
                event_type=MarketOrderbookEventType.ORDERBOOK_FRONTIER.value,
                stream_key=payload.stream_key(),
                idempotency_key=payload.idempotency_key(payload.kind),
                event_time=_EVENT_TIME,
            )
            publisher = OutboxPublisher(db=ingestion_db, bus=bus)
            assert publisher.drain_once() == 1

            topic = topic_for(MarketOrderbookEventType.ORDERBOOK_FRONTIER.value)
            bus.ensure_group(topic, "g1")
            recibidos = bus.poll(topic, "g1", "c1", max_messages=10, block_ms=0)
            assert len(recibidos) == 1

            envelope = json.loads(recibidos[0].message.envelope)
            assert envelope["event_type"] == "market.orderbook_frontier"
            assert envelope["scope"] == "public_market"
            assert envelope["tenant_id"] is None
            assert envelope["payload"]["kind"] == "frontier"
            assert envelope["payload"]["bids"][0]["price"] == "100.12345678"
        finally:
            for key in client.scan_iter(match=f"{config.namespace}:*"):
                client.delete(key)
            client.close()
