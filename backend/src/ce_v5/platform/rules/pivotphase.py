"""Nucleo PURO de la FSM de pivote de fases 0-5 (pivotphase, P08c P3).

Paridad LITERAL de la FSM de v4 (models/pivot_state_machine.py), traducida al marco v5:
- EVALUACION POR BARRA (candle_closed), NO event-driven: evaluate_bar toma las senales
  ya materializadas de una barra y avanza a lo sumo UNA fase (o invalida, o mantiene).
- ESTADO SERIALIZABLE e INMUTABLE (Decimal, no float): PivotState es un snapshot
  reproducible bit a bit (ADR-007); el store de snapshot/replay es de P5, no de aqui.
- SIN IO, SIN threading, SIN imports de infra. Nucleo testeable en aislamiento con
  senales INYECTADAS.

Las senales de una barra (BarSignals) son los observables de {orderflow, vp, absorption,
notrade} ya normalizados aguas arriba (la normalizacion relativa por ventana es de la
capa de materializacion/confianza, P4/P5; el nucleo no normaliza). None = observable
NO EVALUABLE por historia insuficiente o factor DIFERIDO: la fase que lo necesita NO
avanza (nunca se inventa una transicion).

DECISIONES DE DISENO (AHP REV 2, para revision de Central en P3):
- FASE 1 (impulso): usa impulse_score (0-100) YA normalizado aguas arriba desde
  orderflow.delta/delta_momentum (decision 6a). El nucleo puro no puede normalizar
  (no tiene la ventana), asi que recibe el score en BarSignals.
- FASE 4 (agotamiento): gate por DELTA real menguante (decision 6b, F2): |delta| cae por
  debajo de phase4_delta_drop * pico, sostenido phase4_min_candles. NO usa el
  phase4_exhaustion_min de v4 (era el umbral del proxy notrade, ahora F7/confianza, P4).
  phase4_exhaustion_min se conserva INYECTABLE (los 10 params de paridad) para el uso de
  F2 en P4; el gate ESTRUCTURAL de fase 4 no lo lee. Se eleva en el informe de P3.
- FASE 3 (absorcion): gate estructural; absorption puede ser None (DIFERIDA hasta
  candle.open, P08b): entonces la FSM se queda en fase 2 (no confirma en vivo por el
  camino completo). Los tests inyectan absorption.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import IntEnum

from ce_v5.platform.rules.absorption import (
    ABSORPTION_ASK_STRENGTH_SOURCE_ID,
    ABSORPTION_BID_STRENGTH_SOURCE_ID,
)
from ce_v5.platform.rules.climax import (
    CLIMAX_BOTTOM_STRENGTH_SOURCE_ID,
    CLIMAX_TOP_STRENGTH_SOURCE_ID,
)
from ce_v5.platform.rules.footprint_range import FOOTPRINT_PRICE_RANGE_SOURCE_ID
from ce_v5.platform.rules.notrade import NOTRADE_SCORE_SOURCE_ID
from ce_v5.platform.rules.orderflow import (
    ORDERFLOW_DELTA_MOMENTUM_SOURCE_ID,
    ORDERFLOW_DELTA_SOURCE_ID,
)
from ce_v5.platform.rules.rawclose import MARKET_CLOSE_SOURCE_ID
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
from source.datasource import (
    DataSourceDeclaration,
    HistoryUnit,
    MemoryModel,
    Servibility,
    SharingScope,
    SourceType,
)
from source.families.market import Timeframe
from source.rules.scalar import ScalarType


class Phase(IntEnum):
    IDLE = 0
    IMPULSE = 1
    ENCOUNTER = 2
    ABSORPTION = 3
    EXHAUSTION = 4
    FLIP = 5


BULLISH = "bullish"  # impulso alcista -> pivote esperado bearish
BEARISH = "bearish"


class Invalidation:
    PHASE2_PRICE_BREAK = "phase2_price_break"
    PHASE3_ZONE_BREAK = "phase3_zone_break"
    PHASE4_DELTA_REGROWTH = "phase4_delta_regrowth"
    PHASE5_TIMEOUT = "phase5_timeout"


@dataclass(frozen=True, slots=True)
class PivotParams:
    """Los 10 parametros inyectables de la FSM (semillas [PARIDAD v4]), en Decimal.

    phase4_exhaustion_min NO es un gate ESTRUCTURAL en v5 (decision 6b): se conserva
    para el factor F2 de la confianza (P4). Los demas gobiernan las transiciones/
    invalidaciones de este nucleo.
    """

    phase1_impulse_min: Decimal = Decimal("70")
    phase1_min_candles: int = 2
    phase2_near_tolerance: Decimal = Decimal("0.001")
    phase2_break_threshold: Decimal = Decimal("0.003")
    phase3_zone_match: Decimal = Decimal("0.002")
    phase4_exhaustion_min: Decimal = Decimal("60")
    phase4_min_candles: int = 3
    phase4_delta_drop: Decimal = Decimal("0.5")
    phase5_flip_min_candles: int = 2
    phase5_timeout_candles: int = 5


@dataclass(frozen=True, slots=True)
class VpTouch:
    """Un toque de nivel VP en la barra (poc/vah/val/hvn)."""

    level_type: str
    level_price: Decimal


@dataclass(frozen=True, slots=True)
class AbsorptionZone:
    """Una zona de absorcion detectada en la barra (DIFERIDA hasta candle.open)."""

    zone_price: Decimal
    zone_type: str
    zone_strength: Decimal


@dataclass(frozen=True, slots=True)
class BarSignals:
    """Observables ya materializados/normalizados de una barra CERRADA.

    None = no evaluable (historia insuficiente) o factor diferido: la fase que lo
    necesita NO avanza. price y delta son obligatorios (siempre hay vela y su delta).
    """

    price: Decimal
    delta: Decimal
    impulse_score: Decimal | None = None  # 0-100 normalizado (6a); None -> sin impulso
    delta_momentum: Decimal | None = None
    vp_touch: VpTouch | None = None
    absorption: AbsorptionZone | None = None  # DIFERIDA (candle.open)


@dataclass(frozen=True, slots=True)
class PivotState:
    """Estado SERIALIZABLE e inmutable de la FSM. IDLE por defecto.

    Toda la memoria de secuencia va aqui (fase + direccion + lo que cada fase recuerda),
    para que el snapshot/replay de P5 sea exacto y reproducible bit a bit (ADR-007).
    """

    phase: int = int(Phase.IDLE)
    direction: str = ""
    impulse_count: int = 0
    phase1_peak_delta: Decimal = Decimal(0)
    phase2_level_price: Decimal = Decimal(0)
    phase2_level_type: str = ""
    phase3_zone_price: Decimal = Decimal(0)
    phase3_zone_strength: Decimal = Decimal(0)
    exhaustion_count: int = 0
    flip_count: int = 0
    phase5_bars: int = 0


@dataclass(frozen=True, slots=True)
class PivotEvent:
    """Lo que produjo la barra: avance de fase, confirmacion o invalidacion (o nada)."""

    kind: str  # "advance" | "confirmed" | "invalidated" | "none"
    phase: int
    direction: str = ""
    reason: str = ""  # motivo de invalidacion, si kind == "invalidated"


_NONE_EVENT = PivotEvent(kind="none", phase=int(Phase.IDLE))


def _reset() -> PivotState:
    return PivotState()


def evaluate_bar(
    state: PivotState, bar: BarSignals, params: PivotParams
) -> tuple[PivotState, PivotEvent]:
    """Avanza la FSM con UNA barra cerrada. Devuelve (nuevo_estado, evento).

    Procesa la logica de la fase ACTUAL con las senales de esta barra; avanza a lo sumo
    UNA fase, invalida (vuelve a IDLE) o se mantiene. Determinista: mismo estado + misma
    barra + mismos params -> mismo resultado.
    """
    phase = state.phase
    if phase == Phase.IDLE:
        return _on_idle(state, bar, params)
    if phase == Phase.IMPULSE:
        return _on_impulse(state, bar, params)
    if phase == Phase.ENCOUNTER:
        return _on_encounter(state, bar, params)
    if phase == Phase.ABSORPTION:
        return _on_absorption(state, bar, params)
    if phase == Phase.EXHAUSTION:
        return _on_exhaustion(state, bar, params)
    return state, _NONE_EVENT


def _impulse_direction(delta: Decimal) -> str:
    return BULLISH if delta > 0 else BEARISH


def _on_idle(
    state: PivotState, bar: BarSignals, params: PivotParams
) -> tuple[PivotState, PivotEvent]:
    # Sin impulso valido -> se mantiene en IDLE reseteando el conteo.
    if bar.impulse_score is None or bar.impulse_score < params.phase1_impulse_min:
        if state.impulse_count == 0:
            return state, _NONE_EVENT
        return _reset(), _NONE_EVENT
    direction = _impulse_direction(bar.delta)
    # Conteo de velas consecutivas del mismo signo de delta.
    same = state.direction == direction and state.impulse_count > 0
    count = state.impulse_count + 1 if same else 1
    peak = max(abs(bar.delta), state.phase1_peak_delta if same else Decimal(0))
    if count >= params.phase1_min_candles:
        new = PivotState(
            phase=int(Phase.IMPULSE),
            direction=direction,
            phase1_peak_delta=peak,
        )
        return new, PivotEvent("advance", int(Phase.IMPULSE), direction)
    new = replace(
        state, direction=direction, impulse_count=count, phase1_peak_delta=peak
    )
    return new, _NONE_EVENT


def _on_impulse(
    state: PivotState, bar: BarSignals, params: PivotParams
) -> tuple[PivotState, PivotEvent]:
    peak = max(state.phase1_peak_delta, abs(bar.delta))
    state = replace(state, phase1_peak_delta=peak)
    touch = bar.vp_touch
    if touch is None:
        return state, _NONE_EVENT
    # Toque contra-direccional al impulso (paridad v4).
    if state.direction == BULLISH:
        is_counter = bar.price >= touch.level_price * (1 - params.phase3_zone_match)
    else:
        is_counter = bar.price <= touch.level_price * (1 + params.phase3_zone_match)
    if not is_counter:
        return state, _NONE_EVENT
    new = replace(
        state,
        phase=int(Phase.ENCOUNTER),
        phase2_level_price=touch.level_price,
        phase2_level_type=touch.level_type,
    )
    return new, PivotEvent("advance", int(Phase.ENCOUNTER), state.direction)


def _on_encounter(
    state: PivotState, bar: BarSignals, params: PivotParams
) -> tuple[PivotState, PivotEvent]:
    level = state.phase2_level_price
    # Invalidacion: el precio rompe el nivel > umbral.
    if level > 0:
        if state.direction == BULLISH and bar.price > level * (
            1 + params.phase2_break_threshold
        ):
            return _reset(), PivotEvent(
                "invalidated",
                int(Phase.ENCOUNTER),
                state.direction,
                Invalidation.PHASE2_PRICE_BREAK,
            )
        if state.direction == BEARISH and bar.price < level * (
            1 - params.phase2_break_threshold
        ):
            return _reset(), PivotEvent(
                "invalidated",
                int(Phase.ENCOUNTER),
                state.direction,
                Invalidation.PHASE2_PRICE_BREAK,
            )
    # Absorcion (DIFERIDA): None -> no avanza (gate estructural de fase 3).
    zone = bar.absorption
    if zone is None or level <= 0:
        return state, _NONE_EVENT
    near = abs(zone.zone_price - level) / level <= params.phase3_zone_match
    is_counter_zone = (state.direction == BULLISH and BEARISH in zone.zone_type) or (
        state.direction == BEARISH and BULLISH in zone.zone_type
    )
    if not (near and is_counter_zone):
        return state, _NONE_EVENT
    new = replace(
        state,
        phase=int(Phase.ABSORPTION),
        phase3_zone_price=zone.zone_price,
        phase3_zone_strength=zone.zone_strength,
    )
    return new, PivotEvent("advance", int(Phase.ABSORPTION), state.direction)


def _on_absorption(
    state: PivotState, bar: BarSignals, params: PivotParams
) -> tuple[PivotState, PivotEvent]:
    peak = state.phase1_peak_delta
    if peak <= 0:
        return state, _NONE_EVENT
    # Gate de fase 4 (6b): delta agresor menguante (|delta| < drop * pico), sostenido.
    if abs(bar.delta) >= peak * params.phase4_delta_drop:
        return state, _NONE_EVENT
    count = state.exhaustion_count + 1
    if count >= params.phase4_min_candles:
        new = replace(state, phase=int(Phase.EXHAUSTION), exhaustion_count=count)
        return new, PivotEvent("advance", int(Phase.EXHAUSTION), state.direction)
    return replace(state, exhaustion_count=count), _NONE_EVENT


def _on_exhaustion(
    state: PivotState, bar: BarSignals, params: PivotParams
) -> tuple[PivotState, PivotEvent]:
    # Invalidacion: el delta re-crece en la direccion original (>100% del umbral).
    peak = state.phase1_peak_delta
    delta_signed = bar.delta if state.direction == BULLISH else -bar.delta
    if delta_signed > peak * params.phase4_delta_drop * 2:
        return _reset(), PivotEvent(
            "invalidated",
            int(Phase.EXHAUSTION),
            state.direction,
            Invalidation.PHASE4_DELTA_REGROWTH,
        )
    bars = state.phase5_bars + 1
    # Deteccion del flip: el delta invierte el signo del impulso.
    flip_ok = (state.direction == BULLISH and bar.delta < 0) or (
        state.direction == BEARISH and bar.delta > 0
    )
    flip_count = state.flip_count + 1 if flip_ok else 0
    if flip_count >= params.phase5_flip_min_candles:
        return _reset(), PivotEvent("confirmed", int(Phase.FLIP), state.direction)
    # Timeout: sin flip suficiente en phase5_timeout_candles velas.
    if bars > params.phase5_timeout_candles:
        return _reset(), PivotEvent(
            "invalidated",
            int(Phase.EXHAUSTION),
            state.direction,
            Invalidation.PHASE5_TIMEOUT,
        )
    return replace(state, phase5_bars=bars, flip_count=flip_count), _NONE_EVENT


# ---------------------------------------------------------------------------
# Declaracion al catalogo (ADR-008). Cableada en discovery.py (P5, DICTAMEN PIVOT-10).
# ---------------------------------------------------------------------------

PIVOTPHASE_PHASE_SOURCE_ID = "pivotphase.phase"
PIVOTPHASE_CONFIDENCE_SOURCE_ID = "pivotphase.confidence"

# Version de la formula: la FSM puede cambiar de SEMANTICA (gates, orden de fases) SIN
# cambio de parametro, asi que formula_version va EN la cache_key (regla 3, MAT-03).
PIVOTPHASE_FORMULA_VERSION = "pivotphase.v1"

# Dimensiones del cache_key: las cuatro de flujo + reset_policy (la secuencia de la FSM
# es RECURSIVA y su punto de reinicio es un HECHO DISTINTO, como en cvd.value) +
# formula_version.
_PIVOTPHASE_CACHE_KEY_SCHEMA: tuple[str, ...] = (
    "exchange",
    "symbol",
    "market_type",
    "timeframe",
    "reset_policy",
    "formula_version",
)

# consumes: el DAG de insumos de la FSM (DICTAMEN PIVOT-10). absorption.bid/ask_strength
# entraron en 3a (F1). En 3b (P08c-CONF-01) entran las CINCO que alimentan F7:
# climax.top/bottom_strength, void.snap_bullish/bearish y notrade.score.
#
# ENMIENDA DE DAG HONESTO (3b): notrade.footprint_ineff, notrade.flow_dislocation y
# notrade.state NO se listan. F7 solo LEE notrade.score (ya es la suma de los otros
# dos bloques, asi que no hace falta releerlos por separado) y nunca toca
# notrade.state (el token STRING no participa en ningun computo de F7). Declararlas
# seria arista muerta del DAG -- decir que se usa algo que no se lee --, el mismo
# criterio que excluyo candle.open de climax.* en P08c-DET-01 paso b.
_PIVOTPHASE_CONSUMES: tuple[str, ...] = (
    MARKET_CLOSE_SOURCE_ID,
    ORDERFLOW_DELTA_SOURCE_ID,
    ORDERFLOW_DELTA_MOMENTUM_SOURCE_ID,
    FOOTPRINT_PRICE_RANGE_SOURCE_ID,
    VP_POC_SOURCE_ID,
    VP_VAH_SOURCE_ID,
    VP_VAL_SOURCE_ID,
    VP_HVN_SOURCE_ID,
    VP_LVN_SOURCE_ID,
    ABSORPTION_BID_STRENGTH_SOURCE_ID,
    ABSORPTION_ASK_STRENGTH_SOURCE_ID,
    CLIMAX_TOP_STRENGTH_SOURCE_ID,
    CLIMAX_BOTTOM_STRENGTH_SOURCE_ID,
    VOID_SNAP_BULLISH_SOURCE_ID,
    VOID_SNAP_BEARISH_SOURCE_ID,
    NOTRADE_SCORE_SOURCE_ID,
)


def pivotphase_phase_declaration() -> DataSourceDeclaration:
    """pivotphase.phase: fase actual de la FSM de pivote (0-5), entera."""
    return DataSourceDeclaration(
        source_id=PIVOTPHASE_PHASE_SOURCE_ID,
        source_type=SourceType.OBSERVABLE,
        servibility=Servibility.CONTINUOUS,
        # RECURSIVE: la fase de la barra T depende del ESTADO en T-1 (la FSM es una
        # secuencia con memoria). Corregir T contamina todo lo posterior: NO-CONFORME
        # para correccion en v5.0, se reprocesa desde snapshot (ADR-007, P5).
        memory_model=MemoryModel.RECURSIVE,
        value_type=ScalarType.INTEGER,
        evaluation_contexts=tuple(tf.value for tf in Timeframe),
        history_units=(HistoryUnit.BARS,),
        shared_evaluation=True,
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=_PIVOTPHASE_CACHE_KEY_SCHEMA,
        consumes=_PIVOTPHASE_CONSUMES,
    )


def pivotphase_confidence_declaration() -> DataSourceDeclaration:
    """pivotphase.confidence: confianza 0-100 del pivote en curso.

    Solo se DECLARA: el computo de los factores F1-F7 es P4, no de este nucleo puro.
    """
    return DataSourceDeclaration(
        source_id=PIVOTPHASE_CONFIDENCE_SOURCE_ID,
        source_type=SourceType.OBSERVABLE,
        servibility=Servibility.CONTINUOUS,
        # RECURSIVE por la misma razon que pivotphase.phase: la confianza se acumula
        # sobre el estado de la secuencia, no sobre la barra aislada.
        memory_model=MemoryModel.RECURSIVE,
        value_type=ScalarType.DECIMAL,
        evaluation_contexts=tuple(tf.value for tf in Timeframe),
        history_units=(HistoryUnit.BARS,),
        shared_evaluation=True,
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=_PIVOTPHASE_CACHE_KEY_SCHEMA,
        consumes=_PIVOTPHASE_CONSUMES,
    )


def declarations() -> tuple[DataSourceDeclaration, ...]:
    """Declaraciones de pivotphase que este modulo publica al catalogo vivo
    (discovery).
    """
    return (
        pivotphase_phase_declaration(),
        pivotphase_confidence_declaration(),
    )
