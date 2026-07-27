"""Computo puro del MACD (P08b). SIN dependencia del substrato.

Reutiliza el EMA fundado (indicators.ema): macd = EMA(fast) - EMA(slow);
signal = EMA(signal_period, macd); histogram = macd - signal (x1, NO x2:
convencion TradingView fundada en P08b-02). Defaults (12, 26, 9).

Como ambas EMAs siembran en el primer src (EMA[0]==src[0], P08b-08),
macd[0] == 0 -> INVARIANTE DE SEMILLA distintivo del MACD (signal[0]==0,
histogram[0]==0). Aislado y clavado por el candado golden.

Todo bajo el MISMO contexto Decimal pinneado que el EMA (prec 34,
ROUND_HALF_EVEN) -- las restas incluidas -- para reproducibilidad bit-a-bit.
Cambio de formula/contexto sube MACD_FORMULA_VERSION (ADR-008). warm-up =
parametro calibrado.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from ce_v5.platform.rules.indicators.ema import ema

MACD_FORMULA_VERSION = 1

_MACD_PRECISION = 34
_MACD_ROUNDING = ROUND_HALF_EVEN


@dataclass(frozen=True, slots=True)
class Macd:
    """Series MACD alineadas 1:1 con los cierres (oldest->newest)."""

    macd: tuple[Decimal, ...]
    signal: tuple[Decimal, ...]
    histogram: tuple[Decimal, ...]


def macd(
    closes: Sequence[Decimal],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> Macd:
    """MACD sobre `closes` (oldest->newest).

    macd = EMA(fast) - EMA(slow); signal = EMA(signal_period, macd);
    histogram = macd - signal (x1). Valores desde la barra 0 (via EMA);
    macd[0] == signal[0] == histogram[0] == 0 (invariante de semilla).
    """
    for name, p in (("fast", fast), ("slow", slow), ("signal_period", signal_period)):
        if p < 1:
            msg = f"macd exige {name} >= 1."
            raise ValueError(msg)
    with localcontext() as ctx:
        ctx.prec = _MACD_PRECISION
        ctx.rounding = _MACD_ROUNDING
        ema_fast = ema(closes, fast)
        ema_slow = ema(closes, slow)
        macd_line = tuple(f - s for f, s in zip(ema_fast, ema_slow, strict=True))
        signal = ema(macd_line, signal_period)
        histogram = tuple(m - g for m, g in zip(macd_line, signal, strict=True))
    return Macd(macd=macd_line, signal=signal, histogram=histogram)
