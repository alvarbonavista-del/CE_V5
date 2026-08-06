"""Materializador de pivotphase: glue de BD + specs phase/confidence (P08c P5 T4c-2b).

Estrategia A (DICTAMEN P08c-PIVOT-09): cada materialize lee (history_bars + NORM_WINDOW)
barras por CUENTA -- el precio raw via read_ohlcv_window (fuente raw, fuera del
PIVOT-08 Q1), el resto via SOURCE_MATERIALIZERS --, bootstrapea el FSM desde IDLE sobre
toda la ventana (las primeras NORM_WINDOW barras avanzan la FSM y ceban las
sin emitirse) y emite las ultimas history_bars. Como las secuencias del FSM son bounded
(< NORM_WINDOW), el bootstrap reconstruye el estado entero; el snapshot es AS-OF
(se escribe la barra vigente), no continuidad. Doble replay tolerado (dos specs, mismo
replay determinista; ADR-007). Solo Decimal.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from ce_v5.infra.db.market_candles import read_ohlcv_window
from ce_v5.infra.db.pivotphase_snapshot import write_pivotphase_snapshot
from ce_v5.platform.rules.absorption import (
    ABSORPTION_ASK_STRENGTH_SOURCE_ID,
    ABSORPTION_BID_STRENGTH_SOURCE_ID,
)
from ce_v5.platform.rules.climax import (
    CLIMAX_BOTTOM_STRENGTH_SOURCE_ID,
    CLIMAX_TOP_STRENGTH_SOURCE_ID,
)
from ce_v5.platform.rules.cvd import CVD_SOURCE_ID
from ce_v5.platform.rules.footprint_range import FOOTPRINT_PRICE_RANGE_SOURCE_ID
from ce_v5.platform.rules.notrade import NOTRADE_SCORE_SOURCE_ID
from ce_v5.platform.rules.orderflow import (
    ORDERFLOW_DELTA_MOMENTUM_SOURCE_ID,
    ORDERFLOW_DELTA_SOURCE_ID,
)
from ce_v5.platform.rules.pivotphase import PivotParams, PivotState
from ce_v5.platform.rules.pivotphase_confidence import ConfidenceParams, default_params
from ce_v5.platform.rules.pivotphase_replay import (
    ReplayResult,
    ReplaySeries,
    replay_from_series,
)
from ce_v5.platform.rules.pivotphase_state_codec import serialize_state
from ce_v5.platform.rules.void import (
    VOID_SNAP_BEARISH_SOURCE_ID,
    VOID_SNAP_BULLISH_SOURCE_ID,
)
from ce_v5.platform.rules.volume_profile import (
    VP_HVN_SOURCE_ID,
    VP_LVN_SOURCE_ID,
    VP_POC_SOURCE_ID,
    VP_VAH_SOURCE_ID,
    VP_VAL_SOURCE_ID,
)

if TYPE_CHECKING:
    # ALINEAR: mismo tipo Session que usa materializers.py (el del Protocol).
    from ce_v5.infra.db.ports import Session
    from source.rules.scalar import ScalarValue

# Ventana de normalizacion / lookback [PARIDAD v4, A CALIBRAR] (PIVOT-06 Q4).
NORM_WINDOW = 100

# Campos de PivotParams en orden fijo para la huella params_version (PIVOT-06 Q5).
_PIVOT_PARAM_FIELDS: tuple[str, ...] = (
    "phase1_impulse_min",
    "phase1_min_candles",
    "phase2_near_tolerance",
    "phase2_break_threshold",
    "phase3_zone_match",
    "phase4_exhaustion_min",
    "phase4_min_candles",
    "phase4_delta_drop",
    "phase5_flip_min_candles",
    "phase5_timeout_candles",
)


def params_version(
    params: PivotParams, conf_params: ConfidenceParams, norm_window: int
) -> str:
    """Huella canonica (clave=valor;...) de la identidad de replay (PIVOT-06 Q5).

    (PivotParams en orden fijo) + formula_version del modelo + NORM_WINDOW. Un cambio en
    cualquiera produce un params_version distinto -> snapshot con identidad distinta.
    Determinista, Decimals como str; entra en la PK del snapshot.
    """
    fsm = ";".join(f"{name}={getattr(params, name)}" for name in _PIVOT_PARAM_FIELDS)
    return f"fsm:{fsm};formula_version={conf_params.formula_version};nw={norm_window}"


def replay_pivotphase(
    session: Session,
    exchange: str,
    symbol: str,
    timeframe: str,
    open_time: int,
    history_bars: int,
) -> ReplayResult:
    """Materializa los 17 insumos, replaya el FSM desde IDLE y persiste el snapshot.

    Emite las ultimas history_bars (las NORM_WINDOW primeras son lookback que avanza la
    FSM y ceba distribuciones). Escribe el snapshot de la barra vigente (as-of).
    Las 17 series vienen ALINEADAS; ReplaySeries valida la misma longitud (fail-loud).
    """
    total = history_bars + NORM_WINDOW
    candles = read_ohlcv_window(session, exchange, symbol, timeframe, open_time, total)
    if not candles:
        return ReplayResult((), PivotState())

    def _mat(source_id: str) -> tuple[Decimal, ...]:
        # Import local: evita el ciclo con materializers.py, que en T5 importa
        # estas specs para registrarlas. SOURCE_MATERIALIZERS ya existe cuando
        # se invoca (call-time), no en import-time.
        from ce_v5.entrypoints.worker_rules.materializers import (
            SOURCE_MATERIALIZERS,
            _scalars_to_decimals,
        )

        # El registro sirve el CARRIER (tuple[ScalarValue, ...], D1). El FSM habla
        # Decimal, asi que aqui se ABRE el carrier y nada mas: ni la semantica de la
        # FSM ni el modelo de confianza cambian (P08b-D1-02 Q1, edicion mecanica).
        return _scalars_to_decimals(
            SOURCE_MATERIALIZERS[source_id].materialize(
                session, exchange, symbol, timeframe, open_time, total
            )
        )

    series = ReplaySeries(
        price=tuple(candle.close for candle in candles),
        delta=_mat(ORDERFLOW_DELTA_SOURCE_ID),
        delta_momentum=_mat(ORDERFLOW_DELTA_MOMENTUM_SOURCE_ID),
        price_range=_mat(FOOTPRINT_PRICE_RANGE_SOURCE_ID),
        vp_poc=_mat(VP_POC_SOURCE_ID),
        vp_vah=_mat(VP_VAH_SOURCE_ID),
        vp_val=_mat(VP_VAL_SOURCE_ID),
        vp_hvn=_mat(VP_HVN_SOURCE_ID),
        vp_lvn=_mat(VP_LVN_SOURCE_ID),
        absorption_bid=_mat(ABSORPTION_BID_STRENGTH_SOURCE_ID),
        absorption_ask=_mat(ABSORPTION_ASK_STRENGTH_SOURCE_ID),
        climax_top=_mat(CLIMAX_TOP_STRENGTH_SOURCE_ID),
        climax_bottom=_mat(CLIMAX_BOTTOM_STRENGTH_SOURCE_ID),
        void_bull=_mat(VOID_SNAP_BULLISH_SOURCE_ID),
        void_bear=_mat(VOID_SNAP_BEARISH_SOURCE_ID),
        notrade_score=_mat(NOTRADE_SCORE_SOURCE_ID),
        cvd=_mat(CVD_SOURCE_ID),
    )
    params = PivotParams()
    conf_params = default_params()
    lookback = max(0, len(candles) - history_bars)
    result = replay_from_series(
        series, PivotState(), params, conf_params, NORM_WINDOW, lookback
    )
    pv = params_version(params, conf_params, NORM_WINDOW)
    newest_confidence = result.outcomes[-1].confidence if result.outcomes else None
    write_pivotphase_snapshot(
        session,
        exchange,
        symbol,
        timeframe,
        pv,
        open_time,
        serialize_state(result.final_state),
        newest_confidence,
    )
    return result


@dataclass(frozen=True, slots=True)
class PivotphasePhaseSpec:
    """Materializador de pivotphase.phase: proyecta la fase (0-5) del replay a Dec."""

    def materialize(
        self,
        session: Session,
        exchange: str,
        symbol: str,
        timeframe: str,
        open_time: int,
        history_bars: int,
    ) -> tuple[ScalarValue, ...]:
        from ce_v5.entrypoints.worker_rules.materializers import (
            _decimals_to_scalars,
        )

        result = replay_pivotphase(
            session, exchange, symbol, timeframe, open_time, history_bars
        )
        return _decimals_to_scalars(
            tuple(Decimal(outcome.phase) for outcome in result.outcomes)
        )


@dataclass(frozen=True, slots=True)
class PivotphaseConfidenceSpec:
    """Materializador de pivotphase.confidence: proyecta la confianza del replay."""

    def materialize(
        self,
        session: Session,
        exchange: str,
        symbol: str,
        timeframe: str,
        open_time: int,
        history_bars: int,
    ) -> tuple[ScalarValue, ...]:
        from ce_v5.entrypoints.worker_rules.materializers import (
            _decimals_to_scalars,
        )

        result = replay_pivotphase(
            session, exchange, symbol, timeframe, open_time, history_bars
        )
        return _decimals_to_scalars(
            tuple(outcome.confidence for outcome in result.outcomes)
        )
