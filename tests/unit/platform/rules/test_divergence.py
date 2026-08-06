"""Verificacion de divergence.* contra un referente EXACTO independiente.

Metodo P08b (ratificado): el nucleo novedoso de esta fuente (convencion RSI
'i', las 4 reglas, el orden) se verifica de forma diferencial contra un
referente reimplementado de forma independiente:
  - RSI: reimplementado con fractions.Fraction (exacto), distinto del
    wilder_rsi (Decimal) que usa la fuente.
  - reglas de divergencia y orden: reimplementadas aparte.
Los pivotes se localizan con swing.symmetric_pivots, que ya esta bloqueado
con su propio golden en test_swing.py (dependencia verificada). El
clasificador de par se prueba ademas con valores escritos a mano.
"""

from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from ce_v5.platform.rules.indicators.divergence import (
    DIVERGENCE_FORMULA_VERSION,
    DivergenceKind,
    _classify_pair,
    detect_divergences,
)
from ce_v5.platform.rules.indicators.swing import PivotKind, symmetric_pivots

_PRIORITY = {
    DivergenceKind.REGULAR_BEAR: 0,
    DivergenceKind.REGULAR_BULL: 1,
    DivergenceKind.HIDDEN_BEAR: 2,
    DivergenceKind.HIDDEN_BULL: 3,
}


def _d(x: object) -> Decimal:
    return Decimal(str(x))


# --- 1. Clasificador de par: las 4 reglas + estrictitud (verificable a mano) ---


def test_classify_regular_bear() -> None:
    # maximos: precio sube (higher high) + RSI baja (lower high) -> regular bear
    assert (
        _classify_pair(_d(100), _d(110), _d(70), _d(60), PivotKind.HIGH)
        is DivergenceKind.REGULAR_BEAR
    )


def test_classify_hidden_bear() -> None:
    # maximos: precio baja (lower high) + RSI sube (higher high) -> hidden bear
    assert (
        _classify_pair(_d(110), _d(100), _d(60), _d(70), PivotKind.HIGH)
        is DivergenceKind.HIDDEN_BEAR
    )


def test_classify_regular_bull() -> None:
    # minimos: precio baja (lower low) + RSI sube (higher low) -> regular bull
    assert (
        _classify_pair(_d(100), _d(90), _d(30), _d(40), PivotKind.LOW)
        is DivergenceKind.REGULAR_BULL
    )


def test_classify_hidden_bull() -> None:
    # minimos: precio sube (higher low) + RSI baja (lower low) -> hidden bull
    assert (
        _classify_pair(_d(90), _d(100), _d(40), _d(30), PivotKind.LOW)
        is DivergenceKind.HIDDEN_BULL
    )


def test_classify_strict_price_equal_no_fire() -> None:
    assert _classify_pair(_d(100), _d(100), _d(70), _d(60), PivotKind.HIGH) is None


def test_classify_strict_rsi_equal_no_fire() -> None:
    assert _classify_pair(_d(100), _d(110), _d(60), _d(60), PivotKind.HIGH) is None


def test_classify_aligned_is_not_divergence() -> None:
    # maximos: precio sube y RSI sube -> confirmacion, NO divergencia
    assert _classify_pair(_d(100), _d(110), _d(60), _d(70), PivotKind.HIGH) is None


# --- 2. Referente independiente (RSI Fraction + reglas reimplementadas) ---


def _ref_rsi(closes: list[Decimal], period: int) -> list[Fraction | None]:
    n = len(closes)
    vals = [Fraction(c) for c in closes]
    rsi: list[Fraction | None] = [None] * n
    if n - 1 < period:
        return rsi
    changes = [vals[i] - vals[i - 1] for i in range(1, n)]
    gains = [c if c > 0 else Fraction(0) for c in changes]
    losses = [-c if c < 0 else Fraction(0) for c in changes]
    avg_gain = sum(gains[:period], Fraction(0)) / period
    avg_loss = sum(losses[:period], Fraction(0)) / period

    def to_rsi(ag: Fraction, al: Fraction) -> Fraction:
        if al == 0:
            return Fraction(100)
        if ag == 0:
            return Fraction(0)
        rs = ag / al
        return Fraction(100) - Fraction(100) / (Fraction(1) + rs)

    rsi[period] = to_rsi(avg_gain, avg_loss)
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        rsi[i] = to_rsi(avg_gain, avg_loss)
    return rsi


def _ref_divergences(
    highs: list[Decimal],
    lows: list[Decimal],
    closes: list[Decimal],
    strength: int,
    period: int,
) -> list[tuple[int, DivergenceKind, int]]:
    rsi = _ref_rsi(closes, period)
    rows: list[tuple[int, int, DivergenceKind, int]] = []
    for kind, series in ((PivotKind.HIGH, highs), (PivotKind.LOW, lows)):
        pivots = [p for p in symmetric_pivots(series, strength) if p.kind is kind]
        for prev, curr in zip(pivots, pivots[1:], strict=False):
            rp, rc = rsi[prev.index], rsi[curr.index]
            if rp is None or rc is None:
                continue
            pp, pc = Fraction(prev.value), Fraction(curr.value)
            k: DivergenceKind | None = None
            if kind is PivotKind.HIGH:
                if pc > pp and rc < rp:
                    k = DivergenceKind.REGULAR_BEAR
                elif pc < pp and rc > rp:
                    k = DivergenceKind.HIDDEN_BEAR
            else:
                if pc < pp and rc > rp:
                    k = DivergenceKind.REGULAR_BULL
                elif pc > pp and rc < rp:
                    k = DivergenceKind.HIDDEN_BULL
            if k is not None:
                rows.append((curr.index, _PRIORITY[k], k, prev.index))
    rows.sort()
    return [(idx, k, prev) for (idx, _p, k, prev) in rows]


def _synthetic_ohlc(
    n: int, seed: int
) -> tuple[list[Decimal], list[Decimal], list[Decimal]]:
    """OHLC deterministico (LCG) con valores muy dispersos: sin empates
    practicos, para que swing (Opcion B) coincida con la nocion estricta."""
    x = seed
    highs: list[Decimal] = []
    lows: list[Decimal] = []
    closes: list[Decimal] = []
    for _ in range(n):
        x = (1103515245 * x + 12345) % 2147483648
        mid = Decimal(x % 500000) / Decimal(1000)
        spread = Decimal(1) + Decimal((x // 500000) % 300) / Decimal(100)
        c_off = (Decimal((x // 7) % 400) - Decimal(200)) / Decimal(100)
        highs.append(mid + spread)
        lows.append(mid - spread)
        closes.append(mid + c_off)
    return highs, lows, closes


def test_detect_matches_independent_referent() -> None:
    highs, lows, closes = _synthetic_ohlc(400, 1234567)
    got = [
        (d.index, d.kind, d.prev_index) for d in detect_divergences(highs, lows, closes)
    ]
    expected = _ref_divergences(highs, lows, closes, 2, 14)
    assert got == expected
    assert len(got) > 0  # el pipeline realmente detecta divergencias


# --- 3. Reproducibilidad, version y validaciones ---


def test_detect_is_context_independent() -> None:
    highs, lows, closes = _synthetic_ohlc(150, 42)
    with localcontext() as ctx:
        ctx.prec = 6
        a = detect_divergences(highs, lows, closes)
    with localcontext() as ctx:
        ctx.prec = 50
        b = detect_divergences(highs, lows, closes)
    assert a == b


def test_formula_version_is_pinned() -> None:
    assert DIVERGENCE_FORMULA_VERSION == 1


def test_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        detect_divergences([_d(1)], [_d(1)], [_d(1), _d(2)])


def test_bad_strength_raises() -> None:
    xs = [_d(1)] * 10
    with pytest.raises(ValueError):
        detect_divergences(xs, xs, xs, strength=0)


def test_bad_period_raises() -> None:
    xs = [_d(1)] * 10
    with pytest.raises(ValueError):
        detect_divergences(xs, xs, xs, rsi_period=0)


def test_short_series_returns_empty() -> None:
    xs = [_d(i) for i in range(3)]
    assert detect_divergences(xs, xs, xs) == ()
