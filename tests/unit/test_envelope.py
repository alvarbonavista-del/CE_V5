from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from envelope import ENVELOPE_VERSION, Envelope, EventPayload, Scope


def _base_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "event_type": "market.tick",
        "event_schema_version": 1,
        "source": "test",
        "idempotency_key": "idem",
        "stream_key": "stream",
        "scope": Scope.PUBLIC_MARKET,
        "correlation_id": "corr",
        "payload": EventPayload(),
    }
    kwargs.update(overrides)
    return kwargs


def test_envelope_valido_public_market() -> None:
    env = Envelope[EventPayload](**_base_kwargs())
    assert env.event_type == "market.tick"
    assert env.envelope_version == ENVELOPE_VERSION
    assert env.tenant_id is None


def test_tenant_requiere_tenant_id() -> None:
    with pytest.raises(ValidationError):
        Envelope[EventPayload](**_base_kwargs(scope=Scope.TENANT))


def test_tenant_ok_con_tenant_id() -> None:
    env = Envelope[EventPayload](**_base_kwargs(scope=Scope.TENANT, tenant_id="t1"))
    assert env.tenant_id == "t1"


def test_public_market_prohibe_tenant_id() -> None:
    with pytest.raises(ValidationError):
        Envelope[EventPayload](**_base_kwargs(tenant_id="t1"))


def test_user_requiere_user_id() -> None:
    with pytest.raises(ValidationError):
        Envelope[EventPayload](**_base_kwargs(scope=Scope.USER, tenant_id="t1"))
    env = Envelope[EventPayload](
        **_base_kwargs(scope=Scope.USER, tenant_id="t1", user_id="u1")
    )
    assert env.user_id == "u1"


def test_user_id_prohibido_fuera_de_user() -> None:
    with pytest.raises(ValidationError):
        Envelope[EventPayload](**_base_kwargs(user_id="u1"))


def test_event_type_invalido() -> None:
    with pytest.raises(ValidationError):
        Envelope[EventPayload](**_base_kwargs(event_type="desconocido.accion"))


def test_campos_requeridos_no_vacios() -> None:
    with pytest.raises(ValidationError):
        Envelope[EventPayload](**_base_kwargs(correlation_id=""))


def test_envelope_es_inmutable() -> None:
    env = Envelope[EventPayload](**_base_kwargs())
    campo = "source"
    with pytest.raises(ValidationError):
        setattr(env, campo, "otro")


def test_ranuras_de_tiempo_opcionales() -> None:
    now = datetime.now(UTC)
    env = Envelope[EventPayload](**_base_kwargs(event_time=now))
    assert env.event_time == now
    assert env.ingestion_time is None
