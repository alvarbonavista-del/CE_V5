"""Data transfer objects of the EventBus port (ADR-013).

Pure transport model: the bus moves *keyed, opaque* messages. It never
imports the event contract; the canonical envelope travels serialized in
``BusMessage.envelope`` and is validated against the contract by the
producer edge (outbox relay), not by the transport. This keeps the bus
broker-agnostic (REST-15) and contract-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Offset:
    """Opaque position of a message inside a topic.

    The value is broker-specific (e.g. a Redis Streams entry id) and MUST
    be treated as opaque by producers and consumers: only the adapter
    interprets it. Used for replay (ADR-007/ADR-013).
    """

    value: str


@dataclass(frozen=True, slots=True)
class BusMessage:
    """A logical event to publish.

    ``stream_key`` drives ordering and partitioning; ``idempotency_key``
    is the stable dedup identity (ADR-003). ``envelope`` is the canonical
    envelope already serialized to bytes; the transport never inspects it.
    """

    event_id: str
    event_type: str
    stream_key: str
    idempotency_key: str
    envelope: bytes


@dataclass(frozen=True, slots=True)
class Delivery:
    """Transport metadata attached to a message when it is consumed."""

    topic: str
    consumer_group: str
    offset: Offset
    delivery_count: int


@dataclass(frozen=True, slots=True)
class ReceivedMessage:
    """A message as seen by a consumer: payload plus delivery metadata."""

    message: BusMessage
    delivery: Delivery


@dataclass(frozen=True, slots=True)
class DlqReason:
    """Why a message is routed to the dead-letter queue.

    The consumer supplies the semantic reason and attempt count; the
    adapter completes the DLQ record with owner/timestamps/procedure at
    write time (ADR-013). ``detail`` is a short human-readable note.
    """

    reason_code: str
    attempts: int
    detail: str
