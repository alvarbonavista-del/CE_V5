"""Tests de la extraccion de senales de pivotphase (P08c P5 T2/T3): inyectados.

|delta| (magnitud): impulse_score y las features son sign-agnosticas en |delta|.
"""

from decimal import Decimal

from ce_v5.platform.rules.pivotphase_signals import (
    effort_result_feature,
    exhaustion_feature,
    normalize_impulse_score,
)

_DIST = (Decimal(1), Decimal(2), Decimal(3), Decimal(4), Decimal(5))


# --- impulse_score (T2) ---------------------------------------------------------------
def test_impulse_empty_distribution_is_none() -> None:
    assert normalize_impulse_score(Decimal(3), ()) is None


def test_impulse_above_all_gives_100() -> None:
    assert normalize_impulse_score(Decimal(100), _DIST) == Decimal("100.00")


def test_impulse_below_all_gives_0() -> None:
    assert normalize_impulse_score(Decimal("0.5"), _DIST) == Decimal("0.00")


def test_impulse_sign_agnostic() -> None:
    assert normalize_impulse_score(Decimal(4), _DIST) == normalize_impulse_score(
        Decimal(-4), _DIST
    )


def test_impulse_midrank_middle() -> None:
    assert normalize_impulse_score(Decimal(3), _DIST) == Decimal("50.00")


def test_impulse_threshold_70_as_percentile() -> None:
    dist = tuple(Decimal(i) for i in range(1, 11))
    assert normalize_impulse_score(Decimal(8), dist) == Decimal("75.00")
    assert normalize_impulse_score(Decimal(7), dist) == Decimal("65.00")


# --- F2 exhaustion (T3) ---------------------------------------------------------------
def test_exhaustion_at_peak_is_zero() -> None:
    # |delta| = pico reciente -> sin exhaustion -> 0.
    assert exhaustion_feature(Decimal(5), _DIST) == Decimal(0)


def test_exhaustion_far_below_peak_approaches_one() -> None:
    # |delta|=1, pico=5 -> 1 - 1/5 = 0.8.
    assert exhaustion_feature(Decimal(1), _DIST) == Decimal("0.8")


def test_exhaustion_sign_agnostic() -> None:
    assert exhaustion_feature(Decimal(2), _DIST) == exhaustion_feature(
        Decimal(-2), _DIST
    )


def test_exhaustion_empty_window_is_none() -> None:
    assert exhaustion_feature(Decimal(3), ()) is None


def test_exhaustion_zero_peak_is_none() -> None:
    assert exhaustion_feature(Decimal(0), (Decimal(0), Decimal(0))) is None


def test_exhaustion_clamped_non_negative() -> None:
    # |delta| mayor que el pico de la ventana -> acotado a 0 (no negativo).
    assert exhaustion_feature(Decimal(10), (Decimal(1), Decimal(2))) == Decimal(0)


# --- F4 esfuerzo/resultado (T3) -------------------------------------------------------
def test_effort_result_basic() -> None:
    # |delta|=10, rango=2 -> 5.
    assert effort_result_feature(Decimal(10), Decimal(2)) == Decimal(5)


def test_effort_result_sign_agnostic() -> None:
    assert effort_result_feature(Decimal(-8), Decimal(4)) == effort_result_feature(
        Decimal(8), Decimal(4)
    )


def test_effort_result_zero_range_is_none() -> None:
    assert effort_result_feature(Decimal(10), Decimal(0)) is None
