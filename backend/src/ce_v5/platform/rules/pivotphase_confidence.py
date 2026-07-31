"""pivotphase.confidence: modelo de confianza F1-F7 (P08c-P4, nueva en v5.0).

Nucleo PURO del modelo de confianza de pivotphase (DEC-PIVOTPHASE-01): sustituye la
formula simple de v4 (50 + zone_strength/2) por un score ponderado explicable sobre
factores de orderflow (I-04 Parte 2; AHP REV 2 firmado). Fase 1 (OA-4): score
S = sum(w_i * f_i_norm) con pesos SEMILLA; las fases logistica/calibracion son
posteriores y quedan DIFERIDAS (dueno P08c).

FRONTERA P4/P5 (ELEVACION P08c-PIVOT-02, OPCION R1 ratificada por Central):
- P4 (este modulo) = MODELO: normalizacion (forma semilla, [A CALIBRAR AHP]) +
  direccion documentada + combinacion S + escala 0-100 + estructura F1/F5 diferida +
  explicabilidad (desglose por factor; gancho H-02-3 / as-of).
- P5 = CABLEADO: extraccion de los escalares crudos de cada factor desde las series
  reales (orderflow/cvd/vp/notrade, swing.*), snapshot/replay del CVD (F3) y alta en
  catalogo. Sin logica de modelo.
Cada factor recibe un ESCALAR CRUDO ya orientado (mayor = mas soporte del pivote en su
direccion documentada) MAS su ventana de distribucion reciente; el modulo lo normaliza.

FACTORES (I-04 2.3; AHP REV 2). Activos en v5.0 (peso 1/5 cada uno):
  F2 exhaustion de delta, F3 divergencia de CVD, F4 esfuerzo vs resultado,
  F6 contexto de volume profile, F7 void/notrade (penaliza).
Diferidos (peso 0, activables sin cambio de estructura):
  F1 absorcion (espera candle.open + absorption.*), F5 imbalance apilado (espera fuente
  de celdas de footprint). Activarlos = anadir peso + funcion + input, no reestructurar.

EVIDENCIA AUSENTE NO INFLA: un factor ausente o no evaluable aporta 0 (no se renormaliza
el denominador); la confianza sube conforme se acumula evidencia en cada cierre
(DEC-PROVISIONAL-02). Si NINGUN factor activo es evaluable, confidence = None.

DETERMINISTA y reproducible bit a bit (ADR-007): solo Decimal, sin float; cuantizacion
ROUND_HALF_EVEN. DEC-AHP-01: todos los pesos/cortes/ventanas son SEMILLAS [A CALIBRAR
AHP], nunca verdades; la calibracion (walk-forward sobre corpus) esta DIFERIDA.

DECLARACION DIFERIDA: la DataSource pivotphase.confidence declara consumes que incluyen
swing.* (F3) y la fuente de void/notrade (F7), AUN INEXISTENTES en el catalogo; se
hornea en P5 cuando esas fuentes esten disponibles. Mismo criterio ya aceptado en
absorption.py ("como F3 espera a swing.*"). Este modulo entrega el nucleo puro del
modelo, independiente de esa espera.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# Peso semilla: cinco factores activos, 1/5 cada uno (AHP REV 2). Decimal exacto.
_ACTIVE_WEIGHT = Decimal(1) / Decimal(5)
# Cortes [PARIDAD v4] de F6 (GAP-P08c): vol_ratio del nivel frente a la media de la
# ventana. HVN pleno soporte, LVN/void sin soporte. Semillas [A CALIBRAR AHP].
_HVN_CUT = Decimal("1.5")
_LVN_CUT = Decimal("0.3")
# Cuantizacion determinista de la confianza final (0-100), ROUND_HALF_EVEN (ADR-007).
_CONFIDENCE_QUANTUM = Decimal("0.01")


class Factor(StrEnum):
    """Los siete factores del modelo (I-04 2.3). Taxonomia estable.

    Activos en v5.0 (peso 1/5): F2, F3, F4, F6, F7. Diferidos (peso 0, activables sin
    reestructurar): F1 (absorcion; espera candle.open + absorption.*) y F5 (imbalance
    apilado; espera celdas de footprint).
    """

    F1_ABSORPTION = "f1_absorption"
    F2_DELTA_EXHAUSTION = "f2_delta_exhaustion"
    F3_CVD_DIVERGENCE = "f3_cvd_divergence"
    F4_EFFORT_RESULT = "f4_effort_result"
    F5_STACKED_IMBALANCE = "f5_stacked_imbalance"
    F6_VP_CONTEXT = "f6_vp_context"
    F7_VOID_NOTRADE = "f7_void_notrade"


@dataclass(frozen=True, slots=True)
class FactorInput:
    """Insumo de un factor (frontera R1): escalar crudo YA orientado + distribucion.

    raw: valor crudo del factor en la barra, orientado por el extractor (P5) de modo
    que MAYOR = mas soporte del pivote en su direccion documentada. Para F7 la
    orientacion es intrinseca (raw = intensidad de void) y el modelo la invierte. Para
    F6 raw = vol_ratio del nivel (volumen del nivel / media de la ventana), por cortes.
    distribution: ventana de distribucion reciente del propio simbolo/TF contra la
    que se normaliza (percentil). Vacia = factor NO evaluable para los factores por
    percentil; F6 es por cortes y no la usa.
    """

    raw: Decimal
    distribution: tuple[Decimal, ...] = ()


@dataclass(frozen=True, slots=True)
class ConfidenceInputs:
    """Insumos por factor activo. None = ausente en esta barra (aporta 0, conservador).

    F1/F5 diferidos: se anadiran como campos cuando se activen; no reestructuran nada.
    """

    f2: FactorInput | None = None
    f3: FactorInput | None = None
    f4: FactorInput | None = None
    f6: FactorInput | None = None
    f7: FactorInput | None = None


@dataclass(frozen=True, slots=True)
class ConfidenceParams:
    """Parametros SEMILLA del modelo (DEC-AHP-01: [A CALIBRAR AHP], nunca verdades).

    weights: peso por factor; activos 1/5, F1/F5 0. La suma DEBE ser 1 (garantiza que la
    confianza cae en 0-100). hvn_cut/lvn_cut: cortes [PARIDAD v4] de F6. formula_version
    entra en la cache_key (I-01 C2/C3): un cambio de pesos/cortes/forma la incrementa.
    """

    weights: tuple[tuple[Factor, Decimal], ...]
    hvn_cut: Decimal
    lvn_cut: Decimal
    formula_version: int

    def __post_init__(self) -> None:
        total = sum((w for _, w in self.weights), Decimal(0))
        if total != Decimal(1):
            msg = f"la suma de pesos debe ser 1 (es {total}); escala 0-100."
            raise ValueError(msg)
        if any(w < 0 for _, w in self.weights):
            msg = "los pesos no pueden ser negativos."
            raise ValueError(msg)
        if self.lvn_cut < 0 or self.hvn_cut <= self.lvn_cut:
            msg = "se exige 0 <= lvn_cut < hvn_cut para F6."
            raise ValueError(msg)
        if self.formula_version < 1:
            msg = "formula_version debe ser >= 1."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class FactorContribution:
    """Aporte de un factor al score (explicabilidad / gancho H-02-3, as-of)."""

    factor: Factor
    weight: Decimal
    normalized: Decimal | None
    contribution: Decimal
    evaluable: bool


@dataclass(frozen=True, slots=True)
class ConfidenceResult:
    """Salida del modelo. confidence en 0-100 (None si NINGUN factor fue evaluable)."""

    confidence: Decimal | None
    score: Decimal | None
    contributions: tuple[FactorContribution, ...]
    formula_version: int
    used_factors: tuple[Factor, ...]


def default_params() -> ConfidenceParams:
    """Parametros semilla v5.0 (AHP REV 2): activos 1/5, F1/F5 0, cortes PARIDAD v4."""
    return ConfidenceParams(
        weights=(
            (Factor.F1_ABSORPTION, Decimal(0)),
            (Factor.F2_DELTA_EXHAUSTION, _ACTIVE_WEIGHT),
            (Factor.F3_CVD_DIVERGENCE, _ACTIVE_WEIGHT),
            (Factor.F4_EFFORT_RESULT, _ACTIVE_WEIGHT),
            (Factor.F5_STACKED_IMBALANCE, Decimal(0)),
            (Factor.F6_VP_CONTEXT, _ACTIVE_WEIGHT),
            (Factor.F7_VOID_NOTRADE, _ACTIVE_WEIGHT),
        ),
        hvn_cut=_HVN_CUT,
        lvn_cut=_LVN_CUT,
        formula_version=1,
    )


def _weight_of(params: ConfidenceParams, factor: Factor) -> Decimal:
    for candidate, weight in params.weights:
        if candidate is factor:
            return weight
    msg = f"factor sin peso declarado: {factor}."
    raise ValueError(msg)


def _percentile_rank(value: Decimal, distribution: Sequence[Decimal]) -> Decimal:
    """Rango percentil de value en la distribucion reciente, en [0,1] (mid-rank).

    (n_menores + n_iguales/2) / n. Seed [A CALIBRAR AHP]: percentil vs z-score y el
    tamano de ventana son de la calibracion. distribution vacia -> el llamador ya lo
    trata como no evaluable.
    """
    n = len(distribution)
    below = sum(1 for d in distribution if d < value)
    equal = sum(1 for d in distribution if d == value)
    return (Decimal(below) + Decimal(equal) / Decimal(2)) / Decimal(n)


def _normalized_percentile(fin: FactorInput, *, descending: bool) -> Decimal:
    rank = _percentile_rank(fin.raw, fin.distribution)
    return (Decimal(1) - rank) if descending else rank


def _normalized_vp_context(vol_ratio: Decimal, params: ConfidenceParams) -> Decimal:
    """F6 por cortes [PARIDAD v4]: LVN (<=lvn_cut) -> 0; HVN (>=hvn_cut) -> 1; lineal
    entre cortes. VA-edge queda como refinamiento de calibracion (diferido).
    """
    if vol_ratio <= params.lvn_cut:
        return Decimal(0)
    if vol_ratio >= params.hvn_cut:
        return Decimal(1)
    return (vol_ratio - params.lvn_cut) / (params.hvn_cut - params.lvn_cut)


def compute_confidence(
    inputs: ConfidenceInputs, params: ConfidenceParams
) -> ConfidenceResult:
    """Confianza 0-100 del pivote provisional: S = sum(w_i * f_i_norm), escala x100.

    Un factor ausente o no evaluable aporta 0 (la evidencia ausente NO infla la
    confianza). Si NINGUN factor activo es evaluable, confidence = None. Determinista.
    """
    contributions: list[FactorContribution] = []
    # Factores por percentil (raw + distribucion). F7 invierte: void penaliza.
    percentile_specs: tuple[tuple[Factor, FactorInput | None, bool], ...] = (
        (Factor.F2_DELTA_EXHAUSTION, inputs.f2, False),
        (Factor.F3_CVD_DIVERGENCE, inputs.f3, False),
        (Factor.F4_EFFORT_RESULT, inputs.f4, False),
        (Factor.F7_VOID_NOTRADE, inputs.f7, True),
    )
    for factor, fin, descending in percentile_specs:
        weight = _weight_of(params, factor)
        if fin is None or not fin.distribution:
            contributions.append(
                FactorContribution(factor, weight, None, Decimal(0), evaluable=False)
            )
            continue
        norm = _normalized_percentile(fin, descending=descending)
        contributions.append(
            FactorContribution(factor, weight, norm, weight * norm, evaluable=True)
        )
    # F6 por cortes (no usa distribucion).
    weight6 = _weight_of(params, Factor.F6_VP_CONTEXT)
    if inputs.f6 is None:
        contributions.append(
            FactorContribution(
                Factor.F6_VP_CONTEXT, weight6, None, Decimal(0), evaluable=False
            )
        )
    else:
        norm6 = _normalized_vp_context(inputs.f6.raw, params)
        contributions.append(
            FactorContribution(
                Factor.F6_VP_CONTEXT, weight6, norm6, weight6 * norm6, evaluable=True
            )
        )
    frozen_contribs = tuple(contributions)
    used = tuple(c.factor for c in frozen_contribs if c.evaluable)
    if not used:
        return ConfidenceResult(None, None, frozen_contribs, params.formula_version, ())
    score = sum((c.contribution for c in frozen_contribs), Decimal(0))
    confidence = (score * Decimal(100)).quantize(
        _CONFIDENCE_QUANTUM, rounding=ROUND_HALF_EVEN
    )
    return ConfidenceResult(
        confidence, score, frozen_contribs, params.formula_version, used
    )
