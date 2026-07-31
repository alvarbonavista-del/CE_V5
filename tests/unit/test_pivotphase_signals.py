"""Tests del productor de impulse_score (P08c P5 T2): inputs inyectados, deterministas.

|delta| (magnitud): el score es sign-agnostico; la direccion la decide la FSM.
"""

from decimal import Decimal

from ce_v5.platform.rules.pivotphase_signals import normalize_impulse_score

_DIST = (Decimal(1), Decimal(2), Decimal(3), Decimal(4), Decimal(5))


def test_empty_distribution_is_none() -> None:
    assert normalize_impulse_score(Decimal(3), ()) is None


def test_above_all_gives_100() -> None:
    assert normalize_impulse_score(Decimal(100), _DIST) == Decimal("100.00")


def test_below_all_gives_0() -> None:
    # |delta| menor que toda la ventana -> percentil 0 -> 0.00 (sin impulso).
    assert normalize_impulse_score(Decimal("0.5"), _DIST) == Decimal("0.00")


def test_uses_absolute_value_sign_agnostic() -> None:
    up = normalize_impulse_score(Decimal(4), _DIST)
    down = normalize_impulse_score(Decimal(-4), _DIST)
    assert up == down


def test_midrank_middle_value() -> None:
    # |delta|=3 en (1,2,3,4,5): (2 menores + 1 igual/2)/5 = 0.5 -> 50.00
    assert normalize_impulse_score(Decimal(3), _DIST) == Decimal("50.00")


def test_threshold_70_as_percentile() -> None:
    # Una barra cuyo |delta| supera al 70% de la ventana reciente marca >= 70 y pasa el
    # gate phase1_impulse_min=70 (reinterpretado como percentil, 6a).
    dist = tuple(Decimal(i) for i in range(1, 11))  # 1..10
    # |delta|=8: (7 menores + 1 igual/2)/10 = 0.75 -> 75.00 >= 70
    assert normalize_impulse_score(Decimal(8), dist) == Decimal("75.00")
    # |delta|=7: (6 + 0.5)/10 = 0.65 -> 65.00 < 70
    assert normalize_impulse_score(Decimal(7), dist) == Decimal("65.00")


def test_deterministic() -> None:
    a = normalize_impulse_score(Decimal("3.5"), _DIST)
    b = normalize_impulse_score(Decimal("3.5"), _DIST)
    assert a == b
