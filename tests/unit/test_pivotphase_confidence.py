"""Tests del modelo pivotphase.confidence (P08c-P4): inputs inyectados y deterministas.

Todo el modelo se ejercita con escalares y ventanas inyectados (frontera R1).
"""

from decimal import Decimal

import pytest

from ce_v5.platform.rules.pivotphase_confidence import (
    ConfidenceInputs,
    ConfidenceParams,
    Factor,
    FactorInput,
    compute_confidence,
    default_params,
)

_DIST = (Decimal(1), Decimal(2), Decimal(3), Decimal(4), Decimal(5))


def _fin(raw: object, dist: tuple[Decimal, ...] = _DIST) -> FactorInput:
    return FactorInput(raw=Decimal(str(raw)), distribution=dist)


def test_default_params_active_weights_sum_to_one() -> None:
    total = sum((w for _, w in default_params().weights), Decimal(0))
    assert total == Decimal(1)


def test_deferred_factors_have_zero_weight() -> None:
    weights = dict(default_params().weights)
    assert weights[Factor.F1_ABSORPTION] == Decimal(0)
    assert weights[Factor.F5_STACKED_IMBALANCE] == Decimal(0)
    assert weights[Factor.F2_DELTA_EXHAUSTION] == Decimal(1) / Decimal(5)


def test_all_factors_max_support_gives_100() -> None:
    inputs = ConfidenceInputs(
        f2=_fin(100),
        f3=_fin(100),
        f4=_fin(100),
        f6=FactorInput(raw=Decimal("2.0")),
        f7=_fin(-100),
    )
    r = compute_confidence(inputs, default_params())
    assert r.confidence == Decimal("100.00")
    assert set(r.used_factors) == {
        Factor.F2_DELTA_EXHAUSTION,
        Factor.F3_CVD_DIVERGENCE,
        Factor.F4_EFFORT_RESULT,
        Factor.F6_VP_CONTEXT,
        Factor.F7_VOID_NOTRADE,
    }


def test_all_factors_min_support_gives_0() -> None:
    inputs = ConfidenceInputs(
        f2=_fin(-100),
        f3=_fin(-100),
        f4=_fin(-100),
        f6=FactorInput(raw=Decimal("0.1")),
        f7=_fin(100),
    )
    r = compute_confidence(inputs, default_params())
    assert r.confidence == Decimal("0.00")


def test_f7_penalizes_higher_void_lowers_confidence() -> None:
    base = {
        "f2": _fin(3),
        "f3": _fin(3),
        "f4": _fin(3),
        "f6": FactorInput(raw=Decimal("1.0")),
    }
    low = compute_confidence(ConfidenceInputs(**base, f7=_fin(1)), default_params())
    high = compute_confidence(ConfidenceInputs(**base, f7=_fin(5)), default_params())
    assert high.confidence is not None
    assert low.confidence is not None
    assert high.confidence < low.confidence


def test_missing_factor_contributes_zero_and_caps_confidence() -> None:
    inputs = ConfidenceInputs(
        f2=_fin(100),
        f4=_fin(100),
        f6=FactorInput(raw=Decimal("2.0")),
        f7=_fin(-100),
    )
    r = compute_confidence(inputs, default_params())
    assert r.confidence == Decimal("80.00")
    assert Factor.F3_CVD_DIVERGENCE not in r.used_factors
    f3c = next(c for c in r.contributions if c.factor is Factor.F3_CVD_DIVERGENCE)
    assert f3c.evaluable is False
    assert f3c.contribution == Decimal(0)


def test_empty_distribution_is_not_evaluable() -> None:
    inputs = ConfidenceInputs(
        f2=FactorInput(raw=Decimal(3), distribution=()),
        f4=_fin(100),
        f6=FactorInput(raw=Decimal("2.0")),
        f7=_fin(-100),
    )
    r = compute_confidence(inputs, default_params())
    assert Factor.F2_DELTA_EXHAUSTION not in r.used_factors


def test_all_absent_is_not_evaluable() -> None:
    r = compute_confidence(ConfidenceInputs(), default_params())
    assert r.confidence is None
    assert r.score is None
    assert r.used_factors == ()


def test_f6_cuts_paridad_v4() -> None:
    p = default_params()
    below = compute_confidence(ConfidenceInputs(f6=FactorInput(raw=Decimal("0.3"))), p)
    above = compute_confidence(ConfidenceInputs(f6=FactorInput(raw=Decimal("1.5"))), p)
    mid = compute_confidence(ConfidenceInputs(f6=FactorInput(raw=Decimal("0.9"))), p)
    assert below.confidence == Decimal("0.00")
    assert above.confidence == Decimal("20.00")
    assert mid.confidence == Decimal("10.00")


def test_percentile_midrank_equal_to_all() -> None:
    dist = (Decimal(2), Decimal(2), Decimal(2), Decimal(2))
    inputs = ConfidenceInputs(f2=FactorInput(raw=Decimal(2), distribution=dist))
    r = compute_confidence(inputs, default_params())
    assert r.confidence == Decimal("10.00")


def test_deterministic_same_inputs_same_result() -> None:
    inputs = ConfidenceInputs(
        f2=_fin(3),
        f3=_fin(4),
        f4=_fin(2),
        f6=FactorInput(raw=Decimal("1.1")),
        f7=_fin(2),
    )
    p = default_params()
    assert compute_confidence(inputs, p) == compute_confidence(inputs, p)


def test_params_reject_weights_not_summing_one() -> None:
    with pytest.raises(ValueError, match="suma de pesos"):
        ConfidenceParams(
            weights=((Factor.F2_DELTA_EXHAUSTION, Decimal("0.5")),),
            hvn_cut=Decimal("1.5"),
            lvn_cut=Decimal("0.3"),
            formula_version=1,
        )


def test_params_reject_bad_vp_cuts() -> None:
    with pytest.raises(ValueError, match="lvn_cut < hvn_cut"):
        ConfidenceParams(
            weights=((Factor.F2_DELTA_EXHAUSTION, Decimal(1)),),
            hvn_cut=Decimal("0.3"),
            lvn_cut=Decimal("0.3"),
            formula_version=1,
        )


def test_explainability_breakdown_present() -> None:
    inputs = ConfidenceInputs(f2=_fin(3), f6=FactorInput(raw=Decimal("1.0")))
    r = compute_confidence(inputs, default_params())
    factors = {c.factor for c in r.contributions}
    assert factors == {
        Factor.F2_DELTA_EXHAUSTION,
        Factor.F3_CVD_DIVERGENCE,
        Factor.F4_EFFORT_RESULT,
        Factor.F6_VP_CONTEXT,
        Factor.F7_VOID_NOTRADE,
    }
    assert r.score is not None
    assert r.confidence == (r.score * Decimal(100)).quantize(Decimal("0.01"))
