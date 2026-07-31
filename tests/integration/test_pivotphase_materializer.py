"""Test de integracion del glue pivotphase_materializer (P08c P5 T4c-2b).

Contra PostgreSQL REAL: siembra un tramo continuo de footprints y candles y verifica el
replay end-to-end -- las DOS specs (PivotphasePhaseSpec/PivotphaseConfidenceSpec), el
snapshot escrito y la coherencia entre ambas -- leyendo con el rol de REGLAS, el mismo
que usa produccion (SOURCE_MATERIALIZERS, read_ohlcv_window, write_pivotphase_snapshot).

CUANTAS BARRAS SEMBRAR. El glue pide (history_bars + NORM_WINDOW) barras a cada fuente,
pero los materializadores vp.* (FootprintWindowedSpec, ventana rodante
PROFILE_WINDOW_BARS=100) leen ADEMAS (PROFILE_WINDOW_BARS - 1) footprints previos para
completar su propia ventana. Sembrar solo (history_bars + NORM_WINDOW) footprints
dejaria a vp.poc/vah/val/hvn/lvn con MENOS valores que las demas series (el precio via
read_ohlcv_window, footprint.price_range, orderflow.delta y delta_momentum), y
ReplaySeries -- que exige la MISMA longitud en sus 9 series -- reventaria con
ValueError antes de llegar a los asserts. _SEED_BARS es el numero exacto que hace que
las 9 series salgan alineadas: history_bars + NORM_WINDOW + PROFILE_WINDOW_BARS - 1.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from decimal import Decimal

import pytest

from ce_v5.entrypoints.worker_rules.materializers import PROFILE_WINDOW_BARS
from ce_v5.entrypoints.worker_rules.pivotphase_materializer import (
    NORM_WINDOW,
    PivotphaseConfidenceSpec,
    PivotphasePhaseSpec,
    params_version,
)
from ce_v5.infra.db.market_footprint import PostgresFootprintWriter
from ce_v5.infra.db.pivotphase_snapshot import read_pivotphase_snapshot_before
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.platform.rules.pivotphase import PivotParams
from ce_v5.platform.rules.pivotphase_confidence import default_params
from ce_v5.platform.rules.pivotphase_state_codec import parse_state
from source.envelope import Envelope
from source.envelope.enums import Scope
from source.families.footprint import (
    FootprintCell,
    FootprintClosedPayload,
    FootprintPayload,
    MarketFootprintEventType,
)
from source.families.market import (
    CandleClosedPayload,
    CandlePayload,
    MarketCandleEventType,
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

_EXCHANGE = "binance"
_SYMBOL = "BTC-USDT"
_TF = Timeframe.M1
_OPEN = 1_784_073_600_000
_EVENT_TIME = _OPEN + 42
_HISTORY_BARS = 3
_SEED_BARS = _HISTORY_BARS + NORM_WINDOW + PROFILE_WINDOW_BARS - 1

PersistirFootprint = Callable[[FootprintPayload, MarketFootprintEventType, int], bool]
PersistirVela = Callable[[CandlePayload, MarketCandleEventType, int], bool]


@pytest.fixture
def limpiar_pivotphase(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """footprint/candle/outbox/snapshot: sin FK a tenant, se limpian a mano (patron de
    limpiar_footprint/limpiar_velas/limpiar_cvd_snapshot).
    """

    def _wipe() -> None:
        with migrator_db.transaction() as session:
            session.execute("DELETE FROM market_footprint")
            session.execute("DELETE FROM market_candle")
            session.execute("DELETE FROM pivotphase_snapshot")
            session.execute("DELETE FROM outbox")

    _wipe()
    yield
    _wipe()


def _cells(indice: int) -> tuple[FootprintCell, ...]:
    # Precios FIJOS (100/101): las celdas deben ir en orden ascendente estricto (I-04
    # del contrato) a lo largo de las 200+ barras sembradas, asi que lo que varia barra
    # a barra es el volumen, no el precio.
    buy1 = Decimal("2") + Decimal(indice % 5)
    return (
        FootprintCell(
            price=Decimal("100"),
            buy_volume=buy1,
            sell_volume=Decimal("1"),
            delta=buy1 - Decimal("1"),
        ),
        FootprintCell(
            price=Decimal("101"),
            buy_volume=Decimal("5"),
            sell_volume=Decimal("3"),
            delta=Decimal("2"),
        ),
    )


def _footprint(open_time: int, indice: int) -> FootprintClosedPayload:
    cells = _cells(indice)
    buy = sum((c.buy_volume for c in cells), Decimal(0))
    sell = sum((c.sell_volume for c in cells), Decimal(0))
    return FootprintClosedPayload(
        maturity_state=MaturityState.CLOSED,
        exchange=_EXCHANGE,
        market_type=MarketType.SPOT,
        symbol=_SYMBOL,
        timeframe=_TF,
        open_time=open_time,
        close_time=open_time + _TF.duration_ms,
        cells=cells,
        bar_buy_volume=buy,
        bar_sell_volume=sell,
        bar_delta=buy - sell,
        trade_count=len(cells),
        is_complete=True,
    )


def _vela(open_time: int, indice: int) -> CandleClosedPayload:
    close = Decimal("100") + Decimal(indice)
    return CandleClosedPayload(
        maturity_state=MaturityState.CLOSED,
        exchange=_EXCHANGE,
        market_type=MarketType.SPOT,
        symbol=_SYMBOL,
        timeframe=_TF,
        open_time=open_time,
        close_time=open_time + _TF.duration_ms,
        open=close,
        high=close + Decimal("10"),
        low=close - Decimal("10"),
        close=close,
        volume=Decimal("12.5"),
    )


def _footprint_envelope(
    payload: FootprintPayload, event_type: MarketFootprintEventType, event_time: int
) -> bytes:
    envelope = Envelope[FootprintPayload](
        event_type=event_type.value,
        event_schema_version=expected_event_schema_version(event_type.value),
        source="test-pivotphase-materializer",
        idempotency_key=payload.idempotency_key(event_type),
        stream_key=payload.stream_key(),
        scope=Scope.PUBLIC_MARKET,
        event_time=event_time,
        ingestion_time=event_time,
        processing_time=event_time,
        correlation_id=payload.stream_key(),
        payload=payload,
    )
    return envelope.model_dump_json().encode()


@pytest.fixture
def persistir_footprint(ingestion_db: PsycopgDatabase) -> PersistirFootprint:
    """Escribe un footprint por el camino REAL (historico+outbox atomico, INGESTA).

    File-local (como en test_market_footprint.py): persistir_footprint NO vive en el
    conftest compartido (a diferencia de persistir_vela, que si).
    """
    writer = PostgresFootprintWriter(ingestion_db)

    def _persistir(
        payload: FootprintPayload, event_type: MarketFootprintEventType, event_time: int
    ) -> bool:
        return writer.persist_and_enqueue(
            envelope_json=_footprint_envelope(payload, event_type, event_time),
            payload=payload,
            event_type=event_type.value,
            stream_key=payload.stream_key(),
            idempotency_key=payload.idempotency_key(event_type),
        )

    return _persistir


def _sembrar(
    persistir_footprint: PersistirFootprint, persistir_vela: PersistirVela
) -> list[int]:
    """Siembra _SEED_BARS barras CONSECUTIVAS coherentes (footprint + vela) del
    flujo.
    """
    open_times: list[int] = []
    for indice in range(_SEED_BARS):
        open_time = _OPEN + indice * _TF.duration_ms
        assert (
            persistir_footprint(
                _footprint(open_time, indice),
                MarketFootprintEventType.FOOTPRINT_CLOSED,
                _EVENT_TIME,
            )
            is True
        )
        assert (
            persistir_vela(
                _vela(open_time, indice),
                MarketCandleEventType.CANDLE_CLOSED,
                _EVENT_TIME,
            )
            is True
        )
        open_times.append(open_time)
    return open_times


class TestReplayPivotphaseEndToEnd:
    """Propiedades del glue end-to-end (T1-T5): fase/confianza acotadas, determinismo,
    snapshot escrito y coherencia entre las dos specs y el estado final persistido.
    """

    def test_phase_confidence_snapshot_y_coherencia(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: PersistirFootprint,
        persistir_vela: PersistirVela,
        limpiar_pivotphase: None,
    ) -> None:
        open_times = _sembrar(persistir_footprint, persistir_vela)
        open_time = open_times[-1]

        # T1: la phase spec devuelve EXACTAMENTE history_bars fases, cada una en [0,5].
        with rules_db.transaction() as session:
            fases = PivotphasePhaseSpec().materialize(
                session, _EXCHANGE, _SYMBOL, _TF.value, open_time, _HISTORY_BARS
            )
        assert len(fases) == _HISTORY_BARS
        for fase in fases:
            assert Decimal(0) <= fase <= Decimal(5)

        # T2: la confidence spec devuelve EXACTAMENTE history_bars confianzas, 0-100.
        with rules_db.transaction() as session:
            confianzas = PivotphaseConfidenceSpec().materialize(
                session, _EXCHANGE, _SYMBOL, _TF.value, open_time, _HISTORY_BARS
            )
        assert len(confianzas) == _HISTORY_BARS
        for confianza in confianzas:
            assert Decimal(0) <= confianza <= Decimal(100)

        # T3: determinismo bit-exacto (ADR-007): misma llamada, misma tupla.
        with rules_db.transaction() as session:
            fases_repetidas = PivotphasePhaseSpec().materialize(
                session, _EXCHANGE, _SYMBOL, _TF.value, open_time, _HISTORY_BARS
            )
        assert fases_repetidas == fases

        # T4: el snapshot de la barra vigente quedo escrito (idempotente: las tres
        # llamadas de arriba comparten identidad y el ON CONFLICT DO NOTHING absorbe
        # las repeticiones -- doble replay tolerado).
        pv = params_version(PivotParams(), default_params(), NORM_WINDOW)
        with rules_db.transaction() as session:
            ancla = read_pivotphase_snapshot_before(
                session, _EXCHANGE, _SYMBOL, _TF.value, pv, open_time + 1
            )
        assert ancla is not None
        snapshot_open_time, state_text, _confidence = ancla
        assert snapshot_open_time == open_time

        # T5: la fase de la ULTIMA barra emitida coincide con la del estado final
        # decodificado del snapshot (el mismo `state` de la ultima iteracion del
        # replay alimenta tanto el ultimo BarOutcome como final_state).
        estado_final = parse_state(state_text)
        assert estado_final.phase == int(fases[-1])
