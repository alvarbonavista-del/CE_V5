"""Capability set de CORTESIA para la UI (P06b, ADR-012, D9 de P06).

ESTO ES INFORMATIVO. La UI oculta o deshabilita botones con lo que aqui se devuelve, y
nada mas. La decision AUTORITATIVA se vuelve a tomar en el BACKEND, en el punto
sensible, cada vez que alguien intenta la accion (PolicyGate.require). Un cliente que se
salte esta ruta, o que manipule su respuesta, no gana absolutamente nada: al intentar la
accion, el backend reevalua y falla cerrado.

Por eso este endpoint NO audita (auditar cada refresco de pantalla inundaria la traza de
ruido justo cuando importa) y por eso el contrato declara advisory=true en voz alta.

La IP sale de la CONEXION, jamas de una cabecera: X-Forwarded-For la escribe el cliente
y fiarse de ella permitiria fingir que se llama desde otro pais.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status

from ce_v5.core.policy.subject_inputs import ApiSubjectInputsResolver
from ce_v5.entrypoints.api.client_ip import client_ip
from ce_v5.entrypoints.api.security import Context, Principal
from source.api import CapabilitiesResponse, CapabilityDecisionView

router = APIRouter(prefix="/v1")

# Una lista sin limite es una invitacion a hacer trabajar al servidor gratis.
MAX_CAPABILITIES = 50


@router.get("/capabilities", response_model=CapabilitiesResponse)
def capabilities(
    request: Request,
    principal: Principal,
    context: Context,
    capability: Annotated[list[str] | None, Query()] = None,
) -> CapabilitiesResponse:
    """Vista de cortesia del capability set del sujeto autenticado.

    QUE capacidades interesan lo dice el cliente (eso no es identidad, y puede venir de
    el). QUIEN pregunta y en QUE tenant, no: la identidad sale de la sesion verificada y
    el tenant lo resuelve el backend desde la pertenencia (ADR-011).
    """
    capability = capability or []
    if len(capability) > MAX_CAPABILITIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"como maximo {MAX_CAPABILITIES} capabilities por peticion",
        )

    with context.scoped_db.transaction(principal.user_id) as scoped:
        tenant_id = str(scoped.context.tenant_id)

    resolver = ApiSubjectInputsResolver(
        client_ip=client_ip(request, context.api_config),
        ip_geo=context.ip_geo,
        kyc=context.kyc,
        vpn=context.vpn,
    )
    inputs = resolver.resolve(tenant_id, str(principal.user_id))

    # capability_set (no require): vista de CORTESIA, sin auditoria (D9).
    capability_set = context.gate.capability_set(inputs, capability)
    return CapabilitiesResponse(
        policy_version=capability_set.policy_version,
        evaluated_at=capability_set.evaluated_at,
        decisions=[
            CapabilityDecisionView(
                capability_id=decision.capability_id,
                decision=decision.decision.value,
                reason_code=decision.reason_code.value,
                sensitive=decision.sensitive,
                kill_switch_id=decision.kill_switch_id,
            )
            for decision in (
                capability_set.decision_for(capability_id)
                for capability_id in capability
            )
        ],
    )
