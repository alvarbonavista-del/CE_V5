"""Unit tests del PolicyEvaluator: DENY > ALLOW y fail-closed (ADR-012).

El PolicyStore es un doble en memoria escrito aqui; el nucleo no lo conoce.
"""

from __future__ import annotations

from collections.abc import Sequence

from ce_v5.core.clock import Clock, SimulatedClock
from ce_v5.core.policy import (
    CapabilitySet,
    Decision,
    EntitlementRecord,
    EvidenceSource,
    KillSwitchRecord,
    KycStatus,
    OverrideRecord,
    PolicyEvaluator,
    PolicyInputs,
    PolicyRuleRecord,
    ReasonCode,
    ResolvedJurisdiction,
    ResourceContext,
)

_SENS = "execute_order"  # sensible (lista cerrada de B1)
_NONSENS = "view_dashboard"  # de catalogo, no sensible


class _MemStore:
    """PolicyStore en memoria; devuelve lo prefijado sin filtrar por sujeto."""

    def __init__(
        self,
        *,
        policy_version: str | None = "v1",
        rules: Sequence[PolicyRuleRecord] = (),
        entitlements: Sequence[EntitlementRecord] = (),
        overrides: Sequence[OverrideRecord] = (),
        kill_switches: Sequence[KillSwitchRecord] = (),
    ) -> None:
        self._policy_version = policy_version
        self._rules = list(rules)
        self._entitlements = list(entitlements)
        self._overrides = list(overrides)
        self._kill_switches = list(kill_switches)

    def current_policy_version(self) -> str | None:
        return self._policy_version

    def rules(self, policy_version: str) -> Sequence[PolicyRuleRecord]:
        return self._rules

    def entitlements(
        self, tenant_id: str, user_id: str | None
    ) -> Sequence[EntitlementRecord]:
        return self._entitlements

    def overrides(
        self, tenant_id: str, user_id: str | None
    ) -> Sequence[OverrideRecord]:
        return self._overrides

    def active_kill_switches(self) -> Sequence[KillSwitchRecord]:
        return self._kill_switches


def _inputs(
    *,
    tenant_id: str = "t1",
    user_id: str | None = "u1",
    jurisdiction: str | None = "ES",
    kyc: KycStatus = KycStatus.VERIFIED,
    vpn: bool | None = False,
    plan: str | None = None,
    role: str | None = None,
) -> PolicyInputs:
    resolved = ResolvedJurisdiction(
        jurisdiction=jurisdiction,
        source=EvidenceSource.KYC if jurisdiction is not None else None,
        conflicting=False,
    )
    return PolicyInputs(
        subject_tenant_id=tenant_id,
        subject_user_id=user_id,
        jurisdiction=resolved,
        kyc_status=kyc,
        vpn_detected=vpn,
        plan=plan,
        role=role,
    )


def _rule(
    cap: str,
    effect: str,
    reason_code: str,
    *,
    jurisdiction: str | None = None,
    plan: str | None = None,
    role: str | None = None,
    kyc: str | None = None,
    vpn: bool | None = None,
) -> PolicyRuleRecord:
    return PolicyRuleRecord(
        rule_id=f"r-{cap}-{effect}",
        capability_id=cap,
        effect=effect,
        reason_code=reason_code,
        match_jurisdiction=jurisdiction,
        match_plan=plan,
        match_role=role,
        match_kyc_status=kyc,
        match_vpn=vpn,
    )


def _ent(cap: str, *, expires_at: int | None = None) -> EntitlementRecord:
    return EntitlementRecord(capability_id=cap, source="plan", expires_at=expires_at)


def _ovr(
    cap: str, effect: str, reason_code: str, *, expires_at: int | None = None
) -> OverrideRecord:
    return OverrideRecord(
        capability_id=cap,
        effect=effect,
        reason_code=reason_code,
        expires_at=expires_at,
    )


def _ks(
    ks_id: str,
    scope: str,
    *,
    target_ref: str | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> KillSwitchRecord:
    return KillSwitchRecord(
        kill_switch_id=ks_id,
        scope=scope,
        target_ref=target_ref,
        tenant_id=tenant_id,
        user_id=user_id,
    )


def _evaluate(
    store: _MemStore,
    inputs: PolicyInputs,
    caps: Sequence[str],
    *,
    resources: ResourceContext | None = None,
    clock: Clock | None = None,
) -> CapabilitySet:
    engine = PolicyEvaluator(store, clock if clock is not None else SimulatedClock())
    return engine.evaluate(inputs, caps, resources)


_ALLOW = ReasonCode.ALLOWED_BY_POLICY.value


def test_sin_policy_version_deny_unavailable() -> None:
    result = _evaluate(_MemStore(policy_version=None), _inputs(), [_SENS, _NONSENS])
    assert result.policy_version is None
    for cap in (_SENS, _NONSENS):
        decision = result.decisions[cap]
        assert decision.decision is Decision.DENY
        assert decision.reason_code is ReasonCode.DENIED_POLICY_UNAVAILABLE


def test_kill_switch_global_apaga_allow() -> None:
    rules = [_rule(_NONSENS, "allow", _ALLOW)]
    base = _evaluate(_MemStore(rules=rules), _inputs(), [_NONSENS])
    assert base.decisions[_NONSENS].decision is Decision.ALLOW

    store = _MemStore(rules=rules, kill_switches=[_ks("ks1", "global")])
    decision = _evaluate(store, _inputs(), [_NONSENS]).decisions[_NONSENS]
    assert decision.decision is Decision.DENY
    assert decision.reason_code is ReasonCode.DENIED_BY_KILL_SWITCH
    assert decision.kill_switch_id == "ks1"


def test_kill_switch_de_capability_solo_esa() -> None:
    store = _MemStore(
        rules=[_rule("cap_a", "allow", _ALLOW), _rule("cap_b", "allow", _ALLOW)],
        kill_switches=[_ks("ks1", "capability", target_ref="cap_a")],
    )
    result = _evaluate(store, _inputs(), ["cap_a", "cap_b"])
    assert result.decisions["cap_a"].decision is Decision.DENY
    assert result.decisions["cap_a"].reason_code is ReasonCode.DENIED_BY_KILL_SWITCH
    assert result.decisions["cap_b"].decision is Decision.ALLOW


def test_kill_switch_de_tenant_solo_al_objetivo() -> None:
    store = _MemStore(
        rules=[_rule(_NONSENS, "allow", _ALLOW)],
        kill_switches=[_ks("ks1", "tenant", tenant_id="t1")],
    )
    objetivo = _evaluate(store, _inputs(tenant_id="t1"), [_NONSENS]).decisions[_NONSENS]
    otro = _evaluate(store, _inputs(tenant_id="t2"), [_NONSENS]).decisions[_NONSENS]
    assert objetivo.decision is Decision.DENY
    assert otro.decision is Decision.ALLOW


def test_kill_switch_de_exchange_asimetria_ui_backend() -> None:
    store = _MemStore(
        rules=[_rule(_NONSENS, "allow", _ALLOW)],
        kill_switches=[_ks("ks1", "exchange", target_ref="binance")],
    )
    # Sin recurso: la UI no sabe el exchange, el switch NO puede morder.
    sin_recurso = _evaluate(store, _inputs(), [_NONSENS]).decisions[_NONSENS]
    assert sin_recurso.decision is Decision.ALLOW
    # Con el recurso (punto sensible del backend): el switch SI muerde.
    con_recurso = _evaluate(
        store, _inputs(), [_NONSENS], resources=ResourceContext(exchange="binance")
    ).decisions[_NONSENS]
    assert con_recurso.decision is Decision.DENY
    assert con_recurso.reason_code is ReasonCode.DENIED_BY_KILL_SWITCH


def test_sensible_jurisdiccion_desconocida_deny() -> None:
    store = _MemStore(rules=[_rule(_SENS, "allow", _ALLOW)])
    decision = _evaluate(store, _inputs(jurisdiction=None), [_SENS]).decisions[_SENS]
    assert decision.decision is Decision.DENY
    assert decision.reason_code is ReasonCode.DENIED_BY_JURISDICTION


def test_sensible_vpn_desconocida_deny() -> None:
    store = _MemStore(rules=[_rule(_SENS, "allow", _ALLOW)])
    decision = _evaluate(store, _inputs(vpn=None), [_SENS]).decisions[_SENS]
    assert decision.decision is Decision.DENY
    assert decision.reason_code is ReasonCode.DENIED_BY_VPN


def test_sensible_entitlement_ausente_deny_presente_allow() -> None:
    rules = [_rule(_SENS, "allow", _ALLOW)]
    ausente = _evaluate(_MemStore(rules=rules), _inputs(), [_SENS]).decisions[_SENS]
    assert ausente.decision is Decision.DENY
    assert ausente.reason_code is ReasonCode.DENIED_BY_MISSING_ENTITLEMENT

    store = _MemStore(rules=rules, entitlements=[_ent(_SENS)])
    presente = _evaluate(store, _inputs(), [_SENS]).decisions[_SENS]
    assert presente.decision is Decision.ALLOW
    assert presente.reason_code is ReasonCode.ALLOWED_BY_POLICY


def test_entitlement_caducado_en_sensible_deny() -> None:
    rules = [_rule(_SENS, "allow", _ALLOW)]
    clock = SimulatedClock(start_ms=1000)
    caducado = _MemStore(rules=rules, entitlements=[_ent(_SENS, expires_at=500)])
    vencido = _evaluate(caducado, _inputs(), [_SENS], clock=clock).decisions[_SENS]
    assert vencido.decision is Decision.DENY
    assert vencido.reason_code is ReasonCode.DENIED_BY_MISSING_ENTITLEMENT

    vigente = _MemStore(rules=rules, entitlements=[_ent(_SENS, expires_at=2000)])
    ok = _evaluate(vigente, _inputs(), [_SENS], clock=clock).decisions[_SENS]
    assert ok.decision is Decision.ALLOW


def test_regla_deny_vence_a_allow() -> None:
    store = _MemStore(
        rules=[
            _rule(_NONSENS, "allow", _ALLOW),
            _rule(_NONSENS, "deny", ReasonCode.DENIED_BY_PLAN.value),
        ]
    )
    decision = _evaluate(store, _inputs(), [_NONSENS]).decisions[_NONSENS]
    assert decision.decision is Decision.DENY
    assert decision.reason_code is ReasonCode.DENIED_BY_PLAN


def test_regla_con_criterio_y_entrada_desconocida_no_encaja() -> None:
    # La regla ALLOW exige jurisdiccion ES; con entrada None NO encaja, y una
    # capability no sensible sin regla que encaje es NOT_APPLICABLE, no ALLOW.
    store = _MemStore(rules=[_rule(_NONSENS, "allow", _ALLOW, jurisdiction="ES")])
    decision = _evaluate(store, _inputs(jurisdiction=None), [_NONSENS]).decisions[
        _NONSENS
    ]
    assert decision.decision is Decision.NOT_APPLICABLE


def test_override_deny_convierte_allow_en_deny() -> None:
    store = _MemStore(
        rules=[_rule(_NONSENS, "allow", _ALLOW)],
        overrides=[_ovr(_NONSENS, "deny", ReasonCode.DENIED_BY_OVERRIDE.value)],
    )
    decision = _evaluate(store, _inputs(), [_NONSENS]).decisions[_NONSENS]
    assert decision.decision is Decision.DENY
    assert decision.reason_code is ReasonCode.DENIED_BY_OVERRIDE


def test_override_allow_no_levanta_deny_de_kill_switch() -> None:
    store = _MemStore(
        rules=[_rule(_NONSENS, "allow", _ALLOW)],
        kill_switches=[_ks("ks1", "global")],
        overrides=[_ovr(_NONSENS, "allow", ReasonCode.ALLOWED_BY_OVERRIDE.value)],
    )
    decision = _evaluate(store, _inputs(), [_NONSENS]).decisions[_NONSENS]
    assert decision.decision is Decision.DENY
    assert decision.reason_code is ReasonCode.DENIED_BY_KILL_SWITCH


def test_override_allow_no_levanta_deny_de_jurisdiccion() -> None:
    store = _MemStore(
        rules=[_rule(_SENS, "allow", _ALLOW)],
        overrides=[_ovr(_SENS, "allow", ReasonCode.ALLOWED_BY_OVERRIDE.value)],
    )
    decision = _evaluate(store, _inputs(jurisdiction=None), [_SENS]).decisions[_SENS]
    assert decision.decision is Decision.DENY
    assert decision.reason_code is ReasonCode.DENIED_BY_JURISDICTION


def test_override_allow_no_levanta_deny_de_plan() -> None:
    store = _MemStore(
        rules=[_rule(_NONSENS, "deny", ReasonCode.DENIED_BY_PLAN.value, plan="free")],
        overrides=[_ovr(_NONSENS, "allow", ReasonCode.ALLOWED_BY_OVERRIDE.value)],
    )
    decision = _evaluate(store, _inputs(plan="free"), [_NONSENS]).decisions[_NONSENS]
    assert decision.decision is Decision.DENY
    assert decision.reason_code is ReasonCode.DENIED_BY_PLAN


def test_override_allow_no_levanta_deny_de_entitlement_ausente() -> None:
    store = _MemStore(
        rules=[_rule(_SENS, "allow", _ALLOW)],
        overrides=[_ovr(_SENS, "allow", ReasonCode.ALLOWED_BY_OVERRIDE.value)],
    )
    decision = _evaluate(store, _inputs(), [_SENS]).decisions[_SENS]
    assert decision.decision is Decision.DENY
    assert decision.reason_code is ReasonCode.DENIED_BY_MISSING_ENTITLEMENT


def test_no_sensible_sin_regla_not_applicable() -> None:
    decision = _evaluate(_MemStore(), _inputs(), [_NONSENS]).decisions[_NONSENS]
    assert decision.decision is Decision.NOT_APPLICABLE
    assert decision.reason_code is ReasonCode.NOT_APPLICABLE_UNKNOWN_CAPABILITY


def test_decision_for_capability_no_evaluada() -> None:
    result = _evaluate(_MemStore(), _inputs(), [])
    sensible = result.decision_for(_SENS)
    assert sensible.decision is Decision.DENY
    assert sensible.reason_code is ReasonCode.DENIED_NOT_EVALUATED
    no_sensible = result.decision_for(_NONSENS)
    assert no_sensible.decision is Decision.NOT_APPLICABLE
    assert no_sensible.reason_code is ReasonCode.NOT_APPLICABLE_UNKNOWN_CAPABILITY
