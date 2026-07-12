"""Unit tests del CachedPolicyEvaluator: cache y degradacion fail-closed."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from ce_v5.core.clock import SimulatedClock
from ce_v5.core.policy import (
    CachedPolicyEvaluator,
    CapabilityDecision,
    CapabilitySet,
    CapabilitySetCache,
    Decision,
    EvidenceSource,
    KycStatus,
    PolicyDegradedError,
    PolicyInputs,
    ReasonCode,
    ResolvedJurisdiction,
    ResourceContext,
    is_sensitive,
)

_SENS = "execute_order"
_NONSENS = "view_dashboard"


class _FakeEvaluator:
    """Evaluador doble: cuenta las recomputaciones y puede simular fallos."""

    def __init__(self, clock: SimulatedClock, version: str = "v1") -> None:
        self._clock = clock
        self.version: str | None = version
        self.evaluate_calls = 0
        self.fail_evaluate = False
        self.fail_version = False

    def current_policy_version(self) -> str | None:
        if self.fail_version:
            raise RuntimeError("store caido (version)")
        return self.version

    def evaluate(
        self,
        inputs: PolicyInputs,
        capability_ids: Sequence[str],
        resources: ResourceContext | None = None,
    ) -> CapabilitySet:
        self.evaluate_calls += 1
        if self.fail_evaluate:
            raise RuntimeError("store caido (evaluate)")
        decisions = {
            cap: CapabilityDecision(
                capability_id=cap,
                decision=Decision.ALLOW,
                reason_code=ReasonCode.ALLOWED_BY_POLICY,
                policy_version=self.version,
                sensitive=is_sensitive(cap),
                kill_switch_id=None,
            )
            for cap in capability_ids
        }
        return CapabilitySet(
            tenant_id=inputs.subject_tenant_id,
            user_id=inputs.subject_user_id,
            policy_version=self.version,
            evaluated_at=self._clock.now_ms(),
            decisions=decisions,
        )


def _inputs(tenant: str = "t1", user: str | None = "u1") -> PolicyInputs:
    return PolicyInputs(
        subject_tenant_id=tenant,
        subject_user_id=user,
        jurisdiction=ResolvedJurisdiction("AA", EvidenceSource.KYC, False),
        kyc_status=KycStatus.VERIFIED,
        vpn_detected=False,
        plan=None,
        role=None,
    )


def test_entrada_fresca_se_sirve_sin_recomputar() -> None:
    clock = SimulatedClock(start_ms=0)
    fake = _FakeEvaluator(clock)
    cached = CachedPolicyEvaluator(fake, CapabilitySetCache(clock, 1000))
    cached.evaluate(_inputs(), [_NONSENS])
    assert fake.evaluate_calls == 1
    result = cached.evaluate(_inputs(), [_NONSENS])
    assert fake.evaluate_calls == 1  # servido del cache
    assert result.decisions[_NONSENS].decision is Decision.ALLOW


def test_pregunta_por_otra_capability_recomputa_regresion_b9() -> None:
    # REGRESION B9: preguntar por [_SENS] y despues por [_NONSENS] con el MISMO
    # sujeto. Sin el fix, la segunda pregunta servia la entrada de la primera y
    # _NONSENS salia "no evaluada"; con el fix RECOMPUTA (2 llamadas) y devuelve la
    # decision REAL de _NONSENS. TTL largo: el recomputo es por la lista, no por TTL.
    clock = SimulatedClock(start_ms=0)
    fake = _FakeEvaluator(clock)
    cached = CachedPolicyEvaluator(fake, CapabilitySetCache(clock, 100000))
    cached.evaluate(_inputs(), [_SENS])
    assert fake.evaluate_calls == 1
    result = cached.evaluate(_inputs(), [_NONSENS])
    assert fake.evaluate_calls == 2  # es OTRA pregunta: recomputa
    assert result.decisions[_NONSENS].decision is Decision.ALLOW


def test_sensible_no_sale_denied_not_evaluated_por_consulta_previa() -> None:
    # REGRESION B9: consultar antes por _NONSENS no debe hacer que _SENS salga
    # DENY denied_not_evaluated (fail-closed roto): con el fix se recomputa.
    clock = SimulatedClock(start_ms=0)
    fake = _FakeEvaluator(clock)
    cached = CachedPolicyEvaluator(fake, CapabilitySetCache(clock, 100000))
    cached.evaluate(_inputs(), [_NONSENS])
    result = cached.evaluate(_inputs(), [_SENS])
    decision = result.decision_for(_SENS)
    assert decision.decision is Decision.ALLOW
    assert decision.reason_code is not ReasonCode.DENIED_NOT_EVALUATED


def test_misma_lista_en_otro_orden_reusa_la_entrada() -> None:
    # El digest es estable frente al orden y a los duplicados: la MISMA pregunta
    # reusa la entrada (no recomputa).
    clock = SimulatedClock(start_ms=0)
    fake = _FakeEvaluator(clock)
    cached = CachedPolicyEvaluator(fake, CapabilitySetCache(clock, 100000))
    cached.evaluate(_inputs(), [_SENS, _NONSENS])
    assert fake.evaluate_calls == 1
    cached.evaluate(_inputs(), [_NONSENS, _SENS, _SENS])
    assert fake.evaluate_calls == 1  # mismo conjunto: reusa


def test_pasado_max_staleness_se_recomputa() -> None:
    clock = SimulatedClock(start_ms=0)
    fake = _FakeEvaluator(clock)
    cached = CachedPolicyEvaluator(fake, CapabilitySetCache(clock, 1000))
    cached.evaluate(_inputs(), [_NONSENS])
    assert fake.evaluate_calls == 1
    clock.set(2000)
    cached.evaluate(_inputs(), [_NONSENS])
    assert fake.evaluate_calls == 2


def test_policy_version_antigua_no_se_sirve() -> None:
    clock = SimulatedClock(start_ms=0)
    fake = _FakeEvaluator(clock, version="v1")
    cached = CachedPolicyEvaluator(fake, CapabilitySetCache(clock, 100000))
    cached.evaluate(_inputs(), [_NONSENS])
    assert fake.evaluate_calls == 1
    # La version vigente cambia (evento de version_published perdido): la entrada
    # v1 sigue fresca, pero NO vale; se recomputa.
    fake.version = "v2"
    cached.evaluate(_inputs(), [_NONSENS])
    assert fake.evaluate_calls == 2


def test_fail_closed_sensible_sin_entrada_not_recomputable() -> None:
    clock = SimulatedClock()
    fake = _FakeEvaluator(clock)
    fake.fail_evaluate = True
    cached = CachedPolicyEvaluator(fake, CapabilitySetCache(clock, 1000))
    with pytest.raises(PolicyDegradedError) as excinfo:
        cached.evaluate(_inputs(), [_SENS])
    decision = excinfo.value.capability_set.decisions[_SENS]
    assert decision.decision is Decision.DENY
    assert decision.reason_code is ReasonCode.DENIED_NOT_RECOMPUTABLE


def test_fail_closed_sensible_con_stale_cache_stale() -> None:
    clock = SimulatedClock(start_ms=0)
    fake = _FakeEvaluator(clock)
    cache = CapabilitySetCache(clock, max_staleness_ms=1000)
    cached = CachedPolicyEvaluator(fake, cache)
    cached.evaluate(_inputs(), [_SENS])  # cachea en t=0
    clock.set(5000)
    fake.fail_evaluate = True
    with pytest.raises(PolicyDegradedError) as excinfo:
        cached.evaluate(_inputs(), [_SENS])
    decision = excinfo.value.capability_set.decisions[_SENS]
    assert decision.decision is Decision.DENY
    assert decision.reason_code is ReasonCode.DENIED_CACHE_STALE


def test_fail_closed_no_sensible_sin_degradar_not_applicable() -> None:
    clock = SimulatedClock(start_ms=0)
    fake = _FakeEvaluator(clock)
    cache = CapabilitySetCache(clock, max_staleness_ms=1000)
    cached = CachedPolicyEvaluator(fake, cache, degrade_non_sensitive_with_stale=False)
    cached.evaluate(_inputs(), [_NONSENS])
    clock.set(5000)
    fake.fail_evaluate = True
    with pytest.raises(PolicyDegradedError) as excinfo:
        cached.evaluate(_inputs(), [_NONSENS])
    decision = excinfo.value.capability_set.decisions[_NONSENS]
    assert decision.decision is Decision.NOT_APPLICABLE


def test_degradar_sirve_stale_no_sensible_pero_sensible_sigue_deny() -> None:
    clock = SimulatedClock(start_ms=0)
    fake = _FakeEvaluator(clock)
    cache = CapabilitySetCache(clock, max_staleness_ms=1000)
    cached = CachedPolicyEvaluator(fake, cache, degrade_non_sensitive_with_stale=True)
    cached.evaluate(_inputs(), [_NONSENS, _SENS])
    clock.set(5000)
    fake.fail_evaluate = True
    with pytest.raises(PolicyDegradedError) as excinfo:
        cached.evaluate(_inputs(), [_NONSENS, _SENS])
    degraded = excinfo.value.capability_set
    # No sensible: se sirve la stale (ALLOW). Sensible: DENY, jamas degrada.
    assert degraded.decisions[_NONSENS].decision is Decision.ALLOW
    assert degraded.decisions[_SENS].decision is Decision.DENY
    assert degraded.decisions[_SENS].reason_code is ReasonCode.DENIED_CACHE_STALE


def test_version_no_recomputable_sensible_deny() -> None:
    clock = SimulatedClock()
    fake = _FakeEvaluator(clock)
    fake.fail_version = True
    cached = CachedPolicyEvaluator(fake, CapabilitySetCache(clock, 1000))
    with pytest.raises(PolicyDegradedError) as excinfo:
        cached.evaluate(_inputs(), [_SENS])
    assert excinfo.value.capability_set.decisions[_SENS].decision is Decision.DENY
    assert fake.evaluate_calls == 0  # no llego a recomputar


def test_el_fallo_no_se_pierde() -> None:
    clock = SimulatedClock()
    fake = _FakeEvaluator(clock)
    fake.fail_evaluate = True
    cached = CachedPolicyEvaluator(fake, CapabilitySetCache(clock, 1000))
    with pytest.raises(PolicyDegradedError) as excinfo:
        cached.evaluate(_inputs(), [_SENS])
    # El gate (B8) puede auditar la causa y aplicar el set degradado.
    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert excinfo.value.capability_set.decisions[_SENS].decision is Decision.DENY
