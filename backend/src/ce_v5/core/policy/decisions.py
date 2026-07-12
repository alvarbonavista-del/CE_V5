"""Decisiones del PolicyEvaluator y catalogo de motivos (ADR-012).

Salida por capability: ALLOW | DENY | NOT_APPLICABLE, siempre con reason_code
y policy_version. Ninguna decision viaja sin motivo: un DENY sin motivo es
indepurable, y la auditoria de acciones sensibles lo exige.

Frontera: la exposicion del capability set a la UI (contrato de API y tipos TS)
es de P06b. P06 entrega la decision autoritativa de backend.
"""

from enum import StrEnum


class Decision(StrEnum):
    """Decision por capability (ADR-012)."""

    ALLOW = "allow"
    DENY = "deny"
    NOT_APPLICABLE = "not_applicable"


class ReasonCode(StrEnum):
    """Motivo de la decision. DENY siempre lleva el motivo que gano."""

    ALLOWED_BY_POLICY = "allowed_by_policy"
    ALLOWED_BY_OVERRIDE = "allowed_by_override"
    DENIED_BY_KILL_SWITCH = "denied_by_kill_switch"
    DENIED_BY_JURISDICTION = "denied_by_jurisdiction"
    DENIED_BY_KYC = "denied_by_kyc"
    DENIED_BY_VPN = "denied_by_vpn"
    DENIED_BY_PLAN = "denied_by_plan"
    DENIED_BY_ROLE = "denied_by_role"
    DENIED_BY_MISSING_ENTITLEMENT = "denied_by_missing_entitlement"
    DENIED_BY_OVERRIDE = "denied_by_override"
    DENIED_POLICY_UNAVAILABLE = "denied_policy_unavailable"
    DENIED_CACHE_STALE = "denied_cache_stale"
    DENIED_POLICY_VERSION_NOT_CURRENT = "denied_policy_version_not_current"
    DENIED_NOT_RECOMPUTABLE = "denied_not_recomputable"
    DENIED_NOT_EVALUATED = "denied_not_evaluated"
    # El gate (B8) no pudo TRAZAR una accion sensible que se iba a permitir: se
    # deniega de mas antes que ejecutar sin traza (D8). Distinto de un fallo de
    # recomputo de politica: aqui la politica decidio, lo que fallo fue auditar.
    DENIED_AUDIT_UNAVAILABLE = "denied_audit_unavailable"
    NOT_APPLICABLE_UNKNOWN_CAPABILITY = "not_applicable_unknown_capability"
