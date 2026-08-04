"""Tests del codec de PivotState (P08c P5 T4b): round-trip exacto, orden, fail-loud."""

from decimal import Decimal

import pytest

from ce_v5.platform.rules.pivotphase import PivotState
from ce_v5.platform.rules.pivotphase_state_codec import (
    parse_state,
    serialize_state,
)


def test_roundtrip_default_state() -> None:
    state = PivotState()
    assert parse_state(serialize_state(state)) == state


def test_roundtrip_populated_state() -> None:
    state = PivotState(
        phase=3,
        direction="bull",
        impulse_count=2,
        phase1_peak_delta=Decimal("1234.5"),
        phase2_level_price=Decimal("100.25"),
        phase2_level_type="vah",
        phase3_zone_price=Decimal("99.80"),
        phase3_zone_strength=Decimal("0.30"),
        exhaustion_count=1,
        flip_count=0,
        phase5_bars=4,
    )
    assert parse_state(serialize_state(state)) == state


def test_roundtrip_preserves_decimal_scale_and_sign() -> None:
    state = PivotState(
        phase1_peak_delta=Decimal("-0.500"),
        phase3_zone_strength=Decimal("0.0"),
    )
    restored = parse_state(serialize_state(state))
    # str(Decimal) conserva escala y signo -> round-trip exacto.
    assert restored.phase1_peak_delta == Decimal("-0.500")
    assert str(restored.phase1_peak_delta) == "-0.500"
    assert restored == state


def test_serialization_is_deterministic_and_ordered() -> None:
    text = serialize_state(PivotState(direction="bear", phase=1))
    lines = text.split("\n")
    # Orden alfabetico fijo: direction primero, phase5_bars ultimo.
    assert lines[0] == "direction=bear"
    assert lines[-1] == "phase5_bars=0"
    assert serialize_state(PivotState(direction="bear", phase=1)) == text


def test_parse_rejects_line_without_equals() -> None:
    with pytest.raises(ValueError, match="sin '='"):
        parse_state("direction=bull\nbasura\nphase=1")


def test_parse_rejects_missing_field() -> None:
    good = serialize_state(PivotState())
    truncated = "\n".join(good.split("\n")[:-1])  # quita phase5_bars
    with pytest.raises(ValueError, match="campos de PivotState invalidos"):
        parse_state(truncated)


def test_parse_rejects_extra_field() -> None:
    text = serialize_state(PivotState()) + "\nextra=1"
    with pytest.raises(ValueError, match="campos de PivotState invalidos"):
        parse_state(text)
