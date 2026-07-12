"""Unit tests del PolicyLifecycleGate: asimetria global vs sujeto (P06-B8b)."""

from __future__ import annotations

from collections.abc import Sequence

from ce_v5.core.component import LifecycleGateRequest
from ce_v5.core.policy import (
    CapabilityDecision,
    CapabilitySet,
    Decision,
    EvidenceSource,
    KillSwitchRecord,
    KycStatus,
    PolicyInputs,
    PolicyLifecycleGate,
    ReasonCode,
    ResolvedJurisdiction,
    ResourceContext,
)
from source.families.component import LifecycleScope

_CAP = "execute_order"


class _FakeEvaluator:
    def __init__(self, decisions: dict[str, CapabilityDecision]) -> None:
        self._decisions = decisions
        self.calls = 0

    def evaluate(
        self,
        inputs: PolicyInputs,
        capability_ids: Sequence[str],
        resources: ResourceContext | None = None,
    ) -> CapabilitySet:
        self.calls += 1
        return CapabilitySet(
            tenant_id=inputs.subject_tenant_id,
            user_id=inputs.subject_user_id,
            policy_version="v1",
            evaluated_at=0,
            decisions={cap: self._decisions[cap] for cap in capability_ids},
        )


class _FakeKillSwitches:
    def __init__(self, switches: Sequence[KillSwitchRecord]) -> None:
        self._switches = switches

    def active_kill_switches(self) -> Sequence[KillSwitchRecord]:
        return self._switches


class _FakeResolver:
    def __init__(self) -> None:
        self.called = False

    def resolve(self, tenant_id: str, user_id: str | None) -> PolicyInputs:
        self.called = True
        return PolicyInputs(
            subject_tenant_id=tenant_id,
            subject_user_id=user_id,
            jurisdiction=ResolvedJurisdiction(
                "AA", EvidenceSource.KYC, conflicting=False
            ),
            kyc_status=KycStatus.VERIFIED,
            vpn_detected=False,
            plan="plan_x",
            role=None,
        )


def _decision(decision: Decision, reason: ReasonCode) -> CapabilityDecision:
    return CapabilityDecision(
        capability_id=_CAP,
        decision=decision,
        reason_code=reason,
        policy_version="v1",
        sensitive=True,
        kill_switch_id=None,
    )


def _gate(
    *,
    decisions: dict[str, CapabilityDecision] | None = None,
    switches: Sequence[KillSwitchRecord] = (),
    resolver: _FakeResolver | None = None,
) -> tuple[PolicyLifecycleGate, _FakeResolver]:
    res = resolver or _FakeResolver()
    gate = PolicyLifecycleGate(
        _FakeEvaluator(decisions or {}), _FakeKillSwitches(switches), res
    )
    return gate, res


def _global(caps: tuple[str, ...] = (_CAP,)) -> LifecycleGateRequest:
    return LifecycleGateRequest(
        scope=LifecycleScope.GLOBAL,
        tenant_id=None,
        user_id=None,
        required_capabilities=caps,
        critical=False,
    )


def _user(caps: tuple[str, ...] = (_CAP,)) -> LifecycleGateRequest:
    return LifecycleGateRequest(
        scope=LifecycleScope.USER,
        tenant_id="t1",
        user_id="u1",
        required_capabilities=caps,
        critical=False,
    )


# --- Instancia GLOBAL: sin sujeto, solo kill switches ------------------------


def test_global_sin_switches_permite_y_no_llama_al_resolver() -> None:
    gate, resolver = _gate()
    verdict = gate.check_initialize(_global())
    assert verdict.allowed is True
    assert resolver.called is False  # una instancia global NO tiene sujeto


def test_global_con_kill_switch_global_deniega() -> None:
    switch = KillSwitchRecord("ks", "global", None, None, None)
    gate, resolver = _gate(switches=(switch,))
    verdict = gate.check_initialize(_global())
    assert verdict.allowed is False
    assert verdict.reason_code == ReasonCode.DENIED_BY_KILL_SWITCH.value
    assert resolver.called is False


def test_global_con_kill_switch_de_capacidad_que_apunta_deniega() -> None:
    switch = KillSwitchRecord("ks", "capability", _CAP, None, None)
    gate, _ = _gate(switches=(switch,))
    assert gate.check_initialize(_global()).allowed is False


def test_global_con_kill_switch_de_capacidad_que_no_apunta_permite() -> None:
    switch = KillSwitchRecord("ks", "capability", "otra_cap", None, None)
    gate, _ = _gate(switches=(switch,))
    assert gate.check_initialize(_global()).allowed is True


def test_global_ignora_kill_switch_de_tenant() -> None:
    # Un switch de tenant no puede morder algo sin tenant (instancia global).
    switch = KillSwitchRecord("ks", "tenant", None, "t1", None)
    gate, _ = _gate(switches=(switch,))
    assert gate.check_initialize(_global()).allowed is True


# --- Instancia con SUJETO: evaluacion real -----------------------------------


def test_sujeto_allow_permite_y_usa_el_resolver() -> None:
    decisions = {_CAP: _decision(Decision.ALLOW, ReasonCode.ALLOWED_BY_POLICY)}
    gate, resolver = _gate(decisions=decisions)
    verdict = gate.check_initialize(_user())
    assert verdict.allowed is True
    assert resolver.called is True


def test_sujeto_deny_deniega_con_su_reason_code() -> None:
    decisions = {_CAP: _decision(Decision.DENY, ReasonCode.DENIED_BY_PLAN)}
    gate, _ = _gate(decisions=decisions)
    verdict = gate.check_initialize(_user())
    assert verdict.allowed is False
    assert verdict.reason_code == ReasonCode.DENIED_BY_PLAN.value


def test_sujeto_sin_capacidades_permite() -> None:
    gate, resolver = _gate()
    verdict = gate.check_initialize(_user(caps=()))
    assert verdict.allowed is True
    assert resolver.called is True
