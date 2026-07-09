"""Envelope canonico unico (ADR-003).

Un unico sobre compartido por todos los eventos, con payload tipado. Aqui
solo viven los CAMPOS y sus reglas estructurales: identidad fisica y
logica, alcance, ranuras de tiempo y linaje. La SEMANTICA del tiempo
(Clock, watermark, maturity) es P02 (ADR-007): aqui event_time,
ingestion_time, processing_time y time_anchor_ref son solo ranuras
(campos opcionales). Ninguna logica que produzca o consuma eventos vive
aqui.
"""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from envelope.enums import Scope
from envelope.payload import EventPayload
from families import validate_event_type

# Version del contrato del envelope (ADR-005: envelope_version). Evoluciona
# de forma independiente de event_schema_version (version del payload por
# tipo de evento).
ENVELOPE_VERSION = 1


class Envelope[PayloadT: EventPayload](BaseModel):
    """Sobre canonico unico (ADR-003). Inmutable, sin campos extra."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # --- Identidad y tipo ---
    event_id: UUID = Field(default_factory=uuid4)
    event_type: str
    envelope_version: int = Field(default=ENVELOPE_VERSION, ge=1)
    event_schema_version: int = Field(ge=1)
    source: str = Field(min_length=1)

    # --- Identidad logica (separada de la fisica) ---
    idempotency_key: str = Field(min_length=1)
    stream_key: str = Field(min_length=1)
    source_sequence: int | None = Field(default=None, ge=0)
    source_event_id: str | None = None

    # --- Alcance ---
    scope: Scope
    tenant_id: str | None = None
    user_id: str | None = None

    # --- Temporalidad (ranuras; semantica en P02/ADR-007) ---
    event_time: datetime | None = None
    ingestion_time: datetime | None = None
    processing_time: datetime | None = None
    time_anchor_ref: str | None = None

    # --- Linaje ---
    correlation_id: str = Field(min_length=1)
    causation_id: str | None = None

    # --- Payload tipado ---
    payload: PayloadT

    @field_validator("event_type")
    @classmethod
    def _event_type_familia_accion(cls, value: str) -> str:
        return validate_event_type(value)

    @model_validator(mode="after")
    def _reglas_de_scope(self) -> "Envelope[PayloadT]":
        # tenant_id: obligatorio en tenant/user, prohibido en
        # public_market, opcional en system (ADR-003).
        if self.scope in (Scope.TENANT, Scope.USER):
            if self.tenant_id is None:
                msg = f"tenant_id obligatorio con scope={self.scope.value}."
                raise ValueError(msg)
        elif self.scope is Scope.PUBLIC_MARKET and self.tenant_id is not None:
            msg = "tenant_id prohibido con scope=public_market."
            raise ValueError(msg)
        # user_id: presente si y solo si scope=user (ADR-003).
        if self.scope is Scope.USER:
            if self.user_id is None:
                msg = "user_id obligatorio con scope=user."
                raise ValueError(msg)
        elif self.user_id is not None:
            msg = f"user_id solo con scope=user, no con {self.scope.value}."
            raise ValueError(msg)
        return self
