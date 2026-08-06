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

from ce_v5.platform.rules.rawclose import MARKET_CLOSE_SOURCE_ID
from source.datasource import (
    DataSourceDeclaration,
    HistoryUnit,
    MemoryModel,
    ParamSpec,
    Servibility,
    SharingScope,
    SourceType,
)
from source.families.market import Timeframe
from source.rules.scalar import ScalarType, ScalarValue

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


SWING_HIGH_SOURCE_ID = "swing.high"
SWING_LOW_SOURCE_ID = "swing.low"

# Fuerza simetrica N=R por defecto: el fractal (DA-I03-1). Param overridable.
SWING_STRENGTH_DEFAULT = 2


def _swing_declaration(source_id: str) -> DataSourceDeclaration:
    """Declaracion comun de swing.high/swing.low (WINDOWED, dictamen P08b-SWING-01).

    Pivote geometrico servible por barra: el ultimo pivote confirmado del tipo en la
    ventana acotada, o el extremo de la ventana si no hay ninguno (fallback paridad v4).
    WINDOWED (ficha A-1.4: recomputa ventana, SIN snapshot). strength = fuerza simetrica
    N=R, overridable (default 2, fractal). consumes market.close (la serie de precio).
    """
    return DataSourceDeclaration(
        source_id=source_id,
        source_type=SourceType.OBSERVABLE,
        servibility=Servibility.CONTINUOUS,
        memory_model=MemoryModel.WINDOWED,
        value_type=ScalarType.DECIMAL,
        evaluation_contexts=tuple(tf.value for tf in Timeframe),
        history_units=(HistoryUnit.BARS,),
        params=(
            ParamSpec(
                name="strength",
                value_type=ScalarType.INTEGER,
                default=ScalarValue(
                    scalar_type=ScalarType.INTEGER,
                    integer_value=SWING_STRENGTH_DEFAULT,
                ),
            ),
        ),
        overridable_params=("strength",),
        shared_evaluation=True,
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=("exchange", "symbol", "timeframe", "strength"),
        consumes=(MARKET_CLOSE_SOURCE_ID,),
    )


def swing_high_declaration() -> DataSourceDeclaration:
    """swing.high: ultimo pivote HIGH confirmado en ventana, o max de la ventana."""
    return _swing_declaration(SWING_HIGH_SOURCE_ID)


def swing_low_declaration() -> DataSourceDeclaration:
    """swing.low: ultimo pivote LOW confirmado en ventana, o min de la ventana."""
    return _swing_declaration(SWING_LOW_SOURCE_ID)


def declarations() -> tuple[DataSourceDeclaration, ...]:
    """Declaraciones que este modulo publica al catalogo vivo (discovery, MAT-02)."""
    return (swing_high_declaration(), swing_low_declaration())
