"""Verificacion de fib.* (nucleo puro) contra referentes EXACTOS independientes."""

from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from ce_v5.platform.rules.indicators.fib import (
    FIB_FORMULA_VERSION,
    FibDirection,
    direction,
    fib_levels,
    level_pct,
    nearest_level,
)

_INSIDE = [
    Fraction(0),
    Fraction(236, 1000),
    Fraction(382, 1000),
    Fraction(1, 2),
    Fraction(618, 1000),
    Fraction(786, 1000),
    Fraction(1),
]
_EXT = [
    Fraction(272, 1000),
    Fraction(414, 1000),
    Fraction(618, 1000),
    Fraction(1),
    Fraction(1618, 1000),
]


def _d(x: object) -> Decimal:
    return Decimal(str(x))


# --- 1. Golden exacto (rango 0..100) ---


def test_levels_exact_golden() -> None:
    lv = fib_levels(_d(100), _d(0))
    assert lv.inside == (
        _d(0),
        _d("23.6"),
        _d("38.2"),
        _d("50"),
        _d("61.8"),
        _d("78.6"),
        _d(100),
    )
    assert lv.above == (_d("127.2"), _d("141.4"), _d("161.8"), _d(200), _d("261.8"))
    assert lv.below == (_d("-27.2"), _d("-41.4"), _d("-61.8"), _d(-100), _d("-161.8"))
    assert lv.ordered_levels[0] == _d("-161.8")
    assert lv.ordered_levels[-1] == _d("261.8")
    assert lv.ordered_pcts[0] == _d("-161.8")
    assert lv.ordered_pcts[-1] == _d("261.8")


def test_nearest_and_pct_and_direction_golden() -> None:
    assert nearest_level(_d(100), _d(0), _d(40)) == _d("38.2")
    assert level_pct(_d(100), _d(0), _d(40)) == _d("38.2")
    assert direction(_d(100), _d(0), _d(40)) is FibDirection.ABOVE


def test_nearest_tie_picks_lower_index() -> None:
    # price=44.1 equidista de 38.2 y 50 ; en empate gana el de indice menor (38.2)
    assert nearest_level(_d(100), _d(0), _d("44.1")) == _d("38.2")


def test_direction_tie_is_above() -> None:
    assert direction(_d(100), _d(0), _d("38.2")) is FibDirection.ABOVE


# --- 2. Diferencial contra referente Fraction ---


def _ref_levels(ph: Decimal, pl: Decimal) -> tuple[list[Fraction], list[Fraction]]:
    PH, PL = Fraction(ph), Fraction(pl)
    rng = PH - PL
    inside = [PL + r * rng for r in _INSIDE]
    above = [PH + r * rng for r in _EXT]
    below = [PL - r * rng for r in _EXT]
    ordered = list(reversed(below)) + inside + above
    below_pcts = [
        Fraction(-272, 10),
        Fraction(-414, 10),
        Fraction(-618, 10),
        Fraction(-100),
        Fraction(-1618, 10),
    ]
    inside_pcts = [
        Fraction(0),
        Fraction(236, 10),
        Fraction(382, 10),
        Fraction(50),
        Fraction(618, 10),
        Fraction(786, 10),
        Fraction(100),
    ]
    above_pcts = [
        Fraction(1272, 10),
        Fraction(1414, 10),
        Fraction(1618, 10),
        Fraction(200),
        Fraction(2618, 10),
    ]
    ordered_pcts = list(reversed(below_pcts)) + inside_pcts + above_pcts
    return ordered, ordered_pcts


def _ref_nearest(ph: Decimal, pl: Decimal, price: Decimal) -> tuple[Fraction, Fraction]:
    levels, pcts = _ref_levels(ph, pl)
    p = Fraction(price)
    best_l, best_p = levels[0], pcts[0]
    best_d = abs(p - levels[0])
    for lv, pc in zip(levels[1:], pcts[1:], strict=False):
        d = abs(p - lv)
        if d < best_d:
            best_d, best_l, best_p = d, lv, pc
    return best_l, best_p


def _synth(n: int, seed: int) -> list[tuple[Decimal, Decimal, Decimal]]:
    x = seed
    out: list[tuple[Decimal, Decimal, Decimal]] = []
    for _ in range(n):
        x = (1103515245 * x + 12345) % 2147483648
        pl = Decimal(x % 50000) / Decimal(100)
        rng = Decimal(1) + Decimal((x // 7) % 50000) / Decimal(100)
        ph = pl + rng
        price = pl - rng + Decimal((x // 13) % 300000) / Decimal(100)
        out.append((ph, pl, price))
    return out


def test_levels_match_fraction_referent() -> None:
    for ph, pl, _price in _synth(300, 314159):
        got = fib_levels(ph, pl)
        ref_levels, _ref_pcts = _ref_levels(ph, pl)
        assert len(got.ordered_levels) == 17
        for g, r in zip(got.ordered_levels, ref_levels, strict=False):
            assert Fraction(g) == r


def test_nearest_pct_direction_match_referent() -> None:
    for ph, pl, price in _synth(400, 271828):
        ref_l, _ref_p = _ref_nearest(ph, pl, price)
        assert Fraction(nearest_level(ph, pl, price)) == ref_l
        exp_pct = (ref_l - Fraction(pl)) / (Fraction(ph) - Fraction(pl)) * 100
        assert Fraction(level_pct(ph, pl, price)) == exp_pct
        exp_dir = FibDirection.ABOVE if Fraction(price) >= ref_l else FibDirection.BELOW
        assert direction(ph, pl, price) is exp_dir


def test_levels_are_context_independent() -> None:
    ph, pl = _d("12345.678"), _d("9876.543")
    with localcontext() as ctx:
        ctx.prec = 6
        a = fib_levels(ph, pl).ordered_levels
    with localcontext() as ctx:
        ctx.prec = 50
        b = fib_levels(ph, pl).ordered_levels
    assert a == b


# --- 3. Version y validaciones ---


def test_formula_version_is_pinned() -> None:
    assert FIB_FORMULA_VERSION == 1


def test_invalid_range_raises() -> None:
    with pytest.raises(ValueError):
        fib_levels(_d(10), _d(10))
    with pytest.raises(ValueError):
        fib_levels(_d(5), _d(10))
