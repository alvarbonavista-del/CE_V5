"""Verificacion de vwap.* contra referentes EXACTOS independientes (Fraction)."""

from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from ce_v5.platform.rules.indicators.vwap import (
    VWAP_FORMULA_VERSION,
    VwapDirection,
    VwapSide,
    direction,
    distance_pct,
    side,
    value,
)


def _d(x: object) -> Decimal:
    return Decimal(str(x))


# --- 1. Golden exacto (ventana movil, HLC3) ---


def test_value_and_derived_exact_golden() -> None:
    # bar0: HLC3=(2+1+3)/3=2 ; V=10 ; vwap[0]=2
    # bar1: HLC3=(5+4+6)/3=5 ; window=[0,1]: (2*10+5*10)/20 = 70/20 = 3.5
    h = [_d(2), _d(5)]
    low = [_d(1), _d(4)]
    c = [_d(3), _d(6)]
    v = [_d(10), _d(10)]
    assert value(h, low, c, v) == (_d(2), _d("3.5"))
    # distance[0]: |3-2|/2*100 = 50
    assert distance_pct(h, low, c, v)[0] == _d(50)
    # side: 3>=2 ABOVE ; 6>=3.5 ABOVE
    assert side(h, low, c, v) == (VwapSide.ABOVE, VwapSide.ABOVE)
    # direction: bar0 None ; bar1 3.5>2 -> UP
    assert direction(h, low, c, v) == (None, VwapDirection.UP)


def test_zero_volume_is_none() -> None:
    h, low, c, v = [_d(2)], [_d(1)], [_d(3)], [_d(0)]
    assert value(h, low, c, v)[0] is None
    assert distance_pct(h, low, c, v)[0] is None
    assert side(h, low, c, v)[0] is None
    assert direction(h, low, c, v)[0] is None


def test_side_tie_is_above() -> None:
    # close == vwap -> ABOVE (via >=). Vela plana: H=L=C=5 -> HLC3=5, vwap=5, close=5.
    assert side([_d(5)], [_d(5)], [_d(5)], [_d(10)])[0] is VwapSide.ABOVE


# --- 2. Diferencial contra referente Fraction ---


def _synth(
    n: int, seed: int
) -> tuple[list[Decimal], list[Decimal], list[Decimal], list[Decimal]]:
    x = seed
    hs: list[Decimal] = []
    ls: list[Decimal] = []
    cs: list[Decimal] = []
    vs: list[Decimal] = []
    for _ in range(n):
        x = (1103515245 * x + 12345) % 2147483648
        base = Decimal(x % 100000) / Decimal(100)
        o = base
        cl = base + (Decimal((x // 7) % 2000) - Decimal(1000)) / Decimal(100)
        hi = max(o, cl) + Decimal((x // 11) % 500) / Decimal(100)
        lo = min(o, cl) - Decimal((x // 13) % 500) / Decimal(100)
        vol = Decimal(1) + Decimal((x // 17) % 100000) / Decimal(100)
        hs.append(hi)
        ls.append(lo)
        cs.append(cl)
        vs.append(vol)
    return hs, ls, cs, vs


def _ref_vwap(
    h: list[Decimal], low: list[Decimal], c: list[Decimal], v: list[Decimal], n: int
) -> list[Fraction | None]:
    out: list[Fraction | None] = []
    for i in range(len(h)):
        start = max(0, i - n + 1)
        tpv = Fraction(0)
        vol = Fraction(0)
        for j in range(start, i + 1):
            tp = (Fraction(h[j]) + Fraction(low[j]) + Fraction(c[j])) / 3
            tpv += tp * Fraction(v[j])
            vol += Fraction(v[j])
        out.append(None if vol == 0 else tpv / vol)
    return out


def test_value_matches_fraction_referent() -> None:
    h, low, c, v = _synth(250, 20250727)
    n = 20
    got = value(h, low, c, v, n_candles=n)
    ref = _ref_vwap(h, low, c, v, n)
    tol = Fraction(1, 10**28)
    for i in range(len(h)):
        gi = got[i]
        ri = ref[i]
        assert (gi is None) == (ri is None)
        if gi is not None:
            assert ri is not None
            assert abs(Fraction(gi) - ri) < tol


def test_distance_side_direction_match_referent() -> None:
    h, low, c, v = _synth(250, 555111)
    n = 20
    ref = _ref_vwap(h, low, c, v, n)
    dist = distance_pct(h, low, c, v, n_candles=n)
    sd = side(h, low, c, v, n_candles=n)
    di = direction(h, low, c, v, n_candles=n)
    tol = Fraction(1, 10**26)
    for i in range(len(h)):
        r = ref[i]
        dist_i = dist[i]
        if r is None:
            assert dist_i is None
        elif r > 0:
            assert dist_i is not None
            exp = abs(Fraction(c[i]) - r) / r * 100
            assert abs(Fraction(dist_i) - exp) < tol
        sd_i = sd[i]
        if r is None:
            assert sd_i is None
        else:
            assert sd_i == (VwapSide.ABOVE if Fraction(c[i]) >= r else VwapSide.BELOW)
        prev = ref[i - 1] if i > 0 else None
        di_i = di[i]
        if i == 0 or r is None or prev is None:
            assert di_i is None
        else:
            assert di_i == (VwapDirection.UP if r > prev else VwapDirection.DOWN)


def test_value_is_context_independent() -> None:
    h, low, c, v = _synth(60, 99)
    with localcontext() as ctx:
        ctx.prec = 6
        a = value(h, low, c, v)
    with localcontext() as ctx:
        ctx.prec = 50
        b = value(h, low, c, v)
    assert a == b


# --- 3. Version y validaciones ---


def test_formula_version_is_pinned() -> None:
    assert VWAP_FORMULA_VERSION == 1


def test_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        value([_d(1)], [_d(1)], [_d(1)], [_d(1), _d(2)])


def test_bad_n_raises() -> None:
    xs = [_d(1)] * 5
    with pytest.raises(ValueError):
        value(xs, xs, xs, xs, n_candles=0)
