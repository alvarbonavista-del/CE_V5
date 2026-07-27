"""Verificacion del RSI puro contra un referente determinista INDEPENDIENTE.

Referente = misma definicion Wilder+semilla SMA computada en aritmetica
RACIONAL EXACTA (fractions.Fraction, cero redondeo), por un camino de codigo
distinto del de produccion (Decimal pinneado). Coincidencia a tolerancia
fina tras el warm-up (P08b-01/02, T-04 Q-T04-1). El fixture de cierres es
FIJO y versionado.
"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

from ce_v5.platform.rules.indicators.rsi import wilder_rsi

# Fixture FIJO de cierres (golden). 40 velas con subidas y bajadas.
_CLOSES = [
    Decimal(v)
    for v in (
        "100.00",
        "100.50",
        "101.20",
        "100.80",
        "101.50",
        "102.30",
        "101.90",
        "102.70",
        "103.40",
        "102.60",
        "101.80",
        "102.20",
        "103.10",
        "104.00",
        "103.50",
        "104.60",
        "105.20",
        "104.40",
        "103.90",
        "104.80",
        "105.70",
        "106.30",
        "105.60",
        "104.90",
        "105.50",
        "106.80",
        "107.40",
        "106.60",
        "105.80",
        "106.40",
        "107.90",
        "108.50",
        "107.70",
        "106.90",
        "107.60",
        "108.80",
        "109.50",
        "108.60",
        "107.80",
        "108.90",
    )
]

_TOL = Fraction(1, 10**25)


def _reference_rsi(closes: list[Decimal], period: int) -> list[Fraction | None]:
    fr = [Fraction(c) for c in closes]
    n = len(fr)
    out: list[Fraction | None] = [None] * n
    if n < period + 1:
        return out
    gains: list[Fraction] = []
    losses: list[Fraction] = []
    for i in range(1, n):
        ch = fr[i] - fr[i - 1]
        gains.append(ch if ch > 0 else Fraction(0))
        losses.append(-ch if ch < 0 else Fraction(0))
    p = Fraction(period)

    def rsi(ag: Fraction, al: Fraction) -> Fraction:
        if al == 0:
            return Fraction(100)
        if ag == 0:
            return Fraction(0)
        rs = ag / al
        return Fraction(100) - Fraction(100) / (1 + rs)

    ag = sum(gains[:period], Fraction(0)) / p
    al = sum(losses[:period], Fraction(0)) / p
    out[period] = rsi(ag, al)
    for i in range(period, n - 1):
        ag = (ag * (p - 1) + gains[i]) / p
        al = (al * (p - 1) + losses[i]) / p
        out[i + 1] = rsi(ag, al)
    return out


def test_rsi_matches_exact_reference_within_tolerance() -> None:
    period = 14
    got = wilder_rsi(_CLOSES, period)
    ref = _reference_rsi(_CLOSES, period)
    assert len(got) == len(ref) == len(_CLOSES)
    for i, (g, r) in enumerate(zip(got, ref, strict=True)):
        if r is None:
            assert g is None, f"indice {i}: se esperaba warm-up (None)"
        else:
            assert g is not None, f"indice {i}: valor faltante"
            assert abs(Fraction(g) - r) <= _TOL, f"indice {i}: fuera de tolerancia"


def test_warmup_is_none_until_period() -> None:
    period = 14
    got = wilder_rsi(_CLOSES, period)
    assert all(v is None for v in got[:period])
    assert got[period] is not None


def test_monotone_up_saturates_to_100() -> None:
    closes = [Decimal(100) + Decimal(i) for i in range(30)]
    got = wilder_rsi(closes, 14)
    assert got[-1] == Decimal(100)


def test_monotone_down_saturates_to_0() -> None:
    closes = [Decimal(100) - Decimal(i) for i in range(30)]
    got = wilder_rsi(closes, 14)
    assert got[-1] == Decimal(0)


def test_bit_for_bit_reproducible() -> None:
    a = wilder_rsi(_CLOSES, 14)
    b = wilder_rsi(_CLOSES, 14)
    assert [None if x is None else str(x) for x in a] == [
        None if x is None else str(x) for x in b
    ]


def test_insufficient_history_all_none() -> None:
    got = wilder_rsi(_CLOSES[:10], 14)
    assert all(v is None for v in got)
