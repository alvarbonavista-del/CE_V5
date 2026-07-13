"""Los dos procesos de fondo de la API (P06b). Ninguno evalua reglas ni ejecuta ordenes.

FRONTERA DURA (DOC_ROADMAP, ficha P06b): la API PUBLICA y CONSUME eventos, pero NO
EVALUA REGLAS NI EJECUTA ORDENES. Eso es de otras piezas, para siempre.

DRENADO DE LA OUTBOX: la API escribe sus eventos en la outbox dentro de la transaccion
de negocio, y un bucle los publica al bus. Es el patron de P02b/P03: la transaccion no
puede publicar directamente al bus (si el commit fallara despues, habria un evento de
algo que nunca paso).

CONSUMO DE policy.*: la API mantiene un cache del capability set (ADR-012). Un kill
switch tiene que invalidarlo EN CALIENTE, no cuando caduque el TTL.

POR QUE CURSOR PRIVADO Y NO CONSUMER GROUP (esto importa): un kill switch tiene que
llegar a TODAS las instancias de la API. Con un grupo de consumidores compartido, el
evento le tocaria a UNA sola instancia y las demas seguirian concediendo permisos con su
cache viejo. Es exactamente la distincion que el puerto EventBus deja escrita: grupo =
reparto de trabajo; cursor privado = todos ven todo.
"""

from __future__ import annotations

import json
import threading
from types import TracebackType

from ce_v5.core.bus import EventBus, Offset, UnknownOffsetError
from ce_v5.core.policy.invalidation import PolicyCacheInvalidator
from ce_v5.entrypoints.api.observability import log_event
from ce_v5.infra.db.outbox_publisher import OutboxPublisher, topic_for
from source.families.policy import (
    KillSwitchPayload,
    PolicyEventType,
    PolicyVersionPublishedPayload,
    SubjectInvalidatedPayload,
)

DRAIN_INTERVAL_MS = 200
POLL_INTERVAL_MS = 200
_BATCH = 100

# El topic se deriva de la familia del evento (ADR-004), igual que en el publisher.
POLICY_TOPIC = topic_for(PolicyEventType.VERSION_PUBLISHED.value)


class _Loop:
    """Bucle de fondo con arranque y parada LIMPIOS: nunca deja un hilo huerfano."""

    def __init__(self, nombre: str, intervalo_ms: int) -> None:
        self._nombre = nombre
        self._intervalo = intervalo_ms / 1000
        self._parar = threading.Event()
        self._hilo: threading.Thread | None = None

    def _tick(self) -> None:  # pragma: no cover - lo implementan las subclases
        raise NotImplementedError

    def _run(self) -> None:
        while not self._parar.is_set():
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001
                # FAIL-LOUD: se registra y se reintenta. Un bucle de fondo que se traga
                # los errores en silencio deja de publicar (o de invalidar) y nadie se
                # entera hasta que algo grave depende de ello.
                log_event(
                    f"{self._nombre}.error",
                    error=type(exc).__name__,
                    detail=str(exc)[:200],
                )
            self._parar.wait(self._intervalo)

    def start(self) -> None:
        if self._hilo is not None:
            return
        self._parar.clear()
        self._hilo = threading.Thread(target=self._run, name=self._nombre, daemon=True)
        self._hilo.start()

    def stop(self) -> None:
        self._parar.set()
        if self._hilo is not None:
            self._hilo.join(timeout=5)
            self._hilo = None

    def __enter__(self) -> _Loop:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()


class OutboxDrainer(_Loop):
    """Publica al bus lo que la API dejo en la outbox. No decide nada."""

    def __init__(
        self, publisher: OutboxPublisher, interval_ms: int = DRAIN_INTERVAL_MS
    ) -> None:
        super().__init__("outbox_drainer", interval_ms)
        self._publisher = publisher

    def _tick(self) -> None:
        publicados = self._publisher.drain_once(batch_size=_BATCH)
        if publicados:
            log_event("outbox.drained", count=publicados)


class PolicyInvalidationSubscriber(_Loop):
    """Consume policy.* con CURSOR PRIVADO e invalida el cache del capability set.

    Arranca en el final del topic (latest_offset): al arrancar, lo que importa es lo que
    pase DESDE AHORA. Reproducir el historico de kill switches solo serviria para
    invalidar un cache que acaba de nacer vacio.
    """

    def __init__(
        self,
        bus: EventBus,
        invalidator: PolicyCacheInvalidator,
        topic: str = POLICY_TOPIC,
        interval_ms: int = POLL_INTERVAL_MS,
    ) -> None:
        super().__init__("policy_invalidation", interval_ms)
        self._bus = bus
        self._invalidator = invalidator
        self._topic = topic
        self._cursor: Offset | None = None
        self._arrancado = False

    def start(self) -> None:
        self._situar_cursor()
        super().start()

    def _situar_cursor(self) -> None:
        if not self._arrancado:
            self._cursor = self._bus.latest_offset(self._topic)
            self._arrancado = True

    def _tick(self) -> None:
        self._situar_cursor()
        try:
            recibidos = self._bus.replay(
                self._topic, start=self._cursor, max_messages=_BATCH
            )
        except UnknownOffsetError:
            # El cursor ya no existe en el historial: no se avanza en silencio. Se
            # vuelve al final y se invalida TODO, que es lo conservador (recomputar es
            # barato; servir un permiso ya retirado, no).
            self._cursor = self._bus.latest_offset(self._topic)
            self._invalidator.on_version_published(
                PolicyVersionPublishedPayload(policy_version="unknown", actor="bus")
            )
            return
        for recibido in recibidos:
            self._cursor = recibido.delivery.offset
            self._aplicar(recibido.message.event_type, recibido.message.envelope)

    def _aplicar(self, event_type: str, envelope_bytes: bytes) -> None:
        envelope = json.loads(envelope_bytes)
        if not isinstance(envelope, dict):
            return
        payload = envelope.get("payload", {})
        if event_type in (
            PolicyEventType.KILL_SWITCH_ACTIVATED.value,
            PolicyEventType.KILL_SWITCH_DEACTIVATED.value,
        ):
            self._invalidator.on_kill_switch_changed(
                KillSwitchPayload.model_validate(payload)
            )
        elif event_type == PolicyEventType.VERSION_PUBLISHED.value:
            self._invalidator.on_version_published(
                PolicyVersionPublishedPayload.model_validate(payload)
            )
        elif event_type == PolicyEventType.SUBJECT_INVALIDATED.value:
            self._invalidator.on_subject_invalidated(
                SubjectInvalidatedPayload.model_validate(payload)
            )
        else:
            return
        log_event("policy.cache_invalidated", event_type=event_type)
