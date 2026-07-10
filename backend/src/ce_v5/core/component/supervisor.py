"""Supervisor de lifecycle de Componentes (ADR-010).

Registra ComponentInstances creadas desde ComponentDefinitions (ADR-009),
las conduce por la maquina de estados de lifecycle.py validando cada
transicion, y EMITE un evento component.* por el EventBus en cada paso, con
envelope (ADR-003) y Clock (ADR-007). health_status y readiness_status se
llevan como ejes APARTE del estado (ADR-010). El supervisor NO importa
componentes: recibe ya construido el objeto que implementa
ComponentLifecycle (lo carga el composition root con el discovery).

Alcance deliberado de P04: la maquina y la emision. El gate geo/plan actua
ANTES de INITIALIZE (P06): aqui queda el PUNTO (initialize) donde actuara,
pero el gate NO se construye. El scope tenant/user se MODELA
(lifecycle_scope + tenant_id/user_id), pero el aislamiento RLS es P05.
QUARANTINED se define como estado; el kill switch que lleva a el es P06. El
arranque topologico por dependencias y el reintento/backoff son de piezas
posteriores.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from ce_v5.core.bus import BusMessage, EventBus
from ce_v5.core.clock import Clock
from ce_v5.core.component.definition import ComponentDefinition
from ce_v5.core.component.lifecycle import ComponentLifecycle, can_transition
from source.envelope import Envelope, Scope
from source.families.component import (
    ComponentLifecyclePayload,
    HealthStatus,
    LifecycleScope,
    LifecycleState,
    ReadinessStatus,
    event_type_for_state,
)

# Mapa de ambito de instancia (ADR-010) a scope del envelope (ADR-003).
_ENVELOPE_SCOPE: dict[LifecycleScope, Scope] = {
    LifecycleScope.GLOBAL: Scope.SYSTEM,
    LifecycleScope.TENANT: Scope.TENANT,
    LifecycleScope.USER: Scope.USER,
}


class SupervisorError(Exception):
    """Error de uso del supervisor (ADR-010)."""


class UnknownInstanceError(SupervisorError):
    """Se opero sobre un instance_id no registrado."""


class DuplicateInstanceError(SupervisorError):
    """Se intento registrar un instance_id ya existente."""


class IllegalTransitionError(SupervisorError):
    """Transicion no permitida por la maquina de estados (ADR-010)."""


@dataclass(slots=True)
class ComponentInstance:
    """Objeto vivo de runtime (ADR-010). Estado y salud, ejes separados."""

    definition: ComponentDefinition
    component: ComponentLifecycle
    instance_id: str
    scope: LifecycleScope
    tenant_id: str | None = None
    user_id: str | None = None
    state: LifecycleState = LifecycleState.REGISTERED
    health: HealthStatus = HealthStatus.HEALTHY
    readiness: ReadinessStatus = ReadinessStatus.NOT_READY


class Supervisor:
    """Registry central que conduce el lifecycle y emite component.* (ADR-010)."""

    def __init__(self, bus: EventBus, clock: Clock, *, source: str) -> None:
        self._bus = bus
        self._clock = clock
        self._source = source
        self._instances: dict[str, ComponentInstance] = {}

    def instance(self, instance_id: str) -> ComponentInstance:
        """Devuelve la instancia registrada o lanza UnknownInstanceError."""
        try:
            return self._instances[instance_id]
        except KeyError as exc:
            raise UnknownInstanceError(instance_id) from exc

    def register(
        self,
        definition: ComponentDefinition,
        component: ComponentLifecycle,
        *,
        scope: LifecycleScope = LifecycleScope.GLOBAL,
        instance_id: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> ComponentInstance:
        """Registra una Instance en REGISTERED y emite component.registered."""
        iid = instance_id or uuid4().hex
        if iid in self._instances:
            raise DuplicateInstanceError(iid)
        instance = ComponentInstance(
            definition=definition,
            component=component,
            instance_id=iid,
            scope=scope,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        self._emit(
            instance,
            previous=None,
            new=LifecycleState.REGISTERED,
            health=instance.health,
            readiness=instance.readiness,
        )
        self._instances[iid] = instance
        return instance

    def initialize(self, instance_id: str) -> None:
        self._two_phase(
            self.instance(instance_id),
            LifecycleState.INITIALIZING,
            LifecycleState.INITIALIZED,
            lambda inst: inst.component.initialize(),
        )

    def start(self, instance_id: str) -> None:
        self._two_phase(
            self.instance(instance_id),
            LifecycleState.STARTING,
            LifecycleState.RUNNING,
            lambda inst: inst.component.start(),
        )

    def stop(self, instance_id: str) -> None:
        self._two_phase(
            self.instance(instance_id),
            LifecycleState.STOPPING,
            LifecycleState.STOPPED,
            lambda inst: inst.component.stop(),
        )

    def pause(self, instance_id: str) -> None:
        self._direct(
            self.instance(instance_id),
            LifecycleState.PAUSED,
            lambda inst: inst.component.pause(),
        )

    def resume(self, instance_id: str) -> None:
        self._direct(
            self.instance(instance_id),
            LifecycleState.RUNNING,
            lambda inst: inst.component.resume(),
        )

    def unload(self, instance_id: str) -> None:
        self._direct(
            self.instance(instance_id),
            LifecycleState.UNLOADED,
            lambda inst: inst.component.unload(),
        )

    def _two_phase(
        self,
        instance: ComponentInstance,
        transient: LifecycleState,
        settled: LifecycleState,
        hook: Callable[[ComponentInstance], None],
    ) -> None:
        # Emite el estado transitorio, ejecuta el enganche y, si va bien,
        # asienta el estado final; si falla, cae a FAILED (ADR-010).
        self._transition(instance, transient)
        if self._safe_hook(instance, hook):
            self._transition(instance, settled)

    def _direct(
        self,
        instance: ComponentInstance,
        target: LifecycleState,
        hook: Callable[[ComponentInstance], None],
    ) -> None:
        # Transicion directa (sin estado transitorio): ejecuta el enganche y,
        # si va bien, asienta el destino; si falla, cae a FAILED.
        if not can_transition(instance.state, target):
            raise IllegalTransitionError(f"{instance.state.value} -> {target.value}")
        if self._safe_hook(instance, hook):
            self._transition(instance, target)

    def _safe_hook(
        self,
        instance: ComponentInstance,
        hook: Callable[[ComponentInstance], None],
    ) -> bool:
        try:
            hook(instance)
        except Exception as exc:
            self._transition(
                instance,
                LifecycleState.FAILED,
                reason=str(exc),
                error_code=type(exc).__name__,
            )
            return False
        return True

    def _transition(
        self,
        instance: ComponentInstance,
        new: LifecycleState,
        *,
        reason: str | None = None,
        error_code: str | None = None,
    ) -> None:
        if not can_transition(instance.state, new):
            raise IllegalTransitionError(f"{instance.state.value} -> {new.value}")
        previous = instance.state
        readiness = (
            ReadinessStatus.READY
            if new is LifecycleState.RUNNING
            else ReadinessStatus.NOT_READY
        )
        health = (
            HealthStatus.UNHEALTHY
            if new in (LifecycleState.FAILED, LifecycleState.QUARANTINED)
            else HealthStatus.HEALTHY
        )
        # Emitir ANTES de aplicar el estado: si el publish falla, la excepcion
        # PROPAGA (fail-loud) y el estado local NO avanza (se conserva el
        # anterior). El estado observado nunca queda por delante del evento
        # realmente publicado en el bus (D8; ADR-010/013).
        self._emit(
            instance,
            previous=previous,
            new=new,
            health=health,
            readiness=readiness,
            reason=reason,
            error_code=error_code,
        )
        instance.state = new
        instance.readiness = readiness
        instance.health = health

    def _emit(
        self,
        instance: ComponentInstance,
        *,
        previous: LifecycleState | None,
        new: LifecycleState,
        health: HealthStatus,
        readiness: ReadinessStatus,
        reason: str | None = None,
        error_code: str | None = None,
    ) -> None:
        now = self._clock.now_ms()
        payload = ComponentLifecyclePayload(
            component_id=instance.definition.component_id,
            component_version=instance.definition.version,
            component_instance_id=instance.instance_id,
            lifecycle_scope=instance.scope,
            new_state=new,
            health_status=health,
            readiness_status=readiness,
            previous_state=previous,
            tenant_id=instance.tenant_id,
            user_id=instance.user_id,
            reason=reason,
            error_code=error_code,
        )
        stream_key = (
            f"component:{instance.definition.component_id}:{instance.instance_id}"
        )
        idempotency_key = f"{instance.instance_id}:{new.value}:{now}"
        envelope = Envelope[ComponentLifecyclePayload](
            event_type=event_type_for_state(new).value,
            event_schema_version=1,
            source=self._source,
            idempotency_key=idempotency_key,
            stream_key=stream_key,
            scope=_ENVELOPE_SCOPE[instance.scope],
            tenant_id=instance.tenant_id,
            user_id=instance.user_id,
            event_time=now,
            processing_time=now,
            correlation_id=instance.instance_id,
            payload=payload,
        )
        message = BusMessage(
            event_id=str(envelope.event_id),
            event_type=envelope.event_type,
            stream_key=envelope.stream_key,
            idempotency_key=envelope.idempotency_key,
            envelope=envelope.model_dump_json().encode("utf-8"),
        )
        self._bus.publish(envelope.event_type.split(".", 1)[0], message)
