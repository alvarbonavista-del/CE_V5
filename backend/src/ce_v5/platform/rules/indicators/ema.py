"""Computo puro del EMA (P08b). SIN dependencia del substrato.

Convencion FUNDADA (periferico de investigacion EMA, ratificada en P08b-08:
convergencia de 3 lineas + prueba logica por contradiccion + ficha A-1.4):
TradingView siembra ta.ema con el PRIMER VALOR DE LA FUENTE (src), NO con SMA de
N. INVARIANTE DISTINTIVO DE LA SEMILLA: EMA[0] == src[0]; el EMA produce VALOR
DESDE LA BARRA 0 (sin tramo None de warm-up, a diferencia del RSI). alpha =
2/(period+1); EMA[i] = alpha*src[i] + (1-alpha)*EMA[i-1].

Contexto Decimal PINNEADO (prec 34, ROUND_HALF_EVEN) para reproducibilidad
bit-a-bit. Cualquier cambio de semilla/formula/contexto sube EMA_FORMULA_VERSION
(ADR-008); el UNICO punto que depende de la semilla es EMA[0]==src[0], aislado y
verificado por el candado golden (condicion P08b-08). El warm-up es PARAMETRO
calibrado aguas abajo (I-01 B4), no un tramo None.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

EMA_FORMULA_VERSION = 1

_EMA_PRECISION = 34
_EMA_ROUNDING = ROUND_HALF_EVEN
_ONE = Decimal(1)
_TWO = Decimal(2)


def ema(src: Sequence[Decimal], period: int) -> tuple[Decimal, ...]:
    """Serie EMA alineada 1:1 con `src` (oldest->newest).

    Semilla = primer valor de la fuente: resultado[0] == src[0] (VALOR DESDE LA
    BARRA 0; sin None de warm-up). alpha = 2/(period+1).
    """
    if period < 1:
        msg = "ema exige period >= 1."
        raise ValueError(msg)
    n = len(src)
    if n == 0:
        return ()
    with localcontext() as ctx:
        ctx.prec = _EMA_PRECISION
        ctx.rounding = _EMA_ROUNDING
        alpha = _TWO / (Decimal(period) + _ONE)
        one_minus = _ONE - alpha
        out: list[Decimal] = [src[0]]
        prev = src[0]
        for i in range(1, n):
            prev = alpha * src[i] + one_minus * prev
            out.append(prev)
    return tuple(out)
