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

FACTORES (I-04 2.3; AHP REV 2). LOS SIETE con peso (1/7 cada uno tras P08c-CONF-05):
  F1 absorcion, F2 exhaustion de delta, F3 divergencia de CVD, F4 esfuerzo vs resultado,
  F5 imbalance apilado, F6 contexto de volume profile, F7 void/notrade/climax
  (penaliza).
Ya no queda ningun factor diferido.

MODELO CERRADO: PESO E INPUT VIVO COINCIDEN POR FIN. Durante toda la construccion la
distincion importo -- un factor podia pesar y no tener extractor, y entonces el techo
efectivo era menor que 100 aunque los pesos sumaran 1 --. Ese hueco se fue cerrando por
pasos: F1 en 3a (absorption.bid/ask_strength), F7 en 3b (max de climax/void/notrade),
F3 en 3c (divergencia precio-vs-CVD) y F5 en 3e-1 (imbalance.buy_stack/sell_stack). Con
los siete extractores vivos el techo efectivo es 100.

F1/F2/F3/F4/F5/F7 se normalizan por PERCENTIL (raw + distribucion reciente); F7 con
descending=True: MAYOR toxicidad -> MENOR rank -> MENOS confianza (es el UNICO factor
que invierte). F6 se normaliza por DISTANCIA a niveles VP servibles (ELEVACION
P08c-PIVOT-05: corrige el vol_ratio inicial; los vp.* exponen PRECIOS, no ratios de
volumen).

EVIDENCIA AUSENTE NO INFLA: un factor ausente o no evaluable aporta 0 (no se renormaliza
el denominador); la confianza sube conforme se acumula evidencia en cada cierre
(DEC-PROVISIONAL-02). Si NINGUN factor activo es evaluable, confidence = None.

DETERMINISTA y reproducible bit a bit (ADR-007): solo Decimal, sin float; cuantizacion
ROUND_HALF_EVEN. DEC-AHP-01: todos los pesos/ventanas son SEMILLAS [A CALIBRAR AHP],
nunca verdades; la calibracion (walk-forward sobre corpus) esta DIFERIDA.

CONSUMES, AL DIA: pivotphase.* declara las 19 fuentes que el replay LEE de verdad,
imbalance.buy_stack/sell_stack incluidas (F5, P08c-CONF-05). Una arista se declara
cuando se lee, no antes -- y ya no queda ninguna por anadir.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# Peso semilla: los SIETE factores activos, 1/7 cada uno (P08c-CONF-05, que activa F5 --
# el ultimo que quedaba en 0). Ya no hay factor diferido en el modelo.
#
# 1/7 TAMPOCO ES TERMINANTE Y LA SUMA CIERRA EXACTA, PERO NO EN CUALQUIER PRECISION, y
# esa es la diferencia con el 1/6 anterior. Verificado: sumar siete veces Decimal(1)/7
# da exactamente Decimal(1) con prec 9, 15, 28 y 34 -- la por defecto de Python (28) y
# la unica elevada que usa el repo (34, en materializers y candle) --, pero NO con prec
# 6 ni 50. El 1/6 aguantaba las seis probadas; el 1/7 depende del contexto.
#
# Se acepta porque el guarda es FAIL-LOUD: __post_init__ compara la suma con 1 y LEVANTA
# si no cuadra, asi que un cambio futuro de precision rompe en la construccion de
# default_params() -- ruidoso e inmediato --, nunca en silencio con pesos que no suman.
# Si eso llegara a pasar, la salida es declarar los pesos uno a uno (que el septimo
# absorba el residuo), no relajar la comprobacion.
_ACTIVE_WEIGHT = Decimal(1) / Decimal(7)
# Cuantizacion determinista de la confianza final (0-100), ROUND_HALF_EVEN (ADR-007).
_CONFIDENCE_QUANTUM = Decimal("0.01")


class Factor(StrEnum):
    """Los siete factores del modelo (I-04 2.3). Taxonomia estable.

    Los SIETE tienen peso (1/7) desde P08c-CONF-05: F5 (imbalance apilado) era el ultimo
    diferido y ya tiene fuente propia (imbalance.buy_stack/sell_stack). El modelo no
    guarda ningun factor con peso y sin extractor.
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

    f1 entro en P08c-CONF-01 y f5 en P08c-CONF-05, el ultimo: los dos confirmaron lo
    que el diseno prometia -- activar un factor diferido es ANADIR campo + peso +
    extractor, sin reestructurar nada --. Ya no queda ninguno por activar.
    """

    f1: FactorInput | None = None
    f2: FactorInput | None = None
    f3: FactorInput | None = None
    f4: FactorInput | None = None
    f5: FactorInput | None = None
    f6: VpContextInput | None = None
    f7: FactorInput | None = None


@dataclass(frozen=True, slots=True)
class ConfidenceParams:
    """Parametros SEMILLA del modelo (DEC-AHP-01: [A CALIBRAR AHP], nunca verdades).

    weights: peso por factor; los SIETE van a 1/7 (P08c-CONF-05). La suma DEBE ser 1
    (garantiza que la confianza cae en 0-100). formula_version entra en la cache_key
    (I-01 C2/C3): un cambio de pesos/forma la incrementa.
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
    """Parametros semilla v5.0: los SIETE factores activos a 1/7. formula_version=4.

    formula_version 3 -> 4 (P08c-CONF-05): F5 pasa de 0 a 1/7 y los otros seis bajan de
    1/6 a 1/7, asi que la MISMA barra con los MISMOS insumos da otra confianza. Subirla
    es obligatorio -- entra en params_version, que es PK del snapshot --: sin el bump,
    un replay reinterpretaria snapshots viejos con los pesos nuevos y la serie cambiaria
    en silencio.

    TECHO EFECTIVO == 100 POR FIN, y ahora si coincide con la suma de pesos. Hasta 3c
    quedaba F5 con peso pero sin extractor (llegaba None y aportaba 0), asi que el
    maximo real eran 6/7 aunque los pesos sumaran 1. Con imbalance.buy_stack y
    imbalance.sell_stack (P08c-CONF-05) los siete tienen input vivo y el techo teorico
    es alcanzable. La regla de
    fondo no cambia: un factor que no aporta NO infla al resto (la evidencia ausente no
    se renormaliza); lo que cambia es que ya no hay ninguno estructuralmente ausente.
    """
    return ConfidenceParams(
        weights=(
            (Factor.F1_ABSORPTION, _ACTIVE_WEIGHT),
            (Factor.F2_DELTA_EXHAUSTION, _ACTIVE_WEIGHT),
            (Factor.F3_CVD_DIVERGENCE, _ACTIVE_WEIGHT),
            (Factor.F4_EFFORT_RESULT, _ACTIVE_WEIGHT),
            (Factor.F5_STACKED_IMBALANCE, _ACTIVE_WEIGHT),
            (Factor.F6_VP_CONTEXT, _ACTIVE_WEIGHT),
            (Factor.F7_VOID_NOTRADE, _ACTIVE_WEIGHT),
        ),
        formula_version=4,
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
        # F5 (P08c-CONF-05) es SOPORTE como F1/F3: el extractor (P5) ya elige el
        # LADO de la pila que casa con la direccion, asi que el raw llega orientado.
        (Factor.F5_STACKED_IMBALANCE, inputs.f5, False),
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
