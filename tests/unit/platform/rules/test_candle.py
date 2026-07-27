"""Verificacion de candle.* contra referentes EXACTOS independientes.

Metodo P08b: cada fuente se verifica contra su formula exacta (D3 de Central:
golden contra la formula, no contra la salida redondeada de v4). El
pullback_moment se verifica ademas de forma DIFERENCIAL contra una
re-implementacion literal del algoritmo de v4 (incluida su rama inalcanzable),
probando que la version simplificada da el MISMO resultado sin codigo muerto.
"""

from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from ce_v5.platform.rules.indicators.candle import (
    CANDLE_FORMULA_VERSION,
    Direction,
    Pullback,
    ShadowSignal,
    body_pct,
    direction,
    lower_shadow_pct,
    new_high,
    new_low,
    pullback_moment,
    shadow_signal,
    upper_shadow_pct,
)


def _d(x: object) -> Decimal:
    return Decimal(str(x))


# --- 1. Anatomia: golden exacto + diferencial Fraction + bordes ---


def test_anatomy_exact_golden() -> None:
    # rng=100 (l=0,h=100), o=20,c=45 -> body=25, upper=55, lower=20
    o, h, low, c = [_d(20)], [_d(100)], [_d(0)], [_d(45)]
    assert body_pct(o, h, low, c)[0] == _d(25)
    assert upper_shadow_pct(o, h, low, c)[0] == _d(55)
    assert lower_shadow_pct(o, h, low, c)[0] == _d(20)


def test_anatomy_zero_range_is_zero() -> None:
    o, h, low, c = [_d(10)], [_d(10)], [_d(10)], [_d(10)]
    assert body_pct(o, h, low, c)[0] == _d(0)
    assert upper_shadow_pct(o, h, low, c)[0] == _d(0)
    assert lower_shadow_pct(o, h, low, c)[0] == _d(0)


def _synth(
    n: int, seed: int
) -> tuple[list[Decimal], list[Decimal], list[Decimal], list[Decimal]]:
    x = seed
    opens: list[Decimal] = []
    highs: list[Decimal] = []
    lows: list[Decimal] = []
    closes: list[Decimal] = []
    for _ in range(n):
        x = (1103515245 * x + 12345) % 2147483648
        base = Decimal(x % 100000) / Decimal(100)
        o = base
        c = base + (Decimal((x // 7) % 2000) - Decimal(1000)) / Decimal(100)
        hi = max(o, c) + Decimal((x // 11) % 500) / Decimal(100)
        lo = min(o, c) - Decimal((x // 13) % 500) / Decimal(100)
        opens.append(o)
        highs.append(hi)
        lows.append(lo)
        closes.append(c)
    return opens, highs, lows, closes


def test_anatomy_matches_fraction_referent() -> None:
    o, h, low, c = _synth(300, 987654)
    b = body_pct(o, h, low, c)
    u = upper_shadow_pct(o, h, low, c)
    lo = lower_shadow_pct(o, h, low, c)
    tol = Fraction(1, 10**30)
    for i in range(len(o)):
        rng = Fraction(h[i]) - Fraction(low[i])
        if rng <= 0:
            assert b[i] == 0 and u[i] == 0 and lo[i] == 0
            continue
        exp_b = abs(Fraction(c[i]) - Fraction(o[i])) / rng * 100
        exp_u = (Fraction(h[i]) - max(Fraction(o[i]), Fraction(c[i]))) / rng * 100
        exp_lo = (min(Fraction(o[i]), Fraction(c[i])) - Fraction(low[i])) / rng * 100
        assert abs(Fraction(b[i]) - exp_b) < tol
        assert abs(Fraction(u[i]) - exp_u) < tol
        assert abs(Fraction(lo[i]) - exp_lo) < tol


def test_anatomy_is_context_independent() -> None:
    o, h, low, c = _synth(80, 5)
    with localcontext() as ctx:
        ctx.prec = 6
        a = body_pct(o, h, low, c)
    with localcontext() as ctx:
        ctx.prec = 50
        b = body_pct(o, h, low, c)
    assert a == b


# --- 2. Direccion ---


def test_direction_cases() -> None:
    o = [_d(10), _d(10), _d(10)]
    c = [_d(11), _d(9), _d(10)]
    assert direction(o, c) == (Direction.BULLISH, Direction.BEARISH, Direction.NEUTRAL)


# --- 3. Nuevo maximo / minimo ---


def test_new_high_strict_window() -> None:
    highs = [_d(10), _d(11), _d(9), _d(12), _d(8)]
    assert new_high(highs, lookback=2) == (False, True, False, True, False)


def test_new_low_strict_window() -> None:
    lows = [_d(10), _d(9), _d(11), _d(8), _d(12)]
    assert new_low(lows, lookback=2) == (False, True, False, True, False)


# --- 4. Senal de sombra ---


def test_shadow_hammer() -> None:
    # o=10,c=12 body=2 ; l=5 lower=5 > 2*2=4 ; h=12.5 upper=0.5 < body
    assert (
        shadow_signal([_d(10)], [_d("12.5")], [_d(5)], [_d(12)])[0]
        is ShadowSignal.HAMMER
    )


def test_shadow_shooting_star() -> None:
    # o=10,c=12 body=2 ; h=17 upper=5 > 4 ; l=9.5 lower=0.5 < body
    assert (
        shadow_signal([_d(10)], [_d(17)], [_d("9.5")], [_d(12)])[0]
        is ShadowSignal.SHOOTING_STAR
    )


def test_shadow_doji_is_none() -> None:
    # body=0 -> NONE aunque haya mechas
    assert shadow_signal([_d(10)], [_d(20)], [_d(0)], [_d(10)])[0] is ShadowSignal.NONE


def test_shadow_plain_is_none() -> None:
    # cuerpo grande, mechas pequenas
    assert (
        shadow_signal([_d(10)], [_d("20.2")], [_d("9.8")], [_d(20)])[0]
        is ShadowSignal.NONE
    )


# --- 5. Pullback 3 momentos: hand + diferencial vs v4 literal ---


def _bars(
    dirs: str,
) -> tuple[list[Decimal], list[Decimal], list[Decimal], list[Decimal]]:
    o: list[Decimal] = []
    h: list[Decimal] = []
    low: list[Decimal] = []
    c: list[Decimal] = []
    for d in dirs:
        if d == "U":
            o.append(_d(10))
            c.append(_d(20))
        else:
            o.append(_d(20))
            c.append(_d(10))
        h.append(_d(21))
        low.append(_d(9))
    return o, h, low, c


def test_pullback_hand_patterns() -> None:
    assert pullback_moment(*_bars("UUUU"))[-1] is Pullback.M1_BULL
    assert pullback_moment(*_bars("DDDD"))[-1] is Pullback.M1_BEAR
    assert pullback_moment(*_bars("UUUD"))[-1] is Pullback.M2_BULL
    assert pullback_moment(*_bars("DDDU"))[-1] is Pullback.M2_BEAR
    assert pullback_moment(*_bars("UUDDU"))[-1] is Pullback.M3_BULL
    assert pullback_moment(*_bars("DDUUD"))[-1] is Pullback.M3_BEAR


def test_pullback_short_is_none() -> None:
    assert pullback_moment(*_bars("UU")) == (Pullback.NONE, Pullback.NONE)


def _ref_pullback_v4(
    o: list[Decimal],
    h: list[Decimal],
    low: list[Decimal],
    c: list[Decimal],
    end: int,
) -> Pullback:
    """Replica LITERAL de _detect_pullback_moment de v4 sobre el prefijo que
    termina en `end` (incluye la rama 'last_seg[1] >= 2')."""
    n_prefix = end + 1
    if n_prefix < 4:
        return Pullback.NONE
    lo = max(0, n_prefix - 8)
    labels: list[str] = []
    for i in range(lo, n_prefix):
        rng = h[i] - low[i]
        body = abs(c[i] - o[i])
        if rng > 0 and body < Decimal("0.10") * rng:
            continue
        labels.append("U" if c[i] > o[i] else "D")
    if len(labels) < 3:
        return Pullback.NONE
    segments: list[tuple[str, int]] = []
    i = 0
    while i < len(labels):
        d = labels[i]
        cnt = 1
        while i + cnt < len(labels) and labels[i + cnt] == d:
            cnt += 1
        segments.append((d, cnt))
        i += cnt
    n = len(segments)
    if n >= 3:
        s1, s2, s3 = segments[-3], segments[-2], segments[-1]
        if s1[0] == "U" and s2[0] == "D" and s3[0] == "U":
            return Pullback.M3_BULL
        if s1[0] == "D" and s2[0] == "U" and s3[0] == "D":
            return Pullback.M3_BEAR
    if n >= 2:
        s1, s2 = segments[-2], segments[-1]
        if s1[0] == "U" and s2[0] == "D":
            return Pullback.M2_BULL
        if s1[0] == "D" and s2[0] == "U":
            return Pullback.M2_BEAR
    last = segments[-1]
    if n == 1 or last[1] >= 2:
        return Pullback.M1_BULL if last[0] == "U" else Pullback.M1_BEAR
    return Pullback.NONE


def test_pullback_matches_v4_literal_referent() -> None:
    o, h, low, c = _synth(400, 246810)
    got = pullback_moment(o, h, low, c)
    expected = tuple(_ref_pullback_v4(o, h, low, c, i) for i in range(len(o)))
    assert got == expected


# --- 6. Version y validaciones ---


def test_formula_version_is_pinned() -> None:
    assert CANDLE_FORMULA_VERSION == 1


def test_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        body_pct([_d(1)], [_d(1)], [_d(1)], [_d(1), _d(2)])


def test_bad_lookback_raises() -> None:
    with pytest.raises(ValueError):
        new_high([_d(1)] * 5, lookback=0)
