"""fib.* -- nucleo PURO de niveles Fibonacci (parametrizado por rango explicito).

Re-expresion pura en v5 del FibonacciEngine de v4 (paridad de RESULTADO/SEMANTICA,
sin engines). Dictamen Central P08b-13:
  - Nucleo PURO de 4 fuentes: fib.levels (grid de 17 niveles), fib.nearest_level,
    fib.level_pct, fib.direction. Parametrizado por (pivot_high, pivot_low)
    EXPLICITOS (D1).
  - DE DONDE SALE EL RANGO se decide luego (DEC-FIB-RANGO-DIFERIDO): stateless
    (swing.*) vs recursive (grid con histeresis L2). La funcion pura es identica
    para ambos: f(pivot_high, pivot_low, price) -> niveles + nearest + pct + dir.
  - near/bounce NO son fuentes: touch_pct (0.3%) es umbral de decision -> Rule;
    bounce = cercania + candle.shadow_signal -> Rule (D3, DEC-UMBRAL-LOCUS).
  - Pivotes = swing.* (D4); el DrawingStore (UI) queda fuera (D5).
  - Ratios/pcts Fibonacci son constantes DEFINITORIAS (viven en la fuente),
    Decimal EXACTO; los round() de v4 eran presentacion (D6). Sin AHP.

Niveles (paridad v4):
  Retrazados dentro (7):  0, 23.6, 38.2, 50, 61.8, 78.6, 100 (% del rango).
  Extensiones arriba (5): 127.2, 141.4, 161.8, 200, 261.8.
  Extensiones abajo (5): -27.2, -41.4, -61.8, -100, -161.8.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import Enum

FIB_FORMULA_VERSION = 1

_PREC = 34
_HUNDRED = Decimal(100)

# Ratios dentro del rango (retrazados) y sus porcentajes (0=low, 100=high).
_INSIDE_RATIOS = (
    Decimal("0"),
    Decimal("0.236"),
    Decimal("0.382"),
    Decimal("0.5"),
    Decimal("0.618"),
    Decimal("0.786"),
    Decimal("1"),
)
_INSIDE_PCTS = (
    Decimal("0"),
    Decimal("23.6"),
    Decimal("38.2"),
    Decimal("50"),
    Decimal("61.8"),
    Decimal("78.6"),
    Decimal("100"),
)

# Ratios de extension fuera del rango (relativos al tamano del rango).
_EXT_RATIOS = (
    Decimal("0.272"),
    Decimal("0.414"),
    Decimal("0.618"),
    Decimal("1"),
    Decimal("1.618"),
)
_EXT_ABOVE_PCTS = (
    Decimal("127.2"),
    Decimal("141.4"),
    Decimal("161.8"),
    Decimal("200"),
    Decimal("261.8"),
)
_EXT_BELOW_PCTS = (
    Decimal("-27.2"),
    Decimal("-41.4"),
    Decimal("-61.8"),
    Decimal("-100"),
    Decimal("-161.8"),
)


class FibDirection(Enum):
    ABOVE = "above"
    BELOW = "below"


@dataclass(frozen=True)
class FibLevels:
    """Grid Fibonacci de 17 niveles para un rango [pivot_low, pivot_high].

    ordered_* recorren de mas abajo a mas arriba (abajo -> dentro -> arriba),
    emparejando cada nivel con su porcentaje (0=pivot_low, 100=pivot_high).
    """

    inside: tuple[Decimal, ...]  # 7 retrazados (pivot_low -> pivot_high)
    above: tuple[Decimal, ...]  # 5 extensiones por encima
    below: tuple[Decimal, ...]  # 5 extensiones por debajo
    ordered_levels: tuple[Decimal, ...]  # 17 niveles, abajo -> arriba
    ordered_pcts: tuple[Decimal, ...]  # 17 porcentajes, mismos indices


def _check_range(pivot_high: Decimal, pivot_low: Decimal) -> None:
    if pivot_high <= pivot_low:
        raise ValueError("pivot_high debe ser > pivot_low (rango > 0)")


def fib_levels(pivot_high: Decimal, pivot_low: Decimal) -> FibLevels:
    """Los 17 niveles Fibonacci del rango [pivot_low, pivot_high] (Decimal exacto)."""
    _check_range(pivot_high, pivot_low)
    with localcontext() as ctx:
        ctx.prec = _PREC
        ctx.rounding = ROUND_HALF_EVEN
        rng = pivot_high - pivot_low
        inside = tuple(pivot_low + r * rng for r in _INSIDE_RATIOS)
        above = tuple(pivot_high + r * rng for r in _EXT_RATIOS)
        below = tuple(pivot_low - r * rng for r in _EXT_RATIOS)
    ordered_levels = tuple(reversed(below)) + inside + above
    ordered_pcts = tuple(reversed(_EXT_BELOW_PCTS)) + _INSIDE_PCTS + _EXT_ABOVE_PCTS
    return FibLevels(
        inside=inside,
        above=above,
        below=below,
        ordered_levels=ordered_levels,
        ordered_pcts=ordered_pcts,
    )


def _nearest(levels: FibLevels, price: Decimal) -> tuple[Decimal, Decimal]:
    """(nivel_mas_cercano, pct) al precio. En empate, gana el de indice menor
    en el orden abajo->arriba (primer minimo, como v4)."""
    best_level = levels.ordered_levels[0]
    best_pct = levels.ordered_pcts[0]
    best_dist = abs(price - best_level)
    for lv, pct in zip(
        levels.ordered_levels[1:], levels.ordered_pcts[1:], strict=False
    ):
        dist = abs(price - lv)
        if dist < best_dist:
            best_dist = dist
            best_level = lv
            best_pct = pct
    return best_level, best_pct


def nearest_level(pivot_high: Decimal, pivot_low: Decimal, price: Decimal) -> Decimal:
    """Nivel Fibonacci mas cercano al precio."""
    levels = fib_levels(pivot_high, pivot_low)
    return _nearest(levels, price)[0]


def level_pct(pivot_high: Decimal, pivot_low: Decimal, price: Decimal) -> Decimal:
    """Porcentaje Fibonacci del nivel mas cercano al precio (0=low, 100=high;
    puede ser <0 o >100 en extensiones)."""
    levels = fib_levels(pivot_high, pivot_low)
    return _nearest(levels, price)[1]


def direction(pivot_high: Decimal, pivot_low: Decimal, price: Decimal) -> FibDirection:
    """ABOVE si price >= nivel mas cercano, BELOW si no (empate -> ABOVE)."""
    levels = fib_levels(pivot_high, pivot_low)
    near = _nearest(levels, price)[0]
    return FibDirection.ABOVE if price >= near else FibDirection.BELOW
