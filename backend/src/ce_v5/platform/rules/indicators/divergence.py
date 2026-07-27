"""divergence.* -- deteccion de divergencias precio/RSI (fuente derivada de velas).

Re-expresion pura en v5 de la logica de divergencias de v4
(divergence_engine.py), como funcion pura sobre ventanas ya materializadas.
Paridad de RESULTADO/SEMANTICA con v4, NO de implementacion (v5 no tiene engines).

Convenciones (fieles a v4; go de Central tras el I-03 ADDENDUM):
  - Pivotes GEOMETRICOS de precio via swing.symmetric_pivots (DA-I03-9):
    maximos sobre la serie de HIGH, minimos sobre la serie de LOW.
  - RSI Wilder (rsi.wilder_rsi) leido EN la barra del pivote de precio
    (convencion 'i' de v4).
  - Se comparan pivotes consecutivos del mismo tipo (equivale a los
    "ultimos 2" de v4 aplicado sobre replay).
  - Desigualdad ESTRICTA en precio Y en RSI (si una empata, no hay divergencia).
  - Orden determinista de salida: (barra de confirmacion, prioridad de v4).

Esta fuente NO realiza aritmetica Decimal: solo COMPARA Decimals ya
producidos por fuentes bloqueadas (rsi.*, swing.*). La reproducibilidad
bit-a-bit la garantizan esas fuentes; aqui las comparaciones son exactas.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from ce_v5.platform.rules.indicators.rsi import wilder_rsi
from ce_v5.platform.rules.indicators.swing import PivotKind, symmetric_pivots

DIVERGENCE_FORMULA_VERSION = 1

# Defaults de paridad v4.
_DEFAULT_STRENGTH = 2
_DEFAULT_RSI_PERIOD = 14


class DivergenceKind(Enum):
    REGULAR_BULL = "regular_bull"
    REGULAR_BEAR = "regular_bear"
    HIDDEN_BULL = "hidden_bull"
    HIDDEN_BEAR = "hidden_bear"


# Orden de prioridad de v4 (_DETECTION_ORDER): regular-bear, regular-bull,
# hidden-bear, hidden-bull. Se usa para ordenar la salida de forma estable.
_PRIORITY: dict[DivergenceKind, int] = {
    DivergenceKind.REGULAR_BEAR: 0,
    DivergenceKind.REGULAR_BULL: 1,
    DivergenceKind.HIDDEN_BEAR: 2,
    DivergenceKind.HIDDEN_BULL: 3,
}


@dataclass(frozen=True)
class Divergence:
    """Una divergencia confirmada entre un par de pivotes consecutivos."""

    kind: DivergenceKind
    index: int  # barra del pivote mas reciente (confirmacion)
    prev_index: int  # barra del pivote anterior del par
    price_prev: Decimal
    price_curr: Decimal
    rsi_prev: Decimal
    rsi_curr: Decimal


def _classify_pair(
    price_prev: Decimal,
    price_curr: Decimal,
    rsi_prev: Decimal,
    rsi_curr: Decimal,
    kind: PivotKind,
) -> DivergenceKind | None:
    """Clasifica un par de pivotes consecutivos del mismo tipo.

    kind == HIGH -> par de maximos -> divergencias BAJISTAS.
    kind == LOW  -> par de minimos -> divergencias ALCISTAS.
    Desigualdad estricta en precio Y en RSI; si no cumple, devuelve None.
    """
    if kind is PivotKind.HIGH:
        # Higher high + lower RSI high  -> regular bearish.
        if price_curr > price_prev and rsi_curr < rsi_prev:
            return DivergenceKind.REGULAR_BEAR
        # Lower high + higher RSI high  -> hidden bearish.
        if price_curr < price_prev and rsi_curr > rsi_prev:
            return DivergenceKind.HIDDEN_BEAR
        return None
    # kind is PivotKind.LOW
    # Lower low + higher RSI low  -> regular bullish.
    if price_curr < price_prev and rsi_curr > rsi_prev:
        return DivergenceKind.REGULAR_BULL
    # Higher low + lower RSI low  -> hidden bullish.
    if price_curr > price_prev and rsi_curr < rsi_prev:
        return DivergenceKind.HIDDEN_BULL
    return None


def _pairs_of_kind(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    rsi: Sequence[Decimal | None],
    strength: int,
    kind: PivotKind,
) -> list[Divergence]:
    series = highs if kind is PivotKind.HIGH else lows
    pivots = [p for p in symmetric_pivots(series, strength) if p.kind is kind]
    out: list[Divergence] = []
    for prev, curr in zip(pivots, pivots[1:], strict=False):
        rsi_prev = rsi[prev.index]
        rsi_curr = rsi[curr.index]
        if rsi_prev is None or rsi_curr is None:
            continue
        dv_kind = _classify_pair(prev.value, curr.value, rsi_prev, rsi_curr, kind)
        if dv_kind is None:
            continue
        out.append(
            Divergence(
                kind=dv_kind,
                index=curr.index,
                prev_index=prev.index,
                price_prev=prev.value,
                price_curr=curr.value,
                rsi_prev=rsi_prev,
                rsi_curr=rsi_curr,
            )
        )
    return out


def detect_divergences(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    strength: int = _DEFAULT_STRENGTH,
    rsi_period: int = _DEFAULT_RSI_PERIOD,
) -> tuple[Divergence, ...]:
    """Detecta todas las divergencias confirmadas sobre las series dadas.

    highs/lows/closes deben tener la misma longitud y estar alineadas por barra.
    Devuelve una tupla ordenada por (barra de confirmacion, prioridad de v4).
    """
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs, lows y closes deben tener la misma longitud")
    if strength < 1:
        raise ValueError("strength debe ser >= 1")
    if rsi_period < 1:
        raise ValueError("rsi_period debe ser >= 1")

    rsi = wilder_rsi(closes, rsi_period)

    found = _pairs_of_kind(highs, lows, rsi, strength, PivotKind.HIGH)
    found += _pairs_of_kind(highs, lows, rsi, strength, PivotKind.LOW)

    found.sort(key=lambda d: (d.index, _PRIORITY[d.kind]))
    return tuple(found)
