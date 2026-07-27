"""Verificacion de la primitiva swing.* (P08b): fixtures GOLDEN verificados a
mano (incluidas mesetas del caso CVD), referente INDEPENDIENTE por fuerza bruta
sobre serie larga determinista, y simetria bajo negacion (high<->low). Convencion
Opcion B + ancla PRIMERA(a), coordinada con P08c. Geometrico y exacto (sin coma
flotante): coincidencia EXACTA, sin tolerancia.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import pytest

from ce_v5.platform.rules.indicators.swing import (
    SWING_FORMULA_VERSION,
    Pivot,
    PivotKind,
    symmetric_pivots,
)


def _s(*vals: int) -> list[Decimal]:
    return [Decimal(str(v)) for v in vals]


def _as_tuples(pivots: Sequence[Pivot]) -> list[tuple[int, Decimal, str]]:
    return [(p.index, p.value, p.kind.value) for p in pivots]


_GOLDEN: list[tuple[list[Decimal], int, list[tuple[int, Decimal, str]]]] = [
    (_s(1, 2, 3, 4, 3, 2, 1), 2, [(3, Decimal("4"), "high")]),
    (_s(1, 2, 3, 3, 2, 1), 2, [(2, Decimal("3"), "high")]),
    (_s(5, 4, 3, 3, 4, 5), 2, [(2, Decimal("3"), "low")]),
    (_s(3, 3, 2, 1), 2, []),
    (
        _s(1, 3, 2, 3, 1),
        1,
        [
            (1, Decimal("3"), "high"),
            (2, Decimal("2"), "low"),
            (3, Decimal("3"), "high"),
        ],
    ),
    (_s(1, 1, 1, 1, 1), 2, []),
    (
        _s(1, 3, 3, 1, 1, 1, 3, 3, 1),
        1,
        [
            (1, Decimal("3"), "high"),
            (3, Decimal("1"), "low"),
            (6, Decimal("3"), "high"),
        ],
    ),
]


@pytest.mark.parametrize(("series", "k", "expected"), _GOLDEN)
def test_golden_fixtures(
    series: list[Decimal], k: int, expected: list[tuple[int, Decimal, str]]
) -> None:
    assert _as_tuples(symmetric_pivots(series, k)) == expected


def _reference(series: list[Decimal], k: int) -> list[tuple[int, Decimal, str]]:
    # Referente INDEPENDIENTE: escanea por ANCLA (la produccion itera corridas).
    n = len(series)
    res: list[tuple[int, Decimal, str]] = []
    for a in range(n):
        if a > 0 and series[a - 1] == series[a]:
            continue
        v = series[a]
        b = a
        while b + 1 < n and series[b + 1] == v:
            b += 1
        if a - k < 0 or b + k > n - 1:
            continue
        left = series[a - k : a]
        right = series[b + 1 : b + k + 1]
        if all(x < v for x in left) and all(x < v for x in right):
            res.append((a, v, "high"))
        elif all(x > v for x in left) and all(x > v for x in right):
            res.append((a, v, "low"))
    return res


def _deterministic_series(n: int) -> list[Decimal]:
    # Enteros con MUCHAS mesetas a proposito (paso -5..4).
    x = 1234567
    price = Decimal(100)
    out = [price]
    for _ in range(n - 1):
        x = (1103515245 * x + 12345) % (2**31)
        price = price + (Decimal(x % 10) - Decimal(5))
        out.append(price)
    return out


@pytest.mark.parametrize("k", [1, 2, 3, 5])
def test_matches_independent_reference_long_series(k: int) -> None:
    series = _deterministic_series(500)
    assert _as_tuples(symmetric_pivots(series, k)) == _reference(series, k)


@pytest.mark.parametrize("k", [1, 2, 3])
def test_symmetry_under_negation(k: int) -> None:
    series = _deterministic_series(300)
    base = symmetric_pivots(series, k)
    neg = symmetric_pivots([-x for x in series], k)
    expected = tuple(
        Pivot(
            p.index,
            -p.value,
            PivotKind.LOW if p.kind is PivotKind.HIGH else PivotKind.HIGH,
        )
        for p in base
    )
    assert neg == expected


def test_strength_must_be_positive() -> None:
    with pytest.raises(ValueError, match="strength >= 1"):
        symmetric_pivots(_s(1, 2, 3), 0)


def test_empty_and_short_series() -> None:
    assert symmetric_pivots([], 2) == ()
    assert symmetric_pivots(_s(1, 2, 3), 2) == ()


def test_formula_version_pinned() -> None:
    assert SWING_FORMULA_VERSION == 1
