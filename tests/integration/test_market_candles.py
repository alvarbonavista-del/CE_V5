"""Tests de integracion del historico de velas (ADR-013, ADR-007, regla 5.20).

Contra PostgreSQL REAL y con el rol de INGESTA. Lo que se prueba aqui NO lo puede
probar un doble en memoria:

- existing() decide DUPLICADO vs CORRECCION con una subconsulta sobre
  max(correction_revision). Una subconsulta mal escrita ahi falla EN SILENCIO y de la
  peor manera: o duplicariamos velas, o no detectariamos NUNCA una correccion.
- La ATOMICIDAD historico+outbox (ADR-013) la garantiza el MOTOR, no nuestro codigo:
  o estan las dos filas, o no esta ninguna.

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
from ce_v5.infra.db.market_candles import PostgresCandleWriter
from ce_v5.infra.db.outbox_publisher import OutboxPublisher, topic_for
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from source.envelope import Envelope
from source.envelope.enums import Scope
from source.families.market import (
    CandleClosedPayload,
    CandleCorrectedPayload,
    CandlePayload,
    MarketCandleEventType,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    Timeframe,
)
from source.families.registry import expected_event_schema_version
from source.time import MaturityState

_DSN = os.environ.get("CE_V5_DATABASE_URL")
_URL = os.environ.get("CE_V5_REDIS_URL")
pytestmark = pytest.mark.skipif(
    _DSN is None or _URL is None,
    reason="requiere CE_V5_DATABASE_URL y CE_V5_REDIS_URL",
)

_OPEN = 1_784_073_600_000
_CLOSE = _OPEN + 59_999
_EVENT_TIME = _OPEN + 42

_CLAVE = MarketStreamKey(
    exchange="binance",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.CANDLES,
    timeframe=Timeframe.M1,
)
_STREAM_KEY = _CLAVE.as_stream_key()


@pytest.fixture
def limpiar_market(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """Velas y outbox: sin FK a nadie, se acumularian entre ejecuciones."""

    def _wipe() -> None:
        with migrator_db.transaction() as session:
            session.execute("DELETE FROM market_candle")
            session.execute("DELETE FROM outbox")

    _wipe()
    yield
    _wipe()


def _cerrada(close: str = "105") -> CandleClosedPayload:
    return CandleClosedPayload(
        maturity_state=MaturityState.CLOSED,
        exchange="binance",
        market_type=MarketType.SPOT,
        symbol="BTC-USDT",
        timeframe=Timeframe.M1,
        open_time=_OPEN,
        close_time=_CLOSE,
        open=Decimal("100.12345678"),
        high=Decimal("110.5"),
        low=Decimal("95.25"),
        close=Decimal(close),
        volume=Decimal("12.5"),
    )


def _correccion(revision: int, corrige: str, close: str) -> CandleCorrectedPayload:
    return CandleCorrectedPayload(
        maturity_state=MaturityState.CORRECTION,
        corrects_idempotency_key=corrige,
        correction_revision=revision,
        exchange="binance",
        market_type=MarketType.SPOT,
        symbol="BTC-USDT",
        timeframe=Timeframe.M1,
        open_time=_OPEN,
        close_time=_CLOSE,
        open=Decimal("100.12345678"),
        high=Decimal("110.5"),
        low=Decimal("95.25"),
        close=Decimal(close),
        volume=Decimal("12.5"),
    )


def _envelope_de(payload: CandlePayload, event_type: MarketCandleEventType) -> bytes:
    """El sobre canonico, igual que lo construye el motor de ingesta (ADR-003/007)."""
    envelope = Envelope[CandlePayload](
        event_type=event_type.value,
        event_schema_version=expected_event_schema_version(event_type.value),
        source="market-ingestor",
        idempotency_key=payload.idempotency_key(event_type),
        stream_key=payload.stream_key(),
        scope=Scope.PUBLIC_MARKET,  # los publicos NO llevan tenant (ADR-011).
        event_time=_EVENT_TIME,  # lo fija el EXCHANGE (ADR-007).
        ingestion_time=_EVENT_TIME,
        processing_time=_EVENT_TIME,
        correlation_id=payload.stream_key(),
        payload=payload,
    )
    return envelope.model_dump_json().encode()


def _persistir(
    writer: PostgresCandleWriter,
    payload: CandlePayload,
    event_type: MarketCandleEventType,
) -> bool:
    return writer.persist_and_enqueue(
        envelope_json=_envelope_de(payload, event_type),
        payload=payload,
        event_type=event_type.value,
        stream_key=payload.stream_key(),
        idempotency_key=payload.idempotency_key(event_type),
    )


def _contar(db: PsycopgDatabase, sql: str, params: tuple[object, ...] = ()) -> int:
    with db.transaction() as session:
        row = session.fetchone(sql, params)
    assert row is not None
    valor = row[0]
    assert isinstance(valor, int)
    return valor


class TestIdaYVuelta:
    def test_una_cerrada_se_lee_con_sus_ohlcv_exactos(
        self, ingestion_db: PsycopgDatabase, limpiar_market: None
    ) -> None:
        writer = PostgresCandleWriter(ingestion_db)
        payload = _cerrada()

        assert _persistir(writer, payload, MarketCandleEventType.CANDLE_CLOSED) is True

        guardada = writer.existing(_STREAM_KEY, _OPEN)
        assert guardada is not None
        assert guardada.max_correction_revision == 0
        # Decimal SIN perder precision: en M5 esto es dinero.
        assert guardada.open == Decimal("100.12345678")
        assert guardada.close == Decimal("105")
        assert guardada.same_values_as(payload) is True
        assert guardada.idempotency_key == payload.idempotency_key(
            MarketCandleEventType.CANDLE_CLOSED
        )

    def test_no_hay_nada_para_una_ventana_sin_vela(
        self, ingestion_db: PsycopgDatabase, limpiar_market: None
    ) -> None:
        writer = PostgresCandleWriter(ingestion_db)
        assert writer.existing(_STREAM_KEY, _OPEN) is None


class TestDedup:
    def test_la_misma_vela_dos_veces_no_duplica_ni_reencola(
        self, ingestion_db: PsycopgDatabase, limpiar_market: None
    ) -> None:
        # El caso NORMAL tras una reconexion + bootstrap REST. Si se duplicara, el
        # historico tendria el mismo hecho dos veces; si se reencolara, el bus
        # publicaria dos veces la misma vela.
        writer = PostgresCandleWriter(ingestion_db)
        payload = _cerrada()

        assert _persistir(writer, payload, MarketCandleEventType.CANDLE_CLOSED) is True
        assert _persistir(writer, payload, MarketCandleEventType.CANDLE_CLOSED) is False

        assert _contar(ingestion_db, "SELECT count(*) FROM market_candle") == 1
        assert _contar(ingestion_db, "SELECT count(*) FROM outbox") == 1


class TestCorrecciones:
    def test_una_correccion_no_toca_el_original_y_sube_la_revision(
        self, ingestion_db: PsycopgDatabase, limpiar_market: None
    ) -> None:
        # APPEND-ONLY (ADR-007): la correccion es un hecho NUEVO que REFERENCIA al
        # corregido. El original NO se modifica: su verdad de entonces sigue ahi.
        writer = PostgresCandleWriter(ingestion_db)
        original = _cerrada(close="105")
        _persistir(writer, original, MarketCandleEventType.CANDLE_CLOSED)
        clave_original = original.idempotency_key(MarketCandleEventType.CANDLE_CLOSED)

        correccion = _correccion(1, clave_original, close="106")
        assert (
            _persistir(writer, correccion, MarketCandleEventType.CANDLE_CORRECTED)
            is True
        )

        guardada = writer.existing(_STREAM_KEY, _OPEN)
        assert guardada is not None
        # existing() sigue devolviendo el ORIGINAL (no la correccion)...
        assert guardada.idempotency_key == clave_original
        assert guardada.close == Decimal("105")  # INTACTO.
        # ...pero ya sabe que fue corregido una vez.
        assert guardada.max_correction_revision == 1
        assert _contar(ingestion_db, "SELECT count(*) FROM market_candle") == 2

    def test_una_segunda_correccion_llega_a_la_revision_2(
        self, ingestion_db: PsycopgDatabase, limpiar_market: None
    ) -> None:
        # LA SUBCONSULTA DE max(correction_revision) ES LO QUE SE PRUEBA AQUI. Si
        # estuviera mal, la segunda correccion naceria otra vez con revision 1,
        # compartiria idempotency_key con la primera y la outbox se la tragaria EN
        # SILENCIO. Un doble en memoria no puede cazar eso.
        writer = PostgresCandleWriter(ingestion_db)
        original = _cerrada(close="105")
        _persistir(writer, original, MarketCandleEventType.CANDLE_CLOSED)
        clave_original = original.idempotency_key(MarketCandleEventType.CANDLE_CLOSED)

        primera = _correccion(1, clave_original, close="106")
        _persistir(writer, primera, MarketCandleEventType.CANDLE_CORRECTED)
        segunda = _correccion(2, clave_original, close="107")
        assert (
            _persistir(writer, segunda, MarketCandleEventType.CANDLE_CORRECTED) is True
        )

        guardada = writer.existing(_STREAM_KEY, _OPEN)
        assert guardada is not None
        assert guardada.max_correction_revision == 2
        assert guardada.close == Decimal("105")  # el original, SIEMPRE intacto.

        # Las TRES filas conviven: el original y sus dos correcciones.
        assert _contar(ingestion_db, "SELECT count(*) FROM market_candle") == 3
        assert (
            _contar(
                ingestion_db,
                "SELECT count(*) FROM market_candle WHERE maturity_state = %s",
                ("correction",),
            )
            == 2
        )
        # Y son DOS hechos distintos: dos claves distintas.
        assert primera.idempotency_key(
            MarketCandleEventType.CANDLE_CORRECTED
        ) != segunda.idempotency_key(MarketCandleEventType.CANDLE_CORRECTED)


class TestAtomicidad:
    def test_historico_y_outbox_o_los_dos_o_ninguno(
        self, ingestion_db: PsycopgDatabase, limpiar_market: None
    ) -> None:
        # ADR-013 verificado contra el MOTOR, no contra un mock: tras un
        # persist_and_enqueue exitoso hay UNA fila en market_candle Y UNA en outbox,
        # con el MISMO idempotency_key. Es imposible que exista una vela que nadie
        # publico, o un evento publicado sin vela detras.
        writer = PostgresCandleWriter(ingestion_db)
        payload = _cerrada()
        clave = payload.idempotency_key(MarketCandleEventType.CANDLE_CLOSED)

        _persistir(writer, payload, MarketCandleEventType.CANDLE_CLOSED)

        assert (
            _contar(
                ingestion_db,
                "SELECT count(*) FROM market_candle WHERE idempotency_key = %s",
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


class TestElEventoEncoladoEsPublicable:
    def test_el_publisher_lo_valida_y_lo_saca_al_bus(
        self, ingestion_db: PsycopgDatabase, limpiar_market: None
    ) -> None:
        # END TO END: si el envelope que escribimos no cumpliera el registro
        # event_type -> payload (CA-06), el publisher lo RECHAZARIA y no llegaria al
        # bus. Que salga demuestra que el sobre es valido de verdad, no solo que
        # nuestro codigo cree que lo es.
        #
        # El publisher corre con el ROL DE INGESTA: sus policies de outbox lo acotan a
        # los tres market.* (regla 5.20), que es justo lo que tiene que drenar.
        assert _URL is not None
        config = RedisBusConfig(url=_URL, namespace="test-" + uuid.uuid4().hex)
        client: redis.Redis = create_client(config)
        try:
            bus = RedisEventBus(client, config)
            writer = PostgresCandleWriter(ingestion_db)
            payload = _cerrada()
            _persistir(writer, payload, MarketCandleEventType.CANDLE_CLOSED)

            publisher = OutboxPublisher(db=ingestion_db, bus=bus)
            assert publisher.drain_once() == 1

            topic = topic_for(MarketCandleEventType.CANDLE_CLOSED.value)
            bus.ensure_group(topic, "g1")
            recibidos = bus.poll(topic, "g1", "c1", max_messages=10, block_ms=0)
            assert len(recibidos) == 1

            envelope = json.loads(recibidos[0].message.envelope)
            assert envelope["event_type"] == "market.candle_closed"
            assert envelope["scope"] == "public_market"
            assert envelope["tenant_id"] is None
            assert envelope["event_time"] == _EVENT_TIME
            assert envelope["payload"]["close"] == "105"

            # Y la fila queda marcada como publicada: no se reenviara.
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
