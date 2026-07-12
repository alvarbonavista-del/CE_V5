"""Publisher que drena la outbox de P02b y publica al EventBus (ADR-013, ADR-006).

Lee filas no publicadas de la outbox con FOR UPDATE SKIP LOCKED, valida el
envelope opaco contra el contrato canonico ANTES de publicar (ADR-006), lo
publica por el puerto EventBus (nunca la API nativa del broker; REST-15) y
marca published_at de forma idempotente en la misma transaccion.

Validacion CA-06: el payload se valida contra su clase CONCRETA, resuelta por
event_type en el registro (source.families.registry). Antes se validaba contra
Envelope[EventPayload] base (extra=forbid, sin campos), que solo aceptaba
payloads VACIOS: la garantia de ADR-006 era ilusoria. Ahora un tipo no
registrado, un payload que no cumple su clase o un event_schema_version
incoherente FALLAN fail-loud.

At-least-once end-to-end DB->bus: si el proceso cae tras publicar y antes
del commit, las filas siguen sin publicar y se reintentan; los duplicados
los absorbe la idempotencia del consumidor (inbox de P02b). Un envelope que
no cumple el contrato NO se publica: se eleva la excepcion y la transaccion no
marca nada (nunca se avanza en silencio).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from ce_v5.core.bus import BusMessage, EventBus
from ce_v5.infra.db.ports import Database
from source.envelope import Envelope, EventPayload
from source.families.registry import (
    expected_event_schema_version,
    payload_class_for,
)


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
    _validate_contract(str(event_type), payload)
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


def _validate_contract(event_type: str, envelope: dict[str, object]) -> None:
    # Resuelve la clase concreta por event_type (CA-06). Un tipo no registrado o
    # diferido eleva su excepcion propia (UnknownEventTypePayloadError /
    # DeferredEventTypeError) y NO se publica ni se marca la fila.
    payload_cls = payload_class_for(event_type)
    _require_schema_version(event_type, envelope)
    # 1) Estructura del envelope (event_type, scope, requeridos, extra=forbid).
    #    El payload se valida aparte contra su clase, asi que aqui se sustituye
    #    por {} para no re-rechazarlo contra la base con extra=forbid.
    _validate_model(Envelope[EventPayload], {**envelope, "payload": {}}, "envelope")
    # 2) Payload contra SU clase concreta (aqui muerde extra=forbid de verdad).
    _validate_model(payload_cls, envelope.get("payload"), "payload")


def _require_schema_version(event_type: str, envelope: dict[str, object]) -> None:
    expected = expected_event_schema_version(event_type)
    actual = envelope.get("event_schema_version")
    if actual != expected:
        raise OutboxPublishError(
            f"event_schema_version {actual!r} incoherente para {event_type!r}: "
            f"el registro espera {expected} (CA-06). No se publica."
        )


def _validate_model(model: type[BaseModel], data: object, label: str) -> None:
    try:
        model.model_validate(data)
    except ValidationError as exc:
        raise OutboxPublishError(
            f"{label} invalido contra el contrato (ADR-006): {exc}"
        ) from exc
