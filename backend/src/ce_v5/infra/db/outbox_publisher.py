"""Publisher que drena la outbox de P02b y publica al EventBus (ADR-013, ADR-006).

Lee filas no publicadas de la outbox con FOR UPDATE SKIP LOCKED, valida el
envelope opaco contra el contrato canonico ANTES de publicar (ADR-006), lo
publica por el puerto EventBus (nunca la API nativa del broker; REST-15) y
marca published_at de forma idempotente en la misma transaccion.

At-least-once end-to-end DB->bus: si el proceso cae tras publicar y antes
del commit, las filas siguen sin publicar y se reintentan; los duplicados
los absorbe la idempotencia del consumidor (inbox de P02b). Un envelope que
no cumple el contrato NO se publica: se eleva OutboxPublishError y la
transaccion no marca nada (nunca se avanza en silencio).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from ce_v5.core.bus import BusMessage, EventBus
from ce_v5.infra.db.ports import Database
from source.envelope import Envelope, EventPayload


class OutboxPublishError(RuntimeError):
    """Un envelope de la outbox no cumple el contrato canonico (ADR-006)."""


_SELECT_UNPUBLISHED = (
    "SELECT id, event_id, event_type, stream_key, idempotency_key, envelope "
    "FROM outbox WHERE published_at IS NULL "
    "ORDER BY id LIMIT %(limit)s FOR UPDATE SKIP LOCKED"
)
_MARK_PUBLISHED = "UPDATE outbox SET published_at = now() WHERE id = ANY(%(ids)s)"


def topic_for(event_type: str) -> str:
    """Deriva el topic del bus de la familia del evento (ADR-004)."""
    return event_type.split(".", 1)[0]


@dataclass(frozen=True, slots=True)
class OutboxPublisher:
    """Drena la outbox y publica al bus, validando el contrato (ADR-006)."""

    db: Database
    bus: EventBus

    def drain_once(self, *, batch_size: int = 100) -> int:
        """Publica hasta ``batch_size`` eventos pendientes. Devuelve cuantos.

        Leer, publicar y marcar ocurren en UNA transaccion que bloquea las
        filas seleccionadas; otras instancias del publisher las saltan
        (SKIP LOCKED).
        """
        with self.db.transaction() as session:
            rows = session.fetchall(_SELECT_UNPUBLISHED, {"limit": batch_size})
            if not rows:
                return 0
            published_ids: list[int] = []
            for row in rows:
                message = _to_message(row)
                self.bus.publish(topic_for(message.event_type), message)
                published_ids.append(_row_id(row))
            session.execute(_MARK_PUBLISHED, {"ids": published_ids})
            return len(published_ids)


def _row_id(row: tuple[object, ...]) -> int:
    value = row[0]
    if not isinstance(value, int):
        raise OutboxPublishError(f"id de outbox no es entero: {value!r}")
    return value


def _to_message(row: tuple[object, ...]) -> BusMessage:
    _, event_id, event_type, stream_key, idempotency_key, envelope = row
    payload = _as_envelope_dict(envelope)
    _validate_contract(payload)
    return BusMessage(
        event_id=str(event_id),
        event_type=str(event_type),
        stream_key=str(stream_key),
        idempotency_key=str(idempotency_key),
        envelope=json.dumps(payload, sort_keys=True).encode(),
    )


def _as_envelope_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise OutboxPublishError(
            f"el envelope de la outbox no es un objeto JSON: {type(value)!r}"
        )
    return value


def _validate_contract(payload: dict[str, object]) -> None:
    try:
        Envelope[EventPayload].model_validate(payload)
    except ValidationError as exc:
        raise OutboxPublishError(
            f"envelope invalido contra el contrato (ADR-006): {exc}"
        ) from exc
