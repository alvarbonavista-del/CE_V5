"""The EventBus port: our own abstraction over any broker (ADR-013).

Producers and consumers depend on this Protocol, never on the native
broker API (REST-15). The Redis Streams implementation lives in
``ce_v5.infra.bus_redis`` and is wired at the composition root.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ce_v5.core.bus.message import (
    BusMessage,
    Delivery,
    DlqReason,
    Offset,
    ReceivedMessage,
)


@runtime_checkable
class EventBus(Protocol):
    """At-least-once event transport with consumer groups and replay.

    Ordering is guaranteed per ``stream_key``. Consumers are idempotent
    (ADR-013): they ACK only after persisting the effect, so redelivery
    is safe.

    DOS MODOS DE CONSUMO, Y NO SON INTERCAMBIABLES:

    poll + ack (consumer group): consumo COORDINADO y COMPARTIDO entre varios
    workers. El bus lleva el estado de quien ha confirmado que. Es lo que quieren los
    workers (at-least-once, reparto de carga, DLQ).

    replay + cursor privado: consumo INDIVIDUAL y reanudable. El estado (por donde voy)
    lo lleva el CLIENTE, en su checkpoint. Es lo que quiere un canal realtime por
    usuario.

    Un consumer group POR USUARIO seria un error de diseno: crearia miles de grupos con
    estado de ACK que el bus tendria que mantener para siempre, incluso para clientes
    que no volveran. El cursor privado no le cuesta nada al bus.

    latest_offset existe para que una suscripcion SIN checkpoint arranque en el final
    REAL del topic. Sin el, habria que recorrer el historico para saber donde termina.
    """

    def publish(self, topic: str, message: BusMessage) -> Offset:
        """Append ``message`` to ``topic`` and return its assigned offset."""
        ...

    def ensure_group(self, topic: str, consumer_group: str) -> None:
        """Create ``consumer_group`` on ``topic`` if absent (idempotent)."""
        ...

    def poll(
        self,
        topic: str,
        consumer_group: str,
        consumer_name: str,
        *,
        max_messages: int,
        block_ms: int,
    ) -> tuple[ReceivedMessage, ...]:
        """Fetch up to ``max_messages`` new messages for this consumer.

        ``max_messages`` bounds in-flight work (backpressure). ``block_ms``
        is how long to wait for new messages before returning empty.
        """
        ...

    def ack(self, delivery: Delivery) -> None:
        """Confirm a message as processed; it will not be redelivered."""
        ...

    def claim_stale(
        self,
        topic: str,
        consumer_group: str,
        consumer_name: str,
        *,
        min_idle_ms: int,
        max_messages: int,
    ) -> tuple[ReceivedMessage, ...]:
        """Reclaim messages left pending by a crashed/slow consumer.

        This is what makes a consumer restart lose nothing: messages
        delivered but never ACKed become claimable after ``min_idle_ms``.
        """
        ...

    def dead_letter(self, received: ReceivedMessage, reason: DlqReason) -> None:
        """Route a message to the observable DLQ and ACK the original."""
        ...

    def latest_offset(self, topic: str) -> Offset | None:
        """Position of the LAST entry in ``topic``, or None if it is empty.

        Lets a subscription with no checkpoint start at the REAL end of the topic,
        without walking the history to find out where it ends. The returned offset means
        "already seen": ``replay`` from it is EXCLUSIVE, so nothing old is redelivered.
        """
        ...

    def replay(
        self,
        topic: str,
        *,
        start: Offset | None,
        max_messages: int,
    ) -> tuple[ReceivedMessage, ...]:
        """Read historical messages from ``start`` (or the beginning).

        Independent of consumer groups (ADR-007). If ``start`` points at a
        trimmed/removed offset, the adapter raises ``UnknownOffsetError``
        rather than silently skipping (ADR-013).
        """
        ...
