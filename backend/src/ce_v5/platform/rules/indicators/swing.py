"""Primitiva geometrica de pivotes swing.* (P08b). SIN dependencia del substrato.

Pivote por FUERZA SIMETRICA N=R=k (el fractal es k=2), UNA sola primitiva para
PRECIO, RSI y CVD (DA-I03-4). Determinista; ZigZag/ATR solo para la vista, nunca
como fuente (DA-I03-1). Convencion MESETA-AWARE (Opcion B, coordinada con P08c
para F3/divergencia de CVD): una CORRIDA maximal de valor igual v en [a,b] es
pivote high si las k barras ANTES de a y las k barras DESPUES de b son TODAS
estrictamente < v (low: > v); se ancla en la PRIMERA barra de la corrida (a).
Degenera en el pivote estricto cuando no hay empates. Igualdad EXACTA de Decimal,
identica para las tres series.

INVARIANTE DE CAUSALIDAD (compartido con P08c): el compute PURO sobre serie
CERRADA emite SOLO pivotes CONFIRMADOS -- los que ya tienen k barras a cada lado
del extremo dentro de la serie y las cumplen. Lo provisional (barras derechas aun
sin cerrar) es de la integracion (DEC-PROVISIONAL-02), no del compute. Sin
lookahead: un pivote anclado en a se confirma en b+k.

swing.* es GEOMETRICO y SIN confianza (la confianza de orderflow vive en
pivotphase, P08c; DA-I03-9). Aplicable a CUALQUIER serie.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

SWING_FORMULA_VERSION = 1


class PivotKind(StrEnum):
    HIGH = "high"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class Pivot:
    """Pivote confirmado. index = ancla = PRIMERA barra de la corrida (a)."""

    index: int
    value: Decimal
    kind: PivotKind


def symmetric_pivots(series: Sequence[Decimal], strength: int) -> tuple[Pivot, ...]:
    """Pivotes por fuerza simetrica N=R=strength sobre `series` (oldest->newest).

    Devuelve los pivotes CONFIRMADOS en orden de ancla ascendente (highs y lows).
    Convencion en el docstring del modulo.
    """
    if strength < 1:
        msg = "symmetric_pivots exige strength >= 1."
        raise ValueError(msg)
    k = strength
    n = len(series)
    pivots: list[Pivot] = []
    i = 0
    while i < n:
        a = i
        v = series[a]
        b = a
        while b + 1 < n and series[b + 1] == v:
            b += 1
        if a - k >= 0 and b + k <= n - 1:
            left = series[a - k : a]
            right = series[b + 1 : b + k + 1]
            if all(x < v for x in left) and all(x < v for x in right):
                pivots.append(Pivot(a, v, PivotKind.HIGH))
            elif all(x > v for x in left) and all(x > v for x in right):
                pivots.append(Pivot(a, v, PivotKind.LOW))
        i = b + 1
    return tuple(pivots)
