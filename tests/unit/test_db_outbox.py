"""Tests unitarios del evento de outbox (validacion sin base de datos)."""

import uuid

import pytest

from ce_v5.infra.db.outbox import OutboxEvent


def _make(
    *,
    idempotency_key: str = "idem-1",
    stream_key: str = "stream-1",
    event_type: str = "component.demo",
) -> OutboxEvent:
    return OutboxEvent(
        event_id=uuid.uuid4(),
        idempotency_key=idempotency_key,
        stream_key=stream_key,
        event_type=event_type,
        envelope={"k": "v"},
    )


def test_outbox_event_valido_se_construye() -> None:
    event = _make()
    assert event.stream_key == "stream-1"


def test_idempotency_key_vacio_falla() -> None:
    with pytest.raises(ValueError):
        _make(idempotency_key="   ")


def test_stream_key_vacio_falla() -> None:
    with pytest.raises(ValueError):
        _make(stream_key="")


def test_event_type_vacio_falla() -> None:
    with pytest.raises(ValueError):
        _make(event_type="")
