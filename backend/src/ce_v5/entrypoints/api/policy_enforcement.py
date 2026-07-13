"""La primitiva de enforcement en el borde (P06b, CA-10 opcion A; dictamen CSA L).

FAIL-CLOSED ESTRICTO: solo un ALLOW EXPLICITO deja pasar. Un NOT_APPLICABLE (la
capability no esta en el reglamento) NO es un permiso: es un "no lo se", y un "no lo se"
en un borde publico se responde que no. Esta es la primitiva que P10a y P10b montaran
sobre sus endpoints sensibles cuando existan; hoy la usa la suscripcion realtime, que es
el unico borde gateado que P06b tiene (las cinco capacidades sensibles son de piezas
posteriores).

require() (no capability_set) es lo que se usa aqui: es la decision AUTORITATIVA, y
ADEMAS audita. La vista de cortesia de /v1/capabilities no autoriza nada.
"""

from __future__ import annotations

from uuid import UUID

from ce_v5.core.policy.decisions import Decision
from ce_v5.core.policy.gate import PolicyDenied
from ce_v5.core.policy.subject_inputs import ApiSubjectInputsResolver
from ce_v5.entrypoints.api.composition import ApiContext


class CapabilityDenied(RuntimeError):
    """La capability no esta permitida para este sujeto."""

    def __init__(self, capability_id: str, reason_code: str) -> None:
        super().__init__(f"capability {capability_id!r} denegada: {reason_code}")
        self.capability_id = capability_id
        self.reason_code = reason_code


def require_capability(
    context: ApiContext,
    user_id: UUID,
    tenant_id: str,
    client_ip: str | None,
    capability_id: str,
) -> None:
    """Exige un ALLOW EXPLICITO. Cualquier otra cosa es CapabilityDenied."""
    resolver = ApiSubjectInputsResolver(
        client_ip=client_ip,
        ip_geo=context.ip_geo,
        kyc=context.kyc,
        vpn=context.vpn,
    )
    inputs = resolver.resolve(tenant_id, str(user_id))
    try:
        decision = context.gate.require(inputs, capability_id)
    except PolicyDenied as denied:
        raise CapabilityDenied(
            capability_id, denied.decision.reason_code.value
        ) from denied
    if decision.decision is not Decision.ALLOW:
        # No deberia ocurrir (require ya lanza ante un DENY), pero un NOT_APPLICABLE
        # que llegara hasta aqui NO es un permiso: es un "no lo se", y en un borde
        # publico un "no lo se" se responde que no.
        raise CapabilityDenied(capability_id, decision.reason_code.value)
