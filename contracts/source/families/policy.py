"""Familia policy.* : politica de plataforma y kill switch (ADR-021, ADR-012).

Declara el vocabulario y el contrato de los eventos que propagan cambios de
politica sin reinicio (ADR-012). Vive en contracts/source porque es CONTRATO
(ADR-006): alimenta JSON Schema y tipos TS.

FRONTERA policy.* / component.* (regla dura, CA-02):
  policy.*     = CAUSA: cambia la politica, se activa un kill switch, se
                 invalida el capability set de un sujeto.
  component.*  = CONSECUENCIA: cambia el lifecycle de una ComponentInstance.
Flujo canonico: kill switch -> policy.kill_switch_activated -> el supervisor
lo consume -> si decide aislar, emite component.quarantined con causation_id
apuntando al policy.*. Un kill switch NUNCA se emite como component.*.
"""

from enum import StrEnum

from pydantic import model_validator

from source.envelope import EventPayload


class PolicyEventType(StrEnum):
    """Tipos de evento policy.* (ADR-021)."""

    KILL_SWITCH_ACTIVATED = "policy.kill_switch_activated"
    KILL_SWITCH_DEACTIVATED = "policy.kill_switch_deactivated"
    VERSION_PUBLISHED = "policy.version_published"
    SUBJECT_INVALIDATED = "policy.subject_invalidated"


class KillSwitchScope(StrEnum):
    """Ambitos de kill switch (ADR-012).

    Un switch apaga TODO lo que cae dentro de su ambito; un ambito amplio
    bloquea a los inferiores. La union de bloqueos activos manda.
    """

    GLOBAL = "global"
    EXCHANGE = "exchange"
    CONNECTOR = "connector"
    MARKET_SCOPE = "market_scope"
    CAPABILITY = "capability"
    TENANT = "tenant"
    USER = "user"


class InvalidationReason(StrEnum):
    """Motivo por el que el capability set de un sujeto deja de valer."""

    ROLE_CHANGED = "role_changed"
    PLAN_CHANGED = "plan_changed"
    ENTITLEMENT_CHANGED = "entitlement_changed"
    OVERRIDE_CHANGED = "override_changed"
    JURISDICTION_CHANGED = "jurisdiction_changed"
    KYC_CHANGED = "kyc_changed"
    KILL_SWITCH_CHANGED = "kill_switch_changed"
    POLICY_VERSION_CHANGED = "policy_version_changed"


class KillSwitchPayload(EventPayload):
    """Payload de policy.kill_switch_activated y _deactivated (ADR-012).

    target_ref identifica el objetivo del ambito: el exchange, el connector,
    el market_scope o la capability. En GLOBAL no hay objetivo. En TENANT y
    USER el objetivo es el sujeto, no un target_ref.
    """

    kill_switch_id: str
    scope: KillSwitchScope
    reason_code: str
    policy_version: str
    actor: str
    target_ref: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None

    @model_validator(mode="after")
    def _reglas_de_scope(self) -> "KillSwitchPayload":
        con_objetivo = {
            KillSwitchScope.EXCHANGE,
            KillSwitchScope.CONNECTOR,
            KillSwitchScope.MARKET_SCOPE,
            KillSwitchScope.CAPABILITY,
        }
        if self.scope is KillSwitchScope.GLOBAL:
            if self.target_ref is not None:
                msg = "scope=global no lleva target_ref."
                raise ValueError(msg)
            if self.tenant_id is not None or self.user_id is not None:
                msg = "scope=global no lleva tenant_id ni user_id."
                raise ValueError(msg)
        elif self.scope in con_objetivo:
            if self.target_ref is None:
                msg = f"scope={self.scope.value} exige target_ref."
                raise ValueError(msg)
            if self.tenant_id is not None or self.user_id is not None:
                msg = f"scope={self.scope.value} no lleva tenant_id ni user_id."
                raise ValueError(msg)
        elif self.scope is KillSwitchScope.TENANT:
            if self.tenant_id is None:
                msg = "scope=tenant exige tenant_id."
                raise ValueError(msg)
            if self.user_id is not None or self.target_ref is not None:
                msg = "scope=tenant no lleva user_id ni target_ref."
                raise ValueError(msg)
        else:
            if self.tenant_id is None or self.user_id is None:
                msg = "scope=user exige tenant_id y user_id."
                raise ValueError(msg)
            if self.target_ref is not None:
                msg = "scope=user no lleva target_ref."
                raise ValueError(msg)
        return self


class PolicyVersionPublishedPayload(EventPayload):
    """Payload de policy.version_published: entra en vigor una policy_version."""

    policy_version: str
    actor: str
    previous_policy_version: str | None = None
    reason: str | None = None


class SubjectInvalidatedPayload(EventPayload):
    """Payload de policy.subject_invalidated: el capability set de un sujeto
    deja de ser valido y debe recomputarse (ADR-012: invalidacion por evento).

    user_id ausente => se invalida el tenant entero.
    """

    tenant_id: str
    reason: InvalidationReason
    policy_version: str
    user_id: str | None = None
