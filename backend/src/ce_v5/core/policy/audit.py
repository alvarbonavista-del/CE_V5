"""Puerto de auditoria de acciones sensibles (ADR-012, CA-05).

Cada decision sobre una capacidad SENSIBLE deja una traza por sujeto en
sensitive_action_audit: que se decidio, por que motivo, bajo que policy_version
y con que contexto. Es auditoria de SEGURIDAD por sujeto (tenant-scoped),
distinta de operator_audit (accion de operador, plataforma) y del audit_log
tecnico de P02b.

REGLA DURA del contexto: build_context guarda VEREDICTOS Y REFERENCIAS
(jurisdiccion resuelta y su fuente, kyc_status, vpn detectado si/no, plan, role,
kill_switch_id que gano), JAMAS datos personales crudos, ni credenciales, ni
IPs. La auditoria debe permitir reconstruir POR QUE se decidio, no espiar al
usuario: la IP vive en la capa de proveedores (B3) y no llega hasta aqui.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ce_v5.core.policy.decisions import Decision, ReasonCode
from ce_v5.core.policy.evaluator import CapabilityDecision
from ce_v5.core.policy.inputs import PolicyInputs

# Discriminador de la fila (CA-11). Una decision del PolicyEvaluator y un hecho de
# autenticacion comparten tabla, pero NO comparten vocabulario de reason_code: sin este
# discriminador, un filtro por motivo mezclaria dos idiomas en la misma columna.
AUDIT_KIND_POLICY = "policy"
AUDIT_KIND_AUTH = "auth"


@dataclass(frozen=True, slots=True)
class SensitiveActionRecord:
    """Una fila de auditoria de accion sensible lista para escribir (CA-05).

    policy_version es obligatorio: para audit_kind=policy es la version que FUNDAMENTA
    la decision; para audit_kind=auth es la que estaba VIGENTE, como contexto. context
    lleva solo veredictos y referencias (ver la regla dura del modulo).

    audit_kind por defecto es "policy": TODOS los llamadores de P06 (el gate) siguen
    funcionando sin tocarlos, que es lo que exige una ampliacion aditiva.
    """

    tenant_id: str
    user_id: str | None
    capability_id: str
    decision: Decision
    reason_code: ReasonCode
    policy_version: str
    sensitive: bool
    context: Mapping[str, object]
    audit_kind: str = AUDIT_KIND_POLICY


@runtime_checkable
class SensitiveActionAudit(Protocol):
    """Escritura de la auditoria de accion sensible (ADR-012)."""

    def record(self, entry: SensitiveActionRecord) -> None:
        """Persiste la traza. Falla ruidoso si no puede escribirla (CA-05)."""
        ...


def build_context(
    inputs: PolicyInputs, decision: CapabilityDecision
) -> dict[str, object]:
    """Construye el context de auditoria: SOLO veredictos y referencias (CA-05).

    Incluye jurisdiccion resuelta + su fuente + conflicting, kyc_status,
    vpn_detected, plan, role y el kill_switch_id que gano. NUNCA la IP ni datos
    personales crudos ni credenciales: no estan en PolicyInputs (viven en la
    capa de proveedores), y aqui solo se copian campos de veredicto.
    """
    jurisdiction = inputs.jurisdiction
    return {
        "jurisdiction": jurisdiction.jurisdiction,
        "jurisdiction_source": (
            jurisdiction.source.value if jurisdiction.source is not None else None
        ),
        "jurisdiction_conflicting": jurisdiction.conflicting,
        "kyc_status": inputs.kyc_status.value,
        "vpn_detected": inputs.vpn_detected,
        "plan": inputs.plan,
        "role": inputs.role,
        "kill_switch_id": decision.kill_switch_id,
    }
