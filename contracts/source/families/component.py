"""Familia component.* : lifecycle observable de Componentes (ADR-004, ADR-010).

Declara el vocabulario y el contrato de los eventos component.* que emite
el supervisor de lifecycle (P04) en cada transicion (ADR-010). Vive en
contracts/source porque es CONTRATO: sus tipos alimentan el JSON Schema y
los tipos TS (ADR-006), y los consumen backend y frontend. La fuente de
verdad del vocabulario del lifecycle es ESTE modulo; el nucleo
(core/component) lo importa, no lo redefine.
"""

from enum import StrEnum

from pydantic import model_validator

from source.envelope import EventPayload


class LifecycleState(StrEnum):
    """Estados de la maquina principal de ComponentInstance (ADR-010)."""

    REGISTERED = "registered"
    INITIALIZING = "initializing"
    INITIALIZED = "initialized"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    UNLOADED = "unloaded"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class HealthStatus(StrEnum):
    """Salud del componente, EJE APARTE del lifecycle (ADR-010)."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class ReadinessStatus(StrEnum):
    """Disponibilidad para recibir trabajo, EJE APARTE (ADR-010)."""

    READY = "ready"
    NOT_READY = "not_ready"


class LifecycleScope(StrEnum):
    """Ambito de vida de una ComponentInstance (ADR-010)."""

    GLOBAL = "global"
    TENANT = "tenant"
    USER = "user"


class ComponentEventType(StrEnum):
    """Tipos de evento component.* (ADR-004): uno por estado alcanzado."""

    REGISTERED = "component.registered"
    INITIALIZING = "component.initializing"
    INITIALIZED = "component.initialized"
    STARTING = "component.starting"
    RUNNING = "component.running"
    PAUSED = "component.paused"
    STOPPING = "component.stopping"
    STOPPED = "component.stopped"
    UNLOADED = "component.unloaded"
    FAILED = "component.failed"
    QUARANTINED = "component.quarantined"


def event_type_for_state(state: LifecycleState) -> ComponentEventType:
    """Devuelve el ComponentEventType que anuncia la entrada en state."""
    return ComponentEventType(f"component.{state.value}")


class ComponentLifecyclePayload(EventPayload):
    """Payload comun de todo evento component.* (ADR-010).

    Identifica la instancia y su transicion. previous_state es None solo en
    el primer evento (component.registered). health_status y
    readiness_status son EJES APARTE del lifecycle (ADR-010). error_code se
    espera en FAILED/QUARANTINED.
    """

    component_id: str
    component_version: str
    component_instance_id: str
    lifecycle_scope: LifecycleScope
    new_state: LifecycleState
    health_status: HealthStatus
    readiness_status: ReadinessStatus
    previous_state: LifecycleState | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    reason: str | None = None
    error_code: str | None = None

    @model_validator(mode="after")
    def _reglas_de_scope(self) -> "ComponentLifecyclePayload":
        if self.lifecycle_scope is LifecycleScope.GLOBAL:
            if self.tenant_id is not None or self.user_id is not None:
                msg = "lifecycle_scope=global no lleva tenant_id ni user_id."
                raise ValueError(msg)
        elif self.lifecycle_scope is LifecycleScope.TENANT:
            if self.tenant_id is None:
                msg = "lifecycle_scope=tenant exige tenant_id."
                raise ValueError(msg)
            if self.user_id is not None:
                msg = "lifecycle_scope=tenant no lleva user_id."
                raise ValueError(msg)
        else:
            if self.tenant_id is None or self.user_id is None:
                msg = "lifecycle_scope=user exige tenant_id y user_id."
                raise ValueError(msg)
        return self
