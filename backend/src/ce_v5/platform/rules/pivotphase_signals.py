"""Extraccion de senales de pivotphase desde series materializadas (P08c P5 T2/T3).

Convierte las series ya materializadas (orderflow.delta, footprint, vp.*) en los
insumos que consume pivotphase: el impulse_score del gate de fase 1 (T2) y las features
crudas de los factores de confianza (T3). Capa de EXTRACCION (P5), no de modelo: la
normalizacion final (percentil / combinacion) vive en pivotphase_confidence.py; aqui se
producen los escalares crudos por barra. Determinista, solo Decimal (ADR-007).

6a / T2 (impulse_score): escalado de |delta| por percentil de su distribucion reciente,
0-100 (opcion B del AHP REV 2; DICTAMEN P08c-PIVOT-04). Deriva firmada respecto a v4.

T3 (features de confianza, formas semilla ratificadas P08c-PIVOT-05 [A CALIBRAR]):
- F2 exhaustion: 1 - |delta| / max(|delta| reciente). Mas caida desde el pico = mas
  exhaustion = mas soporte (6b: delta real, no proxy notrade).
- F4 esfuerzo/resultado: |delta| / span de precio de la barra. Alto delta con poco
  desplazamiento = esfuerzo absorbido (I-04 2.3).
- F6 (contexto VP) NO necesita funcion: se arma directo como VpContextInput(price,
  vp.hvn, vp.lvn) y su normalizacion por distancia vive en el modelo.
Las features F2/F4 son el 'raw' que el modelo normaliza luego por percentil contra su
distribucion reciente.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# Cuantizacion determinista del score 0-100 (ADR-007).
_IMPULSE_QUANTUM = Decimal("0.01")


def _percentile_rank(value: Decimal, distribution: Sequence[Decimal]) -> Decimal:
    """Rango percentil mid-rank de value en [0,1]: (n_menores + n_iguales/2) / n.

    Misma forma semilla que pivotphase_confidence (P4); si aparece un tercer uso se
    extrae a un util comun. distribution vacia la trata el llamador (no evaluable).
    """
    n = len(distribution)
    below = sum(1 for d in distribution if d < value)
    equal = sum(1 for d in distribution if d == value)
    return (Decimal(below) + Decimal(equal) / Decimal(2)) / Decimal(n)


def normalize_impulse_score(
    delta: Decimal, recent_abs_delta: Sequence[Decimal]
) -> Decimal | None:
    """impulse_score 0-100 = percentil de |delta| en la distribucion reciente x100.

    recent_abs_delta: ventana reciente de |delta| del propio simbolo/TF (materializada).
    Vacia -> None: sin base para normalizar es "sin impulso" (BarSignals.impulse_score
    None -> la FSM no arranca fase 1). El tamano de la ventana y percentil vs z-score
    son [A CALIBRAR AHP]. Determinista.
    """
    if not recent_abs_delta:
        return None
    rank = _percentile_rank(abs(delta), recent_abs_delta)
    return (rank * Decimal(100)).quantize(_IMPULSE_QUANTUM, rounding=ROUND_HALF_EVEN)


def exhaustion_feature(
    delta: Decimal, recent_abs_delta: Sequence[Decimal]
) -> Decimal | None:
    """F2 (exhaustion de delta): 1 - |delta| / max(|delta| reciente), acotado a [0,1].

    Mas caida desde el pico reciente = mas exhaustion = mas soporte (6b: delta real, no
    proxy notrade). recent_abs_delta = ventana reciente de |delta| que INCLUYE la barra
    actual (asi el pico >= |delta| y el resultado cae en [0,1]); si por ventana no lo
    incluyera, el acotado a >=0 lo protege. Pico 0 (todo delta nulo) o ventana vacia ->
    None (no evaluable). Tamano de ventana [A CALIBRAR]. Es el 'raw' de F2; el modelo lo
    normaliza luego por percentil.
    """
    if not recent_abs_delta:
        return None
    peak = max(recent_abs_delta)
    if peak <= 0:
        return None
    feature = Decimal(1) - abs(delta) / peak
    return feature if feature > 0 else Decimal(0)


def effort_result_feature(delta: Decimal, price_range: Decimal) -> Decimal | None:
    """F4 (esfuerzo/resultado): |delta| / span de precio de la barra.

    Alto delta con poco desplazamiento = esfuerzo absorbido = mas soporte (I-04 2.3).
    price_range = span de precio del footprint de la barra (max-min de sus celdas).
    Rango <= 0 -> None (no evaluable). Es el 'raw' de F4; el modelo lo normaliza luego
    por percentil.
    """
    if price_range <= 0:
        return None
    return abs(delta) / price_range
