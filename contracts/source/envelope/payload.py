"""Payload base tipado del envelope (ADR-003).

El envelope es generico sobre su payload. Cada tipo de evento define su
propio payload como subclase de EventPayload en su componente/pieza
(gobernanza ADR-004). P01 no declara payloads concretos: aqui solo vive
la raiz tipada, sin campos.
"""

from pydantic import BaseModel, ConfigDict


class EventPayload(BaseModel):
    """Raiz tipada de todo payload de evento. Sin campos en P01."""

    model_config = ConfigDict(extra="forbid")
