"""Tests del modelo pivotphase.confidence (P08c-P4): inputs inyectados y deterministas.

F6 por DISTANCIA a niveles VP (ELEVACION P08c-PIVOT-05); resto por percentil.
"""

from decimal import Decimal

import pytest

from ce_v5.platform.rules.pivotphase_confidence import (
    ConfidenceInputs,
    ConfidenceParams,
    Factor,
    FactorInput,
    VpContextInput,
    compute_confidence,
    default_params,
)

_DIST = (Decimal(1), Decimal(2), Decimal(3), Decimal(4), Decimal(5))
# F6 en el HVN -> soporte pleno (f6=1): precio en vp.hvn, vp.lvn lejos.
_F6_HVN = VpContextInput(
    price=Decimal(100), hvn_price=Decimal(100), lvn_price=Decimal(50)
)
# F6 en el LVN -> sin soporte (f6=0): precio en vp.lvn, vp.hvn lejos.
_F6_LVN = VpContextInput(
    price=Decimal(100), hvn_price=Decimal(150), lvn_price=Decimal(100)
)


def _fin(raw: object, dist: tuple[Decimal, ...] = _DIST) -> FactorInput:
    return FactorInput(raw=Decimal(str(raw)), distribution=dist)


def test_default_params_active_weights_sum_to_one() -> None:
    total = sum((w for _, w in default_params().weights), Decimal(0))
    assert total == Decimal(1)


def test_default_params_formula_version_is_2() -> None:
    assert default_params().formula_version == 2


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
        f6=_F6_HVN,
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
        f6=_F6_LVN,
        f7=_fin(100),
    )
    r = compute_confidence(inputs, default_params())
    assert r.confidence == Decimal("0.00")


def test_f7_penalizes_higher_void_lowers_confidence() -> None:
    low = compute_confidence(
        ConfidenceInputs(f2=_fin(3), f3=_fin(3), f4=_fin(3), f6=_F6_HVN, f7=_fin(1)),
        default_params(),
    )
    high = compute_confidence(
        ConfidenceInputs(f2=_fin(3), f3=_fin(3), f4=_fin(3), f6=_F6_HVN, f7=_fin(5)),
        default_params(),
    )
    assert high.confidence is not None
    assert low.confidence is not None
    assert high.confidence < low.confidence


def test_missing_factor_contributes_zero_and_caps_confidence() -> None:
    inputs = ConfidenceInputs(f2=_fin(100), f4=_fin(100), f6=_F6_HVN, f7=_fin(-100))
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
        f6=_F6_HVN,
        f7=_fin(-100),
    )
    r = compute_confidence(inputs, default_params())
    assert Factor.F2_DELTA_EXHAUSTION not in r.used_factors


def test_all_absent_is_not_evaluable() -> None:
    r = compute_confidence(ConfidenceInputs(), default_params())
    assert r.confidence is None
    assert r.score is None
    assert r.used_factors == ()


def test_f6_distance_hvn_lvn_equidistant() -> None:
    p = default_params()
    # solo F6 evaluable -> confidence = 0.2 * f6 * 100 = 20 * f6.
    at_hvn = compute_confidence(ConfidenceInputs(f6=_F6_HVN), p)
    at_lvn = compute_confidence(ConfidenceInputs(f6=_F6_LVN), p)
    equi = VpContextInput(
        price=Decimal(100), hvn_price=Decimal(110), lvn_price=Decimal(90)
    )
    at_equi = compute_confidence(ConfidenceInputs(f6=equi), p)
    assert at_hvn.confidence == Decimal("20.00")  # f6=1
    assert at_lvn.confidence == Decimal("0.00")  # f6=0
    assert at_equi.confidence == Decimal("10.00")  # f6=0.5


def test_f6_degenerate_is_not_evaluable() -> None:
    # price<=0 o ambos niveles en el precio -> F6 no evaluable.
    degenerate = VpContextInput(
        price=Decimal(100), hvn_price=Decimal(100), lvn_price=Decimal(100)
    )
    r = compute_confidence(ConfidenceInputs(f6=degenerate), default_params())
    assert Factor.F6_VP_CONTEXT not in r.used_factors
    assert r.confidence is None


def test_percentile_midrank_equal_to_all() -> None:
    dist = (Decimal(2), Decimal(2), Decimal(2), Decimal(2))
    inputs = ConfidenceInputs(f2=FactorInput(raw=Decimal(2), distribution=dist))
    r = compute_confidence(inputs, default_params())
    assert r.confidence == Decimal("10.00")


def test_deterministic_same_inputs_same_result() -> None:
    inputs = ConfidenceInputs(
        f2=_fin(3), f3=_fin(4), f4=_fin(2), f6=_F6_HVN, f7=_fin(2)
    )
    p = default_params()
    assert compute_confidence(inputs, p) == compute_confidence(inputs, p)


def test_params_reject_weights_not_summing_one() -> None:
    with pytest.raises(ValueError, match="suma de pesos"):
        ConfidenceParams(
            weights=((Factor.F2_DELTA_EXHAUSTION, Decimal("0.5")),),
            formula_version=2,
        )


def test_explainability_breakdown_present() -> None:
    inputs = ConfidenceInputs(f2=_fin(3), f6=_F6_HVN)
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
