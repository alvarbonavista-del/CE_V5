"""Replay determinista de pivotphase desde series materializadas (P08c P5 T4c).

replay_from_series es la LOGICA PURA del replay (DICTAMEN P08c-PIVOT-07 Q1): dado el
conjunto de series ya materializadas y el PivotState del snapshot ancla, recorre las
barras oldest->newest manteniendo las ventanas trailing de NORM_WINDOW, arma BarSignals
(gate de fase 1) y ConfidenceInputs (F2/F4/F6; F3/F1/F5/F7 diferidos = None), hila el
estado por evaluate_bar (P3) y gradua por compute_confidence (P4), y emite (phase,
confidence) por barra. Sin BD: el glue (replay_pivotphase) materializa y persiste.

Ventanas INCLUSIVE hasta la barra i (Q2). Las primeras `lookback` barras solo CEBAN las
ventanas (Q5): no se emiten ni avanzan la FSM; la continuidad RECURSIVE la ancla el
snapshot. WARM-UP natural (Q3): sin distribucion suficiente, impulse_score None (la FSM
no arranca) y los factores no evaluables aportan 0. Determinista, solo Decimal.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ce_v5.platform.rules.pivotphase import (
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
        }
        if len(lengths) != 1:
            msg = "las series de ReplaySeries deben tener la misma longitud."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class BarOutcome:
    """Salida por barra emitida del replay: fase (0-5) y confianza 0-100."""

    phase: int
    confidence: Decimal


def _nearest_vp_touch(
    price: Decimal, poc: Decimal, vah: Decimal, val: Decimal
) -> VpTouch:
    """Nivel VP mas cercano al precio entre {poc, vah, val} (Q6). Empate: poc."""
    candidates = (("poc", poc), ("vah", vah), ("val", val))
    best_type, best_price = min(candidates, key=lambda c: abs(price - c[1]))
    return VpTouch(level_type=best_type, level_price=best_price)


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
) -> tuple[BarOutcome, ...]:
    """Recorre las barras, hila el estado y emite (phase, confidence) por barra emitida.

    Las primeras `lookback` barras solo ceban ventanas (no emiten ni avanzan la FSM); el
    resto emite. Ventanas trailing INCLUSIVE de norm_window. Ver docstring del modulo.
    """
    abs_delta: list[Decimal] = []
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
        if i < lookback:
            continue
        bar = BarSignals(
            price=series.price[i],
            delta=delta,
            impulse_score=normalize_impulse_score(delta, window_abs),
            delta_momentum=series.delta_momentum[i],
            vp_touch=_nearest_vp_touch(
                series.price[i], series.vp_poc[i], series.vp_vah[i], series.vp_val[i]
            ),
            absorption=None,
        )
        state, _event = evaluate_bar(state, bar, params)
        inputs = ConfidenceInputs(
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
    return tuple(outcomes)
