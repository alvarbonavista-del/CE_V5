"""Tests de los contratos de la API (P06b, CA-08, ADR-019).

Estos tests son la MATERIALIZACION de dos reglas duras: el refresh token no puede
aparecer en una respuesta JSON (viaja en cookie httpOnly) y el cliente no puede colar un
tenant en una peticion (lo resuelve el backend desde la pertenencia).
"""

import pytest
from pydantic import ValidationError

from source.api import (
    ApiError,
    LoginRequest,
    MeResponse,
    RegisterRequest,
    SessionResponse,
)

_PASSWORD = "contrasena-falsa-de-test"


def test_session_response_no_declara_ningun_campo_de_refresh() -> None:
    assert not any("refresh" in campo for campo in SessionResponse.model_fields)


def test_session_response_rechaza_colar_el_refresh_token() -> None:
    # Regla dura de ADR-019: el refresh token JAMAS viaja en el JSON. Si alguien lo
    # intentase, no es que "no se recomiende": el contrato lo RECHAZA.
    with pytest.raises(ValidationError):
        SessionResponse(
            access_token="jwt",
            expires_in_seconds=900,
            user_id="u1",
            refresh_token="x",  # type: ignore[call-arg]
        )


def test_session_response_valida_con_sus_campos() -> None:
    session = SessionResponse(access_token="jwt", expires_in_seconds=900, user_id="u1")
    assert session.token_type == "bearer"


def test_register_request_rechaza_contrasena_corta() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(email="ana@ejemplo.test", password="corta123")


def test_register_request_rechaza_contrasena_gigante() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(email="ana@ejemplo.test", password="a" * 129)


def test_register_request_rechaza_un_tenant_colado() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(
            email="ana@ejemplo.test",
            password=_PASSWORD,
            tenant_id="x",  # type: ignore[call-arg]
        )


def test_login_request_rechaza_un_tenant_colado() -> None:
    # ADR-011: el tenant lo resuelve el BACKEND desde la pertenencia; el cliente no
    # puede pedir "quiero ser este tenant".
    with pytest.raises(ValidationError):
        LoginRequest(
            email="ana@ejemplo.test",
            password=_PASSWORD,
            tenant_id="x",  # type: ignore[call-arg]
        )


def test_login_request_admite_cualquier_contrasena_no_vacia() -> None:
    # Al ENTRAR no hay minimo de longitud: rechazar por corta una clave equivocada
    # filtraria informacion sobre la clave real.
    assert LoginRequest(email="ana@ejemplo.test", password="x").password == "x"


def test_me_response_exige_user_id_y_tenant_id() -> None:
    with pytest.raises(ValidationError):
        MeResponse(user_id="u1")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        MeResponse(tenant_id="t1")  # type: ignore[call-arg]
    me = MeResponse(user_id="u1", tenant_id="t1")
    assert (me.user_id, me.tenant_id) == ("u1", "t1")


def test_api_error_lleva_codigo_estable_y_mensaje() -> None:
    error = ApiError(
        code="invalid_credentials", message="Email o contrasena incorrectos."
    )
    assert error.code == "invalid_credentials"
