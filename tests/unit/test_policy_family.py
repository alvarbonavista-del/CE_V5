"""Unit tests de la familia policy.* (ADR-021, ADR-012)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from source.families import Family, validate_event_type
from source.families.policy import (
    InvalidationReason,
    KillSwitchPayload,
    KillSwitchScope,
    PolicyEventType,
    SubjectInvalidatedPayload,
)


def test_family_incluye_policy() -> None:
    assert Family.POLICY.value == "policy"
    assert "policy" in {f.value for f in Family}


def test_event_type_acepta_los_cuatro_policy() -> None:
    for event_type in PolicyEventType:
        assert validate_event_type(event_type.value) == event_type.value


def test_event_type_rechaza_familia_inexistente() -> None:
    with pytest.raises(ValueError):
        validate_event_type("politica.kill_switch_activated")


def _kill_switch(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "kill_switch_id": "ks-1",
        "reason_code": "manual",
        "policy_version": "2026.07.0",
        "actor": "admin",
    }
    base.update(overrides)
    return base


def test_kill_switch_global_valido() -> None:
    payload = KillSwitchPayload(**_kill_switch(scope=KillSwitchScope.GLOBAL))
    assert payload.scope is KillSwitchScope.GLOBAL
    assert payload.target_ref is None


@pytest.mark.parametrize(
    "scope",
    [
        KillSwitchScope.EXCHANGE,
        KillSwitchScope.CONNECTOR,
        KillSwitchScope.MARKET_SCOPE,
        KillSwitchScope.CAPABILITY,
    ],
)
def test_kill_switch_con_objetivo_valido(scope: KillSwitchScope) -> None:
    payload = KillSwitchPayload(**_kill_switch(scope=scope, target_ref="obj-1"))
    assert payload.scope is scope
    assert payload.target_ref == "obj-1"


def test_kill_switch_tenant_valido() -> None:
    payload = KillSwitchPayload(
        **_kill_switch(scope=KillSwitchScope.TENANT, tenant_id="t-1")
    )
    assert payload.tenant_id == "t-1"
    assert payload.user_id is None


def test_kill_switch_user_valido() -> None:
    payload = KillSwitchPayload(
        **_kill_switch(scope=KillSwitchScope.USER, tenant_id="t-1", user_id="u-1")
    )
    assert payload.tenant_id == "t-1"
    assert payload.user_id == "u-1"


def test_kill_switch_global_con_target_ref_falla() -> None:
    with pytest.raises(ValidationError):
        KillSwitchPayload(**_kill_switch(scope=KillSwitchScope.GLOBAL, target_ref="x"))


def test_kill_switch_exchange_sin_target_ref_falla() -> None:
    with pytest.raises(ValidationError):
        KillSwitchPayload(**_kill_switch(scope=KillSwitchScope.EXCHANGE))


def test_kill_switch_tenant_sin_tenant_id_falla() -> None:
    with pytest.raises(ValidationError):
        KillSwitchPayload(**_kill_switch(scope=KillSwitchScope.TENANT))


def test_kill_switch_user_sin_user_id_falla() -> None:
    with pytest.raises(ValidationError):
        KillSwitchPayload(**_kill_switch(scope=KillSwitchScope.USER, tenant_id="t-1"))


def test_kill_switch_user_con_target_ref_falla() -> None:
    with pytest.raises(ValidationError):
        KillSwitchPayload(
            **_kill_switch(
                scope=KillSwitchScope.USER,
                tenant_id="t-1",
                user_id="u-1",
                target_ref="x",
            )
        )


def test_subject_invalidated_con_user_id() -> None:
    payload = SubjectInvalidatedPayload(
        tenant_id="t-1",
        reason=InvalidationReason.ROLE_CHANGED,
        policy_version="2026.07.0",
        user_id="u-1",
    )
    assert payload.user_id == "u-1"


def test_subject_invalidated_sin_user_id() -> None:
    payload = SubjectInvalidatedPayload(
        tenant_id="t-1",
        reason=InvalidationReason.PLAN_CHANGED,
        policy_version="2026.07.0",
    )
    assert payload.user_id is None
