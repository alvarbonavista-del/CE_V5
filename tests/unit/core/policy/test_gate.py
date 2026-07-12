"""Unit tests del PolicyGate: enforcement, auditoria y D8 (ADR-012)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from ce_v5.core.policy import (
    CapabilityDecision,
    CapabilitySet,
    Decision,
    EvidenceSource,
    KycStatus,
    PolicyDegradedError,
    PolicyDenied,
    PolicyGate,
    PolicyInputs,
    ReasonCode,
    ResolvedJurisdiction,
    ResourceContext,
    SensitiveActionRecord,
    is_sensitive,
)

_SENS = "execute_order"
_NONSENS = "view_dashboard"


class _FakeEvaluator:
    """Evaluador doble: devuelve decisiones prefijadas o simula fallos."""

    def __init__(
        self,
        decisions: dict[str, CapabilityDecision],
        *,
        degrade: CapabilitySet | None = None,
        boom: Exception | None = None,
    ) -> None:
        self._decisions = decisions
        self._degrade = degrade
        self._boom = boom
        self.evaluate_calls = 0

    def evaluate(
        self,
        inputs: PolicyInputs,
        capability_ids: Sequence[str],
        resources: ResourceContext | None = None,
    ) -> CapabilitySet:
        self.evaluate_calls += 1
        if self._boom is not None:
            raise self._boom
        if self._degrade is not None:
            raise PolicyDegradedError(self._degrade)
        return CapabilitySet(
            tenant_id=inputs.subject_tenant_id,
            user_id=inputs.subject_user_id,
            policy_version="v1",
            evaluated_at=0,
            decisions={cap: self._decisions[cap] for cap in capability_ids},
        )


class _FakeAudit:
    """Auditoria doble: registra los intentos y puede fallar (D8)."""

    def __init__(self, *, boom: Exception | None = None) -> None:
        self._boom = boom
        self.records: list[SensitiveActionRecord] = []

    def record(self, entry: SensitiveActionRecord) -> None:
        self.records.append(entry)
        if self._boom is not None:
            raise self._boom


def _inputs() -> PolicyInputs:
    return PolicyInputs(
        subject_tenant_id="t1",
        subject_user_id="u1",
        jurisdiction=ResolvedJurisdiction("AA", EvidenceSource.KYC, conflicting=False),
        kyc_status=KycStatus.VERIFIED,
        vpn_detected=False,
        plan="plan_x",
        role=None,
    )


def _decision(
    cap: str,
    decision: Decision,
    reason: ReasonCode,
    *,
    kill_switch_id: str | None = None,
) -> CapabilityDecision:
    return CapabilityDecision(
        capability_id=cap,
        decision=decision,
        reason_code=reason,
        policy_version="v1",
        sensitive=is_sensitive(cap),
        kill_switch_id=kill_switch_id,
    )


def _set(decision: CapabilityDecision) -> CapabilitySet:
    return CapabilitySet(
        tenant_id="t1",
        user_id="u1",
        policy_version="v1",
        evaluated_at=0,
        decisions={decision.capability_id: decision},
    )


def _gate(evaluator: _FakeEvaluator, audit: _FakeAudit) -> PolicyGate:
    return PolicyGate(evaluator, audit)


def test_require_allow_sensible_devuelve_y_audita() -> None:
    decision = _decision(_SENS, Decision.ALLOW, ReasonCode.ALLOWED_BY_POLICY)
    audit = _FakeAudit()
    result = _gate(_FakeEvaluator({_SENS: decision}), audit).require(_inputs(), _SENS)
    assert result.decision is Decision.ALLOW
    assert len(audit.records) == 1
    assert audit.records[0].capability_id == _SENS
    assert audit.records[0].decision is Decision.ALLOW


def test_require_deny_sensible_lanza_y_audita_con_su_motivo() -> None:
    decision = _decision(
        _SENS, Decision.DENY, ReasonCode.DENIED_BY_KILL_SWITCH, kill_switch_id="ks-1"
    )
    audit = _FakeAudit()
    with pytest.raises(PolicyDenied) as excinfo:
        _gate(_FakeEvaluator({_SENS: decision}), audit).require(_inputs(), _SENS)
    assert excinfo.value.decision.reason_code is ReasonCode.DENIED_BY_KILL_SWITCH
    assert len(audit.records) == 1
    assert audit.records[0].reason_code is ReasonCode.DENIED_BY_KILL_SWITCH


def test_require_allow_no_sensible_no_audita() -> None:
    decision = _decision(_NONSENS, Decision.ALLOW, ReasonCode.ALLOWED_BY_POLICY)
    audit = _FakeAudit()
    result = _gate(_FakeEvaluator({_NONSENS: decision}), audit).require(
        _inputs(), _NONSENS
    )
    assert result.decision is Decision.ALLOW
    assert audit.records == []


def test_require_deny_no_sensible_lanza_sin_auditar() -> None:
    decision = _decision(_NONSENS, Decision.DENY, ReasonCode.DENIED_BY_PLAN)
    audit = _FakeAudit()
    with pytest.raises(PolicyDenied):
        _gate(_FakeEvaluator({_NONSENS: decision}), audit).require(_inputs(), _NONSENS)
    assert audit.records == []


def test_d8_fallo_de_auditoria_en_sensible_permitido_deniega() -> None:
    # EL CORAZON DE D8: la politica permitia, pero la auditoria fallo -> DENY.
    decision = _decision(_SENS, Decision.ALLOW, ReasonCode.ALLOWED_BY_POLICY)
    audit = _FakeAudit(boom=RuntimeError("auditoria caida"))
    with pytest.raises(PolicyDenied) as excinfo:
        _gate(_FakeEvaluator({_SENS: decision}), audit).require(_inputs(), _SENS)
    assert excinfo.value.decision.decision is Decision.DENY
    assert excinfo.value.decision.reason_code is ReasonCode.DENIED_AUDIT_UNAVAILABLE
    assert isinstance(excinfo.value.__cause__, RuntimeError)  # la causa no se traga
    assert len(audit.records) == 1  # se intento auditar


def test_require_ante_policy_degraded_usa_el_set_degradado() -> None:
    degraded = _set(_decision(_SENS, Decision.DENY, ReasonCode.DENIED_CACHE_STALE))
    audit = _FakeAudit()
    with pytest.raises(PolicyDenied) as excinfo:
        _gate(_FakeEvaluator({}, degrade=degraded), audit).require(_inputs(), _SENS)
    assert excinfo.value.decision.reason_code is ReasonCode.DENIED_CACHE_STALE
    assert isinstance(excinfo.value.__cause__, PolicyDegradedError)
    assert len(audit.records) == 1  # sensible DENY tambien se audita


def test_require_ante_excepcion_inesperada_deniega_not_recomputable() -> None:
    audit = _FakeAudit()
    evaluator = _FakeEvaluator({}, boom=RuntimeError("store caido inesperado"))
    with pytest.raises(PolicyDenied) as excinfo:
        _gate(evaluator, audit).require(_inputs(), _SENS)
    assert excinfo.value.decision.reason_code is ReasonCode.DENIED_NOT_RECOMPUTABLE
    assert isinstance(excinfo.value.__cause__, RuntimeError)  # no se filtra cruda
    assert len(audit.records) == 1


def test_capability_set_no_audita() -> None:
    caps = [_SENS, _NONSENS]
    decisions = {
        cap: _decision(cap, Decision.ALLOW, ReasonCode.ALLOWED_BY_POLICY)
        for cap in caps
    }
    audit = _FakeAudit()
    result = _gate(_FakeEvaluator(decisions), audit).capability_set(_inputs(), caps)
    assert set(result.decisions) == set(caps)
    assert audit.records == []


def test_capability_set_ante_policy_degraded_devuelve_set_degradado() -> None:
    degraded = _set(_decision(_SENS, Decision.DENY, ReasonCode.DENIED_CACHE_STALE))
    audit = _FakeAudit()
    result = _gate(_FakeEvaluator({}, degrade=degraded), audit).capability_set(
        _inputs(), [_SENS]
    )
    assert result is degraded
    assert result.decisions[_SENS].decision is Decision.DENY
    assert audit.records == []
