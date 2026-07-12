"""PolicyEvaluator: resolucion de capacidades fail-closed (ADR-012).

DENY > ALLOW y fail-closed por construccion. El motor DECIDE; no escribe
auditoria (B7) ni cachea (B5). La decision autoritativa se reevalua en el
punto sensible del backend; el capability set que consume la UI es INFORMATIVO.

D7 (FAIL-LOUD ante datos de politica invalidos): el motor no interpreta ni
ignora datos malformados. El store (B4b) los VALIDA al leer y lanza si una
regla trae un effect o un reason_code fuera del catalogo; el gate (B8)
convertira cualquier excepcion del motor en DENY con auditoria. Una regla mal
escrita DENIEGA y se nota; jamas concede.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ce_v5.core.clock import Clock
from ce_v5.core.policy.capabilities import is_sensitive
from ce_v5.core.policy.decisions import Decision, ReasonCode
from ce_v5.core.policy.inputs import KycStatus, PolicyInputs
from ce_v5.core.policy.ports import (
    EntitlementRecord,
    KillSwitchRecord,
    OverrideRecord,
    PolicyRuleRecord,
    PolicyStore,
)

_RESOURCE_SCOPES = ("exchange", "connector", "market_scope")


@dataclass(frozen=True, slots=True)
class ResourceContext:
    """Recurso concreto de la pregunta (exchange/connector/market_scope).

    ASIMETRIA UI/backend (ADR-012): si el llamador NO aporta, por ejemplo, el
    exchange, un kill switch de scope=exchange NO puede aplicarse a esa
    pregunta. Por eso el capability set que consume la UI es INFORMATIVO
    (cortesia: oculta o deshabilita), y la decision AUTORITATIVA es SIEMPRE la
    reevaluacion en el punto sensible del backend, donde el recurso SI se conoce
    y el switch de exchange si puede morder.
    """

    exchange: str | None = None
    connector: str | None = None
    market_scope: str | None = None


@dataclass(frozen=True, slots=True)
class CapabilityDecision:
    """Decision para una capability, siempre con motivo (ADR-012)."""

    capability_id: str
    decision: Decision
    reason_code: ReasonCode
    policy_version: str | None
    sensitive: bool
    kill_switch_id: str | None


@dataclass(frozen=True, slots=True)
class CapabilitySet:
    """Conjunto de decisiones evaluadas para un sujeto en un instante."""

    tenant_id: str
    user_id: str | None
    policy_version: str | None
    evaluated_at: int
    decisions: Mapping[str, CapabilityDecision]

    def decision_for(self, capability_id: str) -> CapabilityDecision:
        """Decision de una capability. Si NO fue evaluada, fail-closed.

        Una capability ausente del conjunto no lanza KeyError: devuelve DENY si
        es sensible (nunca se concede por omision) y NOT_APPLICABLE si no lo es.
        """
        existing = self.decisions.get(capability_id)
        if existing is not None:
            return existing
        sensitive = is_sensitive(capability_id)
        # No fue evaluada: fail-closed, y el motivo COINCIDE con la decision.
        # Sensible -> DENY denied_not_evaluated (no se concede por omision); no
        # sensible -> NOT_APPLICABLE (un motivo denied_* con NOT_APPLICABLE seria
        # incoherente y ensuciaria la auditoria).
        if sensitive:
            return CapabilityDecision(
                capability_id=capability_id,
                decision=Decision.DENY,
                reason_code=ReasonCode.DENIED_NOT_EVALUATED,
                policy_version=self.policy_version,
                sensitive=True,
                kill_switch_id=None,
            )
        return CapabilityDecision(
            capability_id=capability_id,
            decision=Decision.NOT_APPLICABLE,
            reason_code=ReasonCode.NOT_APPLICABLE_UNKNOWN_CAPABILITY,
            policy_version=self.policy_version,
            sensitive=False,
            kill_switch_id=None,
        )


@dataclass(frozen=True, slots=True)
class _EvalContext:
    """Datos leidos una sola vez del store para toda la evaluacion."""

    policy_version: str
    rules: Sequence[PolicyRuleRecord]
    entitlements: Sequence[EntitlementRecord]
    overrides: Sequence[OverrideRecord]
    kill_switches: Sequence[KillSwitchRecord]
    now_ms: int


def _expired(expires_at: int | None, now_ms: int) -> bool:
    """Caducado si tiene expiracion y ya paso; sin expiracion nunca caduca."""
    return expires_at is not None and expires_at <= now_ms


def _wildcard_matches(match_value: str | None, actual: str | None) -> bool:
    """True si el criterio es comodin (None) o coincide con una entrada conocida.

    Un criterio no nulo con entrada desconocida (None) NO encaja: nunca se
    cumple por omision.
    """
    if match_value is None:
        return True
    return actual is not None and actual == match_value


def _rule_matches(rule: PolicyRuleRecord, inputs: PolicyInputs) -> bool:
    """True si cada match_* no nulo de la regla coincide con la entrada."""
    if not _wildcard_matches(rule.match_jurisdiction, inputs.jurisdiction.jurisdiction):
        return False
    if not _wildcard_matches(rule.match_plan, inputs.plan):
        return False
    if not _wildcard_matches(rule.match_role, inputs.role):
        return False
    if rule.match_kyc_status is not None:
        # UNKNOWN es entrada desconocida: un criterio de KYC no la cumple.
        if inputs.kyc_status is KycStatus.UNKNOWN:
            return False
        if inputs.kyc_status.value != rule.match_kyc_status:
            return False
    if rule.match_vpn is not None:
        if inputs.vpn_detected is None:
            return False
        if inputs.vpn_detected != rule.match_vpn:
            return False
    return True


def _resource_value(resources: ResourceContext, scope: str) -> str | None:
    if scope == "exchange":
        return resources.exchange
    if scope == "connector":
        return resources.connector
    return resources.market_scope


def _kill_switch_applies(
    switch: KillSwitchRecord,
    inputs: PolicyInputs,
    resources: ResourceContext | None,
    capability_id: str,
) -> bool:
    """True si el kill switch aplica a esta pregunta (ver asimetria UI/backend)."""
    scope = switch.scope
    if scope == "global":
        return True
    if scope == "capability":
        return switch.target_ref == capability_id
    if scope == "tenant":
        return switch.tenant_id == inputs.subject_tenant_id
    if scope == "user":
        return (
            switch.tenant_id == inputs.subject_tenant_id
            and switch.user_id == inputs.subject_user_id
        )
    if scope in _RESOURCE_SCOPES:
        if resources is None:
            return False
        value = _resource_value(resources, scope)
        return value is not None and value == switch.target_ref
    return False


def _has_valid_entitlement(
    entitlements: Sequence[EntitlementRecord], capability_id: str, now_ms: int
) -> bool:
    return any(
        e.capability_id == capability_id and not _expired(e.expires_at, now_ms)
        for e in entitlements
    )


class PolicyEvaluator:
    """Resuelve capacidades a decisiones autoritativas (ADR-012)."""

    def __init__(self, store: PolicyStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def current_policy_version(self) -> str | None:
        """La policy_version en vigor segun el store (lectura barata).

        La usa el CachedPolicyEvaluator (B5) para detectar que una entrada
        cacheada quedo con una version antigua, sin recomputar el set entero.
        """
        return self._store.current_policy_version()

    def evaluate(
        self,
        inputs: PolicyInputs,
        capability_ids: Sequence[str],
        resources: ResourceContext | None = None,
    ) -> CapabilitySet:
        """Evalua cada capability para el sujeto de inputs (DENY > ALLOW)."""
        now_ms = self._clock.now_ms()
        policy_version = self._store.current_policy_version()

        # 1. Sin reglamento vigente no hay permiso: DENY para TODA capability.
        if policy_version is None:
            decisions = {
                cap: CapabilityDecision(
                    capability_id=cap,
                    decision=Decision.DENY,
                    reason_code=ReasonCode.DENIED_POLICY_UNAVAILABLE,
                    policy_version=None,
                    sensitive=is_sensitive(cap),
                    kill_switch_id=None,
                )
                for cap in capability_ids
            }
            return CapabilitySet(
                tenant_id=inputs.subject_tenant_id,
                user_id=inputs.subject_user_id,
                policy_version=None,
                evaluated_at=now_ms,
                decisions=decisions,
            )

        context = _EvalContext(
            policy_version=policy_version,
            rules=self._store.rules(policy_version),
            entitlements=self._store.entitlements(
                inputs.subject_tenant_id, inputs.subject_user_id
            ),
            overrides=self._store.overrides(
                inputs.subject_tenant_id, inputs.subject_user_id
            ),
            kill_switches=self._store.active_kill_switches(),
            now_ms=now_ms,
        )
        decisions = {
            cap: _evaluate_capability(cap, inputs, resources, context)
            for cap in capability_ids
        }
        return CapabilitySet(
            tenant_id=inputs.subject_tenant_id,
            user_id=inputs.subject_user_id,
            policy_version=policy_version,
            evaluated_at=now_ms,
            decisions=decisions,
        )


def _evaluate_capability(
    capability_id: str,
    inputs: PolicyInputs,
    resources: ResourceContext | None,
    ctx: _EvalContext,
) -> CapabilityDecision:
    sensitive = is_sensitive(capability_id)

    def deny(
        reason: ReasonCode, kill_switch_id: str | None = None
    ) -> CapabilityDecision:
        return CapabilityDecision(
            capability_id=capability_id,
            decision=Decision.DENY,
            reason_code=reason,
            policy_version=ctx.policy_version,
            sensitive=sensitive,
            kill_switch_id=kill_switch_id,
        )

    def allow(reason: ReasonCode) -> CapabilityDecision:
        return CapabilityDecision(
            capability_id=capability_id,
            decision=Decision.ALLOW,
            reason_code=reason,
            policy_version=ctx.policy_version,
            sensitive=sensitive,
            kill_switch_id=None,
        )

    # 2. Kill switches: la union de bloqueos activos que aplican. Cualquiera que
    # aplique DENIEGA; un scope amplio bloquea a los inferiores por construccion.
    for switch in ctx.kill_switches:
        if _kill_switch_applies(switch, inputs, resources, capability_id):
            return deny(ReasonCode.DENIED_BY_KILL_SWITCH, switch.kill_switch_id)

    # 3. Fail-closed de entradas, SOLO en capacidades sensibles (D5).
    if sensitive:
        if inputs.jurisdiction.jurisdiction is None:
            return deny(ReasonCode.DENIED_BY_JURISDICTION)
        if inputs.vpn_detected is None:
            return deny(ReasonCode.DENIED_BY_VPN)

    # 4. Reglas del reglamento vigente para esta capability.
    matching = [
        rule
        for rule in ctx.rules
        if rule.capability_id == capability_id and _rule_matches(rule, inputs)
    ]
    deny_rule = next((rule for rule in matching if rule.effect == "deny"), None)
    if deny_rule is not None:
        return deny(ReasonCode(deny_rule.reason_code))
    allow_rule = next((rule for rule in matching if rule.effect == "allow"), None)

    # Candidato blando (ALLOW/NOT_APPLICABLE); todos los DENY duros ya salieron.
    if sensitive:
        if allow_rule is None:
            # Nada la permite: no se concede por silencio.
            return deny(ReasonCode.DENIED_POLICY_UNAVAILABLE)
        # 5. Entitlement obligatorio en sensibles (D6).
        if not _has_valid_entitlement(ctx.entitlements, capability_id, ctx.now_ms):
            return deny(ReasonCode.DENIED_BY_MISSING_ENTITLEMENT)
        candidate = allow(ReasonCode.ALLOWED_BY_POLICY)
    elif allow_rule is not None:
        candidate = allow(ReasonCode.ALLOWED_BY_POLICY)
    elif _has_valid_entitlement(ctx.entitlements, capability_id, ctx.now_ms):
        # 5. En NO sensibles un entitlement vigente concede aunque no haya regla.
        candidate = allow(ReasonCode.ALLOWED_BY_POLICY)
    else:
        candidate = CapabilityDecision(
            capability_id=capability_id,
            decision=Decision.NOT_APPLICABLE,
            reason_code=ReasonCode.NOT_APPLICABLE_UNKNOWN_CAPABILITY,
            policy_version=ctx.policy_version,
            sensitive=sensitive,
            kill_switch_id=None,
        )

    # 6. Overrides: DENY siempre gana; ALLOW solo eleva si no hay DENY acumulado.
    active = [
        override
        for override in ctx.overrides
        if override.capability_id == capability_id
        and not _expired(override.expires_at, ctx.now_ms)
    ]
    if any(override.effect == "deny" for override in active):
        return deny(ReasonCode.DENIED_BY_OVERRIDE)
    if (
        any(override.effect == "allow" for override in active)
        and candidate.decision is not Decision.DENY
    ):
        return allow(ReasonCode.ALLOWED_BY_OVERRIDE)
    return candidate
