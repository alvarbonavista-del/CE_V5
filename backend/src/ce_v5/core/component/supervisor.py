"""Supervisor de lifecycle de Componentes (ADR-010).

Registra ComponentInstances creadas desde ComponentDefinitions (ADR-009),
las conduce por la maquina de estados de lifecycle.py validando cada
transicion, y EMITE un evento component.* por el EventBus en cada paso, con
envelope (ADR-003) y Clock (ADR-007). health_status y readiness_status se
llevan como ejes APARTE del estado (ADR-010). El supervisor NO importa
componentes: recibe ya construido el objeto que implementa
ComponentLifecycle (lo carga el composition root con el discovery).

P06 cablea la politica en el lifecycle (via el puerto LifecycleGate, para no
cerrar un ciclo con core.policy): el gate actua ANTES de INITIALIZE; una
denegacion manda la instancia a QUARANTINED (PROHIBIDA, no rota). Un fallo de
INITIALIZE es fail-fast (FAILED) si el manifest es critico, o QUARANTINED con
backoff acotado si no lo es. quarantine()/release_from_quarantine() son las
primitivas que el consumer de kill switch (CA-02) maneja: policy.* es la CAUSA,
component.quarantined la CONSECUENCIA, con causation_id al evento de politica.
El arranque topologico por dependencias sigue siendo de piezas posteriores.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from ce_v5.core.bus import BusMessage, EventBus
from ce_v5.core.clock import Clock
from ce_v5.core.component.definition import ComponentDefinition
from ce_v5.core.component.gate import (
    LifecycleGate,
    LifecycleGateRequest,
)
from ce_v5.core.component.lifecycle import ComponentLifecycle, can_transition
from ce_v5.core.manifest import ComponentManifest
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

# Estados VIVOS a efectos de kill switch (PASO 5): operativos o en camino de
# serlo. Un kill switch solo aisla instancias que estan haciendo (o a punto de
# hacer) trabajo; REGISTERED (aun no arrancada) la filtra el gate en INITIALIZE,
# y STOPPING/STOPPED/UNLOADED ya se estan apagando.
_LIVE_STATES: frozenset[LifecycleState] = frozenset(
    {
        LifecycleState.INITIALIZED,
        LifecycleState.STARTING,
        LifecycleState.RUNNING,
        LifecycleState.PAUSED,
    }
)

# Backoff acotado de reintento de INITIALIZE no critico (D9). Acotado (3) para
# que un componente roto de forma permanente aflore a humanos (FAILED) en vez de
# trillar sin fin; crecimiento exponencial para dar a un fallo transitorio de
# dependencia margen creciente; base pequena para recuperar rapido ante blips.
MAX_INIT_ATTEMPTS = 3
INIT_BACKOFF_BASE_MS = 1_000
INIT_BACKOFF_FACTOR = 2


def _backoff_ms(attempt: int) -> int:
    """Espera antes del reintento tras el intento n (1-indexado): 1s, 2s, ..."""
    return INIT_BACKOFF_BASE_MS * int(INIT_BACKOFF_FACTOR ** (attempt - 1))


def component_capability_ids(manifest: ComponentManifest) -> tuple[str, ...]:
    """Capacidades que el gate debe evaluar para el sujeto (ADR-008/012).

    Union (deduplicada, orden estable) de las capacidades cuya autorizacion el
    Componente declara necesitar: sensibles + permisos + entitlements. Los
    feature flags NO son capacidades del evaluador y quedan fuera. La comparten
    el gate (evaluar/emparejar kill switch) y el consumer de kill switch, para
    que un switch que deniega el arranque tambien aisle la instancia viva.
    """
    pr = manifest.policy_requirements
    return tuple(
        dict.fromkeys(
            (
                *pr.sensitive_capabilities,
                *pr.permissions_required,
                *pr.entitlements_required,
            )
        )
    )


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
    # Bookkeeping de las aristas de politica (D9). init_attempts cuenta los
    # INITIALIZE fallidos no criticos; next_retry_at_ms es el instante a partir
    # del cual retry_initialize acepta reintentar (backoff); quarantine_switch_id
    # recuerda que kill switch aislo la instancia, para liberarla al desactivarlo.
    init_attempts: int = 0
    next_retry_at_ms: int | None = None
    quarantine_switch_id: str | None = None


class Supervisor:
    """Registry central que conduce el lifecycle y emite component.* (ADR-010)."""

    def __init__(
        self,
        bus: EventBus,
        clock: Clock,
        *,
        source: str,
        gate: LifecycleGate | None = None,
    ) -> None:
        self._bus = bus
        self._clock = clock
        self._source = source
        # Gate de politica ANTES de INITIALIZE (ADR-010). Opcional: sin gate no
        # hay enforcement (comportamiento P04); lo cablea el composition root
        # (P06b). No es core.policy: es el puerto LifecycleGate (inversion).
        self._gate = gate
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
        """Consulta el gate y, si permite, INITIALIZE (ADR-010, P06).

        DENY del gate -> QUARANTINED (no FAILED: no esta rota, esta PROHIBIDA;
        sale cuando cambie la politica). El enganche initialize() NO se ejecuta.
        """
        self._gate_then_initialize(self.instance(instance_id), causation_id=None)

    def retry_initialize(self, instance_id: str) -> None:
        """Reintento OBSERVABLE de INITIALIZE desde FAILED o QUARANTINED (D9).

        Sin bucle oculto: es un metodo explicito. Desde QUARANTINED por backoff
        exige que el instante de reintento ya haya pasado. Desde FAILED (intentos
        agotados o fallo critico) resetea el contador: es un reintento de
        operador, no la continuacion del backoff.
        """
        instance = self.instance(instance_id)
        if (
            instance.state is LifecycleState.QUARANTINED
            and instance.next_retry_at_ms is not None
            and self._clock.now_ms() < instance.next_retry_at_ms
        ):
            faltan = instance.next_retry_at_ms - self._clock.now_ms()
            raise SupervisorError(
                f"backoff no cumplido para {instance_id}: faltan {faltan} ms"
            )
        if instance.state is LifecycleState.FAILED:
            instance.init_attempts = 0
            instance.next_retry_at_ms = None
        self._gate_then_initialize(instance, causation_id=None)

    def release_from_quarantine(
        self, instance_id: str, *, causation_id: str | None = None
    ) -> None:
        """Libera una instancia de QUARANTINED reintentando INITIALIZE (D9).

        Re-consulta el gate: si la causa que la puso en cuarentena (p.ej. un kill
        switch) ya no deniega, arranca; si sigue denegando, permanece en
        cuarentena (no-op, sin re-emitir). causation_id apunta al evento que la
        libero (p.ej. policy.kill_switch_deactivated), para la cadena causal.
        """
        instance = self.instance(instance_id)
        if instance.state is not LifecycleState.QUARANTINED:
            raise SupervisorError(f"{instance_id} no esta en QUARANTINED")
        self._gate_then_initialize(instance, causation_id=causation_id)

    def quarantine(
        self,
        instance_id: str,
        *,
        reason_code: str,
        causation_id: str,
        switch_id: str | None = None,
    ) -> None:
        """Aisla una instancia VIVA en QUARANTINED (PASO 5, frontera CA-02).

        Primitiva que maneja el consumer de kill switch: policy.* es la CAUSA y
        component.quarantined la CONSECUENCIA; causation_id apunta al event_id del
        policy.kill_switch_activated que la provoco. El kill switch NUNCA se emite
        como component.*. reason_code viaja tal cual al evento (depurable).
        """
        instance = self.instance(instance_id)
        self._transition(
            instance,
            LifecycleState.QUARANTINED,
            reason=reason_code,
            error_code=reason_code,
            causation_id=causation_id,
        )
        instance.quarantine_switch_id = switch_id

    def live_instances(self) -> list[ComponentInstance]:
        """Instancias en estado VIVO (candidatas a kill switch, PASO 5)."""
        return [i for i in self._instances.values() if i.state in _LIVE_STATES]

    def quarantined_instances(self) -> list[ComponentInstance]:
        """Instancias actualmente en QUARANTINED (candidatas a liberacion)."""
        return [
            i for i in self._instances.values() if i.state is LifecycleState.QUARANTINED
        ]

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

    def _gate_request(self, instance: ComponentInstance) -> LifecycleGateRequest:
        manifest = instance.definition.manifest
        return LifecycleGateRequest(
            scope=instance.scope,
            tenant_id=instance.tenant_id,
            user_id=instance.user_id,
            required_capabilities=component_capability_ids(manifest),
            critical=manifest.critical,
        )

    def _gate_then_initialize(
        self, instance: ComponentInstance, *, causation_id: str | None
    ) -> None:
        if self._gate is not None:
            verdict = self._gate.check_initialize(self._gate_request(instance))
            if not verdict.allowed:
                if instance.state is LifecycleState.QUARANTINED:
                    # Ya en cuarentena y sigue denegada: no-op, sin re-emitir.
                    return
                self._transition(
                    instance,
                    LifecycleState.QUARANTINED,
                    reason=verdict.reason_code,
                    error_code=verdict.reason_code,
                    causation_id=verdict.causation_id or causation_id,
                )
                return
        self._transition(
            instance, LifecycleState.INITIALIZING, causation_id=causation_id
        )
        if self._safe_init_hook(instance):
            self._transition(instance, LifecycleState.INITIALIZED)
            # Exito: se limpia el bookkeeping de reintento/cuarentena.
            instance.init_attempts = 0
            instance.next_retry_at_ms = None
            instance.quarantine_switch_id = None

    def _safe_init_hook(self, instance: ComponentInstance) -> bool:
        try:
            instance.component.initialize()
        except Exception as exc:
            self._handle_init_failure(instance, exc)
            return False
        return True

    def _handle_init_failure(self, instance: ComponentInstance, exc: Exception) -> None:
        # Aristas de politica del fallo de INITIALIZE (D9). A diferencia del
        # _safe_hook generico (que siempre cae a FAILED), aqui la criticidad
        # decide: critico -> fail-fast; no critico -> cuarentena con backoff.
        reason, code = str(exc), type(exc).__name__
        if instance.definition.manifest.critical:
            self._transition(
                instance, LifecycleState.FAILED, reason=reason, error_code=code
            )
            return
        instance.init_attempts += 1
        if instance.init_attempts >= MAX_INIT_ATTEMPTS:
            # Reintentos agotados: aflora a FAILED para atencion humana; el
            # reintento desde FAILED es explicito (retry_initialize).
            instance.next_retry_at_ms = None
            self._transition(
                instance, LifecycleState.FAILED, reason=reason, error_code=code
            )
            return
        self._transition(
            instance, LifecycleState.QUARANTINED, reason=reason, error_code=code
        )
        instance.next_retry_at_ms = self._clock.now_ms() + _backoff_ms(
            instance.init_attempts
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
        causation_id: str | None = None,
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
            causation_id=causation_id,
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
        causation_id: str | None = None,
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
            causation_id=causation_id,
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
