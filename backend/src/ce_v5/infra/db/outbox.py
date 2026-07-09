"""Primitiva de escritura transaccional con outbox (ADR-013).

Escribe, en UNA sola transaccion, una o varias filas de negocio junto con
su fila de outbox. Si algo falla, la transaccion hace rollback y no queda
escrita ninguna de las dos partes (atomicidad DB-outbox). Depende solo de
los puertos de ports.py y de la libreria estandar; no conoce el driver.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from uuid import UUID

from ce_v5.infra.db.ports import Database, Session, SqlParams

_INSERT_OUTBOX = (
    "INSERT INTO outbox "
    "(event_id, idempotency_key, stream_key, event_type, envelope) "
    "VALUES (%(event_id)s, %(idempotency_key)s, %(stream_key)s, "
    "%(event_type)s, %(envelope)s::jsonb)"
)


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """Identidad y contenido de un evento a encolar en la outbox (ADR-003)."""

    event_id: UUID
    idempotency_key: str
    stream_key: str
    event_type: str
    envelope: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key es obligatorio (ADR-003).")
        if not self.stream_key.strip():
            raise ValueError("stream_key es obligatorio (ADR-003).")
        if not self.event_type.strip():
            raise ValueError("event_type es obligatorio (ADR-004).")


def enqueue_event(session: Session, event: OutboxEvent) -> None:
    """Inserta una fila de outbox en la transaccion activa (no hace commit)."""
    session.execute(
        _INSERT_OUTBOX,
        {
            "event_id": event.event_id,
            "idempotency_key": event.idempotency_key,
            "stream_key": event.stream_key,
            "event_type": event.event_type,
            "envelope": json.dumps(dict(event.envelope), sort_keys=True),
        },
    )


def write_atomically(
    db: Database,
    *,
    business: Iterable[tuple[str, SqlParams]],
    event: OutboxEvent,
) -> None:
    """Escribe filas de negocio + su fila de outbox en UNA transaccion.

    Si cualquier sentencia falla, la transaccion hace rollback y no queda
    escrita ni la fila de negocio ni la de outbox (atomicidad; ADR-013).
    """
    with db.transaction() as session:
        for query, params in business:
            session.execute(query, params)
        enqueue_event(session, event)
