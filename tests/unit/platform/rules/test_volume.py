"""Verificacion de volume.* contra referentes EXACTOS independientes."""

from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from ce_v5.platform.rules.indicators.volume import (
    VOLUME_FORMULA_VERSION,
    VolumeDirection,
    direction,
    is_increasing,
    ratio_vs_avg,
)


def _d(x: object) -> Decimal:
    return Decimal(str(x))


# --- 1. ratio_vs_avg: golden exacto + edge avg<=0 + diferencial + contexto ---


def test_ratio_exact_golden() -> None:
    vols = [_d(10), _d(20), _d(30), _d(40)]
    r = ratio_vs_avg(vols, lookback=3)
    assert r[0] is None
    assert r[1] == _d(2)  # 20 / mean(10)
    assert r[2] == _d(2)  # 30 / mean(10,20)=15
    assert r[3] == _d(2)  # 40 / mean(10,20,30)=20


def test_ratio_zero_avg_is_one() -> None:
    vols = [_d(0), _d(0), _d(0), _d(5)]
    r = ratio_vs_avg(vols, lookback=3)
    assert r[3] == _d(1)  # media 0 -> fail-safe 1


def _synth_vol(n: int, seed: int) -> list[Decimal]:
    x = seed
    out: list[Decimal] = []
    for _ in range(n):
        x = (1103515245 * x + 12345) % 2147483648
        out.append(Decimal(1) + Decimal(x % 1000000) / Decimal(100))
    return out


def test_ratio_matches_fraction_referent() -> None:
    vols = _synth_vol(300, 424242)
    lookback = 20
    r = ratio_vs_avg(vols, lookback=lookback)
    tol = Fraction(1, 10**30)
    for i in range(len(vols)):
        window = vols[max(0, i - lookback) : i]
        ri = r[i]
        if not window:
            assert ri is None
            continue
        avg = sum((Fraction(v) for v in window), Fraction(0)) / len(window)
        exp = Fraction(vols[i]) / avg
        assert ri is not None
        assert abs(Fraction(ri) - exp) < tol


def test_ratio_is_context_independent() -> None:
    vols = _synth_vol(80, 7)
    with localcontext() as ctx:
        ctx.prec = 6
        a = ratio_vs_avg(vols)
    with localcontext() as ctx:
        ctx.prec = 50
        b = ratio_vs_avg(vols)
    assert a == b


# --- 2. direction (empate -> UP) ---


def test_direction_cases_with_tie() -> None:
    vols = [_d(10), _d(10), _d(5), _d(7)]
    assert direction(vols) == (
        None,
        VolumeDirection.UP,  # 10 >= 10
        VolumeDirection.DOWN,  # 5 < 10
        VolumeDirection.UP,  # 7 >= 5
    )


# --- 3. is_increasing ---


def test_is_increasing_hand() -> None:
    inc = is_increasing([_d(1), _d(2), _d(3), _d(4), _d(5)], lookback=4)
    # bar4: ref=[1,2,3,4] -> mean(1,2)=1.5 < mean(3,4)=3.5 -> True; resto insuf.
    assert inc == (False, False, False, False, True)


def test_is_increasing_decreasing_is_false() -> None:
    dec = is_increasing([_d(4), _d(3), _d(2), _d(1), _d(0)], lookback=4)
    assert dec[4] is False


def test_is_increasing_matches_fraction_referent() -> None:
    vols = _synth_vol(300, 135791)
    lookback = 20
    got = is_increasing(vols, lookback=lookback)
    for i in range(len(vols)):
        ref = vols[max(0, i - lookback) : i]
        if len(ref) < 4:
            assert got[i] is False
            continue
        half = len(ref) // 2
        first = sum((Fraction(v) for v in ref[:half]), Fraction(0)) / half
        second = sum((Fraction(v) for v in ref[half:]), Fraction(0)) / (len(ref) - half)
        assert got[i] == (second > first)


# --- 4. Version y validaciones ---


def test_formula_version_is_pinned() -> None:
    assert VOLUME_FORMULA_VERSION == 1


def test_bad_lookback_raises() -> None:
    with pytest.raises(ValueError):
        ratio_vs_avg([_d(1)] * 5, lookback=0)
    with pytest.raises(ValueError):
        is_increasing([_d(1)] * 5, lookback=0)
