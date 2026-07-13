"""Tests de la proteccion CSRF por doble envio (P06b, dictamen CSA B/G).

La defensa se apoya en algo que la pagina atacante NO PUEDE HACER: leer nuestra cookie
para copiarla en una cabecera. Sin las dos mitades, no se pasa.
"""

from typing import Any, cast

import pytest
from fastapi import Request

from ce_v5.entrypoints.api.config import ApiConfig
from ce_v5.entrypoints.api.csrf import CsrfError, new_csrf_token, verify_csrf

_TOKEN = "token-csrf-de-test"
_ORIGEN = "https://app.ejemplo.test"


class _FakeRequest:
    """Doble minimo de Request: solo lo que verify_csrf mira."""

    def __init__(
        self,
        cookie: str | None = _TOKEN,
        header: str | None = _TOKEN,
        origin: str | None = None,
    ) -> None:
        self.cookies: dict[str, str] = {}
        if cookie is not None:
            self.cookies["ce_v5_csrf"] = cookie
        self.headers: dict[str, str] = {}
        if header is not None:
            self.headers["x-csrf-token"] = header
        if origin is not None:
            self.headers["origin"] = origin


def _request(
    cookie: str | None = _TOKEN,
    header: str | None = _TOKEN,
    origin: str | None = None,
) -> Request:
    return cast(Request, cast(Any, _FakeRequest(cookie, header, origin)))


def _config(origins: tuple[str, ...] = ()) -> ApiConfig:
    return ApiConfig(allowed_origins=origins)


def test_cabecera_y_cookie_iguales_pasan() -> None:
    verify_csrf(_request(), _config())


def test_cabecera_distinta_falla() -> None:
    with pytest.raises(CsrfError):
        verify_csrf(_request(header="otro-token"), _config())


def test_sin_cabecera_falla() -> None:
    # La pagina atacante puede provocar que el navegador ENVIE la cookie, pero no puede
    # LEERLA para rellenar la cabecera. Sin esa mitad, no se pasa.
    with pytest.raises(CsrfError):
        verify_csrf(_request(header=None), _config())


def test_sin_cookie_falla() -> None:
    with pytest.raises(CsrfError):
        verify_csrf(_request(cookie=None), _config())


def test_origen_no_admitido_falla() -> None:
    with pytest.raises(CsrfError):
        verify_csrf(_request(origin="https://web-atacante.test"), _config())


def test_origen_admitido_pasa() -> None:
    verify_csrf(_request(origin=_ORIGEN), _config(origins=(_ORIGEN,)))


def test_un_token_de_longitud_distinta_falla_sin_reventar() -> None:
    # hmac.compare_digest tolera longitudes distintas y devuelve False; un == normal
    # cortaria en el primer caracter distinto y el TIEMPO de respuesta iria revelando el
    # token caracter a caracter.
    #
    # El tiempo NO se mide en este test a proposito: una medicion fiable exige muchas
    # repeticiones y una maquina sin ruido, y en CI daria falsos rojos. Lo que se puede
    # afirmar aqui es que se usa la primitiva correcta y que no lanza excepciones raras.
    with pytest.raises(CsrfError):
        verify_csrf(_request(header=_TOKEN + "-mas-largo"), _config())
    with pytest.raises(CsrfError):
        verify_csrf(_request(header="x"), _config())


def test_los_tokens_generados_son_distintos() -> None:
    assert new_csrf_token() != new_csrf_token()
