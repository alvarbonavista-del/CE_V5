"""Contrato de las capabilities servidas al cliente (P06b, ADR-012).

ESTO ES INFORMATIVO, Y EL CONTRATO LO DICE EN VOZ ALTA: el campo advisory vale SIEMPRE
true. La UI usa esto para OCULTAR o DESHABILITAR botones (cortesia). La decision
AUTORITATIVA se vuelve a tomar en el backend, en el punto sensible, cada vez (ADR-012;
D9 de P06). Si la UI se equivoca o alguien la manipula, no pasa nada: el backend vuelve
a preguntar y falla cerrado.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class CapabilityDecisionView(BaseModel):
    """Una decision, tal como la ve el cliente. No autoriza: informa."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_id: str
    decision: str
    reason_code: str
    sensitive: bool
    kill_switch_id: str | None = None


class CapabilitiesResponse(BaseModel):
    """El capability set de CORTESIA (D9). Jamas una autorizacion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    advisory: Literal[True] = True
    """SIEMPRE true: esto NO autoriza nada. La autorizacion se decide en el backend."""

    policy_version: str | None
    evaluated_at: int
    decisions: list[CapabilityDecisionView]
