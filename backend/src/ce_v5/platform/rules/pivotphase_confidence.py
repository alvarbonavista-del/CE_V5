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

FACTORES (I-04 2.3; AHP REV 2). Con peso en v5.0 (1/6 cada uno tras P08c-CONF-01):
  F1 absorcion, F2 exhaustion de delta, F3 divergencia de CVD, F4 esfuerzo vs resultado,
  F6 contexto de volume profile, F7 void/notrade (penaliza).
Diferido (peso 0): F5 imbalance apilado (espera fuente de celdas de footprint).

PESO NO ES LO MISMO QUE INPUT VIVO. F1 se activo en P08c-CONF-01 con su extractor
real (absorption.bid/ask_strength, orientado por la direccion del pivote). F3 y F7
tienen peso pero su input aun se construye -- llegan como None y aportan 0 --, asi
que el techo
efectivo de hoy es 4/6 = 66,67 y no 100. Se cierran en 3b (F3) y 3c (F7).

F1/F2/F3/F4/F7 se normalizan por PERCENTIL (raw + distribucion reciente). F6 se
normaliza por DISTANCIA a niveles VP servibles (ELEVACION P08c-PIVOT-05: corrige
el vol_ratio inicial; los vp.* exponen PRECIOS, no ratios de volumen).

EVIDENCIA AUSENTE NO INFLA: un factor ausente o no evaluable aporta 0 (no se renormaliza
el denominador); la confianza sube conforme se acumula evidencia en cada cierre
(DEC-PROVISIONAL-02). Si NINGUN factor activo es evaluable, confidence = None.

DETERMINISTA y reproducible bit a bit (ADR-007): solo Decimal, sin float; cuantizacion
ROUND_HALF_EVEN. DEC-AHP-01: todos los pesos/ventanas son SEMILLAS [A CALIBRAR AHP],
nunca verdades; la calibracion (walk-forward sobre corpus) esta DIFERIDA.

CONSUMES, AL DIA: pivotphase.* ya declara absorption.bid/ask_strength (F1, vivo desde
P08c-CONF-01). Lo que F3 y F7 necesiten se anadira cuando sus extractores existan
(3b y 3c): una arista se declara cuando se LEE, no antes.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# Peso semilla: SEIS factores activos, 1/6 cada uno (AHP REV 2 + P08c-CONF-01, que
# activa F1). Solo F5 queda en 0.
#
# 1/6 NO ES TERMINANTE Y AUN ASI LA SUMA CIERRA EXACTA. Decimal redondea 1/6 al alza en
# su ultimo digito (…6667), asi que sumar seis veces ese valor da 1.000…000, que compara
# == Decimal(1). Se verifico ademas que se mantiene con prec 6, 9, 15, 28, 34 y 50:
# el invariante de __post_init__ (suma == 1) no depende del contexto. Si algun dia
# se pasara a un reparto que NO cierre exacto, la validacion mordera -- que es justo
# lo que tiene que hacer -- y la solucion sera declarar los pesos, no relajar la
# comprobacion.
_ACTIVE_WEIGHT = Decimal(1) / Decimal(6)
# Cuantizacion determinista de la confianza final (0-100), ROUND_HALF_EVEN (ADR-007).
_CONFIDENCE_QUANTUM = Decimal("0.01")


class Factor(StrEnum):
    """Los siete factores del modelo (I-04 2.3). Taxonomia estable.

    Con peso en v5.0 (1/6): F1, F2, F3, F4, F6, F7. Diferido (peso 0, activable sin
    reestructurar): F5 (imbalance apilado; espera celdas de footprint).
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
    """Insumo de un factor por PERCENTIL (F2/F3/F4/F7): escalar crudo YA orientado +
    distribucion reciente.

    raw: valor crudo del factor en la barra, orientado por el extractor (P5) de modo
    que MAYOR = mas soporte del pivote en su direccion documentada. Para F7 la
    orientacion es intrinseca (raw = intensidad de void) y el modelo la invierte.
    distribution: ventana de distribucion reciente del propio simbolo/TF contra la
    que se normaliza (percentil). Vacia = factor NO evaluable.
    """

    raw: Decimal
    distribution: tuple[Decimal, ...] = ()


@dataclass(frozen=True, slots=True)
class VpContextInput:
    """Insumo de F6 (por DISTANCIA; ELEVACION P08c-PIVOT-05): precio del pivote y los
    niveles VP servibles (precios) contra los que se mide la proximidad.

    hvn_price = precio del nodo de alto volumen (vp.hvn; iman, soporte); lvn_price =
    precio del nodo de bajo volumen / void (vp.lvn; penaliza). VA-edge (vah/val) queda
    DIFERIDO a calibracion. Los cortes HVN/LVN viven en volume_profile.py, no aqui.
    """

    price: Decimal
    hvn_price: Decimal
    lvn_price: Decimal


@dataclass(frozen=True, slots=True)
class ConfidenceInputs:
    """Insumos por factor activo. None = ausente en esta barra (aporta 0, conservador).

    f1 entra en P08c-CONF-01 y confirma lo que el diseno prometia: activar un factor
    diferido es ANADIR campo + peso + extractor, sin reestructurar nada. F5 sigue sin
    campo (espera celdas de footprint) y entrara igual el dia que se active.
    """

    f1: FactorInput | None = None
    f2: FactorInput | None = None
    f3: FactorInput | None = None
    f4: FactorInput | None = None
    f6: VpContextInput | None = None
    f7: FactorInput | None = None


@dataclass(frozen=True, slots=True)
class ConfidenceParams:
    """Parametros SEMILLA del modelo (DEC-AHP-01: [A CALIBRAR AHP], nunca verdades).

    weights: peso por factor; los seis con peso van a 1/6 y solo F5 queda en 0. La
    suma DEBE ser 1 (garantiza que la confianza cae en 0-100). formula_version entra
    en la cache_key (I-01 C2/C3): un cambio de pesos/forma la incrementa.
    """

    weights: tuple[tuple[Factor, Decimal], ...]
    formula_version: int

    def __post_init__(self) -> None:
        total = sum((w for _, w in self.weights), Decimal(0))
        if total != Decimal(1):
            msg = f"la suma de pesos debe ser 1 (es {total}); escala 0-100."
            raise ValueError(msg)
        if any(w < 0 for _, w in self.weights):
            msg = "los pesos no pueden ser negativos."
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
    """Parametros semilla v5.0: SEIS activos a 1/6, solo F5 en 0. formula_version=3.

    formula_version 2 -> 3 (P08c-CONF-01): F1 pasa de 0 a 1/6 y los otros cinco bajan de
    1/5 a 1/6, asi que la MISMA barra con los MISMOS insumos da otra confianza. Subirla
    es obligatorio -- entra en params_version, que es PK del snapshot --: sin el bump,
    un replay reinterpretaria snapshots viejos con los pesos nuevos y la serie cambiaria
    en silencio.

    TECHO EFECTIVO, que no es lo mismo que la suma de pesos. F3 y F7 tienen peso 1/6
    pero su input aun no se construye (llegan como None y aportan 0): hasta 3b/3c el
    maximo alcanzable son los cuatro factores con input vivo -- F1, F2, F4, F6 --, o sea
    4/6 = 66,67 sobre 100. Antes de esta tanda eran tres de cinco (60). Que un factor
    pesado no aporte NO infla al resto: la evidencia ausente no se renormaliza.
    """
    return ConfidenceParams(
        weights=(
            (Factor.F1_ABSORPTION, _ACTIVE_WEIGHT),
            (Factor.F2_DELTA_EXHAUSTION, _ACTIVE_WEIGHT),
            (Factor.F3_CVD_DIVERGENCE, _ACTIVE_WEIGHT),
            (Factor.F4_EFFORT_RESULT, _ACTIVE_WEIGHT),
            (Factor.F5_STACKED_IMBALANCE, Decimal(0)),
            (Factor.F6_VP_CONTEXT, _ACTIVE_WEIGHT),
            (Factor.F7_VOID_NOTRADE, _ACTIVE_WEIGHT),
        ),
        formula_version=3,
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


def _normalized_vp_context(vp: VpContextInput) -> Decimal | None:
    """F6 por DISTANCIA (ELEVACION P08c-PIVOT-05): proximidad del precio del pivote a
    vp.hvn (soporte) vs vp.lvn (void/penaliza).

    f6 = (1 + (d_lvn - d_hvn) / (d_lvn + d_hvn)) / 2, con d = |price - nivel| / price.
    En el HVN (d_hvn=0) -> 1; en el LVN (d_lvn=0) -> 0; equidistante -> 0.5. price<=0 o
    ambos niveles en el precio (d_lvn+d_hvn=0) -> None (no evaluable). VA-edge diferido.
    """
    if vp.price <= 0:
        return None
    d_hvn = abs(vp.price - vp.hvn_price) / vp.price
    d_lvn = abs(vp.price - vp.lvn_price) / vp.price
    total = d_lvn + d_hvn
    if total == 0:
        return None
    return (Decimal(1) + (d_lvn - d_hvn) / total) / Decimal(2)


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
        # F1 orientado como F2/F3/F4: MAYOR fuerza de absorcion = mas soporte del
        # pivote.
        # El extractor (P5) ya elige el LADO que casa con la direccion, asi que aqui el
        # raw llega orientado y no hay que invertir. F7 sigue siendo el unico que
        # invierte.
        (Factor.F1_ABSORPTION, inputs.f1, False),
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
    # F6 por distancia a niveles VP (no usa distribucion; puede ser no evaluable).
    weight6 = _weight_of(params, Factor.F6_VP_CONTEXT)
    norm6 = None if inputs.f6 is None else _normalized_vp_context(inputs.f6)
    if norm6 is None:
        contributions.append(
            FactorContribution(
                Factor.F6_VP_CONTEXT, weight6, None, Decimal(0), evaluable=False
            )
        )
    else:
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
