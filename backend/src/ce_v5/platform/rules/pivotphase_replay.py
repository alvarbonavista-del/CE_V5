"""Replay determinista de pivotphase desde series materializadas (P08c P5 T4c).

replay_from_series es la LOGICA PURA del replay (DICTAMEN P08c-PIVOT-07 Q1): dado el
conjunto de series ya materializadas y el PivotState del snapshot ancla, recorre las
barras oldest->newest manteniendo las ventanas trailing de NORM_WINDOW, arma BarSignals
(gate de fase 1) y ConfidenceInputs (F1/F2/F4/F6 con input vivo; F3/F5/F7 = None),
hila el estado por evaluate_bar (P3) y gradua por compute_confidence (P4), y emite
(phase, confidence) por barra. Sin BD: el glue (replay_pivotphase) materializa y
persiste.

Ventanas INCLUSIVE hasta la barra i (Q2). Estrategia A (DICTAMEN P08c-PIVOT-09): la FSM
avanza DESDE IDLE sobre toda la ventana; las primeras `lookback` barras (NORM_WINDOW)
AVANZAN la FSM y ceban las ventanas pero NO se emiten. Como las secuencias del FSM son
bounded (< NORM_WINDOW), el bootstrap-desde-IDLE con ese lookback reconstruye el estado
entero (el snapshot es as-of/auditoria, no continuidad). WARM-UP natural (Q3): sin
distribucion suficiente, impulse_score None y los factores no evaluables aportan 0.
Determinista, solo Decimal.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ce_v5.platform.rules.pivotphase import (
    BEARISH,
    BULLISH,
    BarSignals,
    Phase,
    PivotParams,
    PivotState,
    VpTouch,
    evaluate_bar,
)
from ce_v5.platform.rules.pivotphase_confidence import (
    ConfidenceInputs,
    ConfidenceParams,
    FactorInput,
    VpContextInput,
    compute_confidence,
)
from ce_v5.platform.rules.pivotphase_signals import (
    effort_result_feature,
    exhaustion_feature,
    normalize_impulse_score,
)


@dataclass(frozen=True, slots=True)
class ReplaySeries:
    """Series materializadas ALINEADAS barra a barra, de la misma longitud."""

    price: tuple[Decimal, ...]
    delta: tuple[Decimal, ...]
    delta_momentum: tuple[Decimal, ...]
    price_range: tuple[Decimal, ...]
    vp_poc: tuple[Decimal, ...]
    vp_vah: tuple[Decimal, ...]
    vp_val: tuple[Decimal, ...]
    vp_hvn: tuple[Decimal, ...]
    vp_lvn: tuple[Decimal, ...]
    absorption_bid: tuple[Decimal, ...]
    absorption_ask: tuple[Decimal, ...]

    def __post_init__(self) -> None:
        lengths = {
            len(self.price),
            len(self.delta),
            len(self.delta_momentum),
            len(self.price_range),
            len(self.vp_poc),
            len(self.vp_vah),
            len(self.vp_val),
            len(self.vp_hvn),
            len(self.vp_lvn),
            len(self.absorption_bid),
            len(self.absorption_ask),
        }
        if len(lengths) != 1:
            msg = "las series de ReplaySeries deben tener la misma longitud."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class BarOutcome:
    """Salida por barra emitida del replay: fase (0-5) y confianza 0-100."""

    phase: int
    confidence: Decimal


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """Resultado del replay: outcomes por barra EMITIDA + estado final de la FSM.

    final_state = PivotState tras la ULTIMA barra recorrida (la vigente); el glue lo
    serializa para el snapshot (as-of / auditoria).
    """

    outcomes: tuple[BarOutcome, ...]
    final_state: PivotState


def _nearest_vp_touch(
    price: Decimal, poc: Decimal, vah: Decimal, val: Decimal
) -> VpTouch:
    """Nivel VP mas cercano al precio entre {poc, vah, val} (Q6). Empate: poc."""
    candidates = (("poc", poc), ("vah", vah), ("val", val))
    best_type, best_price = min(candidates, key=lambda c: abs(price - c[1]))
    return VpTouch(level_type=best_type, level_price=best_price)


def _absorption_raw(
    direction: str, bid_strength: Decimal, ask_strength: Decimal
) -> Decimal | None:
    """El raw de F1: la fuerza de absorcion del lado que SOPORTA el pivote esperado.

    LA ORIENTACION NO ES OBVIA Y AQUI ESTA LA TRAMPA. PivotState.direction es la del
    IMPULSO, no la del pivote: el propio modulo lo dice ("BULLISH = impulso alcista ->
    pivote esperado bearish"). Asi que:

      impulso BULLISH -> se espera un TECHO -> lo confirma la absorcion de COMPRADORES
        (AbsorptionSide.ASK: agresion compradora que no logra avanzar) -> ask_strength.
      impulso BEARISH -> se espera un SUELO -> lo confirma la absorcion de VENDEDORES
        (AbsorptionSide.BID: agresion vendedora que no logra hundir) -> bid_strength.

    Es la MISMA regla de contrariedad que ya aplica el gate de fase 3 de la FSM
    (is_counter_zone: con impulso BULLISH se exige zona BEARISH). Tomar el lado
    contrario seria peor que no medir: puntuaria como soporte justo la agresion que
    empuja el precio en la direccion del impulso.

    Sin direccion (FSM en IDLE, direction == "") no hay pivote que soportar: None, y F1
    aporta 0 en esa barra. Es la misma convencion conservadora de F2/F4.
    """
    if direction == BULLISH:
        return ask_strength
    if direction == BEARISH:
        return bid_strength
    return None


def _project_confidence(phase: int, confidence: Decimal | None) -> Decimal:
    """Proyeccion Q2 opcion (d): IDLE o NOT_EVALUABLE -> 0; en otro caso, el valor."""
    if phase == int(Phase.IDLE) or confidence is None:
        return Decimal(0)
    return confidence


def replay_from_series(
    series: ReplaySeries,
    snapshot_state: PivotState,
    params: PivotParams,
    conf_params: ConfidenceParams,
    norm_window: int,
    lookback: int,
) -> ReplayResult:
    """Recorre las barras, hila el estado y emite (phase, confidence) por barra emitida.

    La FSM AVANZA en TODAS las barras (DICTAMEN P08c-PIVOT-09, Estrategia A): las
    primeras `lookback` barras avanzan la FSM y ceban las ventanas, pero NO se emiten ni
    gradua su confianza; a partir de `lookback` se emite. Ventanas trailing INCLUSIVE de
    norm_window. Devuelve el estado final para el snapshot. Ver docstring del modulo.
    """
    abs_delta: list[Decimal] = []
    f1_raws: list[Decimal] = []
    f2_raws: list[Decimal] = []
    f4_raws: list[Decimal] = []
    state = snapshot_state
    outcomes: list[BarOutcome] = []
    for i in range(len(series.delta)):
        delta = series.delta[i]
        abs_delta.append(abs(delta))
        window_abs = abs_delta[-norm_window:]
        f2_raw = exhaustion_feature(delta, window_abs)
        f4_raw = effort_result_feature(delta, series.price_range[i])
        if f2_raw is not None:
            f2_raws.append(f2_raw)
        if f4_raw is not None:
            f4_raws.append(f4_raw)
        bar = BarSignals(
            price=series.price[i],
            delta=delta,
            impulse_score=normalize_impulse_score(delta, window_abs),
            delta_momentum=series.delta_momentum[i],
            vp_touch=_nearest_vp_touch(
                series.price[i], series.vp_poc[i], series.vp_vah[i], series.vp_val[i]
            ),
            # SIGUE None tras P08c-CONF-01 (paso 3a), y no por olvido: el gate de fase 3
            # exige un AbsorptionZone con zone_price, y absorption.bid/ask_strength solo
            # sirven la FUERZA [0,1] -- el nucleo detect_absorption no produce un nivel.
            # Inventar aqui un precio de zona (p.ej. el cierre) seria fabricar
            # semantica. F1 SI esta vivo: usa esa fuerza para la CONFIANZA, que no
            # necesita nivel.
            absorption=None,
        )
        state, _event = evaluate_bar(state, bar, params)
        # F1 se calcula DESPUES de avanzar la FSM porque necesita la direccion VIGENTE
        # del impulso, y se acumula ANTES del corte de lookback para que su distribucion
        # llegue cebada a la primera barra emitida (igual que f2_raws/f4_raws).
        f1_raw = _absorption_raw(
            state.direction, series.absorption_bid[i], series.absorption_ask[i]
        )
        if f1_raw is not None:
            f1_raws.append(f1_raw)
        if i < lookback:
            continue
        inputs = ConfidenceInputs(
            f1=(
                FactorInput(raw=f1_raw, distribution=tuple(f1_raws[-norm_window:]))
                if f1_raw is not None
                else None
            ),
            f2=(
                FactorInput(raw=f2_raw, distribution=tuple(f2_raws[-norm_window:]))
                if f2_raw is not None
                else None
            ),
            f4=(
                FactorInput(raw=f4_raw, distribution=tuple(f4_raws[-norm_window:]))
                if f4_raw is not None
                else None
            ),
            f6=VpContextInput(
                price=series.price[i],
                hvn_price=series.vp_hvn[i],
                lvn_price=series.vp_lvn[i],
            ),
        )
        result = compute_confidence(inputs, conf_params)
        outcomes.append(
            BarOutcome(
                phase=state.phase,
                confidence=_project_confidence(state.phase, result.confidence),
            )
        )
    return ReplayResult(outcomes=tuple(outcomes), final_state=state)
