"""Computo puro del RSI de Wilder (P08b). SIN dependencia del substrato.

Determinista y reproducible BIT A BIT: computo FORWARD desde el reset fijo
de Wilder (semilla = media simple de los primeros N cambios), sobre cierres
CERRADOS oldest->newest. Convencion FIRMADA (P08b-02, fuente primaria
TradingView + Wikipedia): Wilder/RMA, semilla SMA de N. El contexto Decimal
esta PINNEADO (precision y redondeo fijos) para reproducibilidad entre
maquinas; cualquier cambio de contexto o formula EXIGE subir
RSI_FORMULA_VERSION (ADR-008).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

RSI_FORMULA_VERSION = 1

_RSI_PRECISION = 34
_RSI_ROUNDING = ROUND_HALF_EVEN
_HUNDRED = Decimal(100)
_ONE = Decimal(1)
_ZERO = Decimal(0)


def _rsi_from_avgs(avg_gain: Decimal, avg_loss: Decimal) -> Decimal:
    # "sin bajadas" -> 100 (mercado plano cae aqui por convencion); "sin
    # subidas" -> 0. El bug de KLineChart<v10 era devolver 0 en racha 100%
    # alcista; aqui es 100.
    if avg_loss == _ZERO:
        return _HUNDRED
    if avg_gain == _ZERO:
        return _ZERO
    rs = avg_gain / avg_loss
    return _HUNDRED - _HUNDRED / (_ONE + rs)


def wilder_rsi(closes: Sequence[Decimal], period: int) -> tuple[Decimal | None, ...]:
    """Serie RSI alineada 1:1 con `closes` (oldest->newest).

    None en el warm-up (sin `period` cambios aun); el primer valor valido
    cae en el indice `period` (hacen falta period+1 cierres). Semilla:
    media simple de los primeros `period` gains/losses. Recursion de Wilder:
    avg = (avg_prev*(period-1) + nuevo)/period.
    """
    if period < 1:
        msg = "wilder_rsi exige period >= 1."
        raise ValueError(msg)
    n = len(closes)
    result: list[Decimal | None] = [None] * n
    if n < period + 1:
        return tuple(result)
    with localcontext() as ctx:
        ctx.prec = _RSI_PRECISION
        ctx.rounding = _RSI_ROUNDING
        p = Decimal(period)
        gains: list[Decimal] = []
        losses: list[Decimal] = []
        for i in range(1, n):
            change = closes[i] - closes[i - 1]
            gains.append(change if change > _ZERO else _ZERO)
            losses.append(-change if change < _ZERO else _ZERO)
        avg_gain = sum(gains[:period], _ZERO) / p
        avg_loss = sum(losses[:period], _ZERO) / p
        result[period] = _rsi_from_avgs(avg_gain, avg_loss)
        for i in range(period, n - 1):
            avg_gain = (avg_gain * (p - _ONE) + gains[i]) / p
            avg_loss = (avg_loss * (p - _ONE) + losses[i]) / p
            result[i + 1] = _rsi_from_avgs(avg_gain, avg_loss)
    return tuple(result)
