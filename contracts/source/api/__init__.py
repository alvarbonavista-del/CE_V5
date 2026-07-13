"""Contratos de la API HTTP/WS (P06b, CA-08, ADR-006).

Aqui viven los tipos de PETICION y RESPUESTA de la API. NO son eventos: no viajan en el
Envelope, no pertenecen a ninguna familia y NO entran en el registro event_type ->
payload (CA-06). Son la frontera entre el cliente y el backend, y como todo contrato de
CE v5, se generan a JSON Schema y a TypeScript desde esta fuente unica: el cliente los
CONSUME, no los inventa (ADR-006, ADR-019).
"""

from source.api.auth import (
    LoginRequest,
    MeResponse,
    RegisterRequest,
    SessionResponse,
)
from source.api.capabilities import CapabilitiesResponse, CapabilityDecisionView
from source.api.errors import ApiError
from source.api.realtime import (
    RealtimeAck,
    RealtimeAuth,
    RealtimeErrorMessage,
    RealtimeEvent,
    RealtimeSubscribe,
)

__all__ = [
    "ApiError",
    "CapabilitiesResponse",
    "CapabilityDecisionView",
    "LoginRequest",
    "MeResponse",
    "RealtimeAck",
    "RealtimeAuth",
    "RealtimeErrorMessage",
    "RealtimeEvent",
    "RealtimeSubscribe",
    "RegisterRequest",
    "SessionResponse",
]
