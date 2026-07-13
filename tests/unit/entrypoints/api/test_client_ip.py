"""Tests de la IP efectiva (P06b, dictamen CSA c/A). Sin montar una app entera.

El test clave es el primero: con 0 proxies de confianza, X-Forwarded-For se IGNORA. Esa
es la defensa contra la falsificacion de IP, que dejaria inutiles el limitador y el
geo-bloqueo.
"""

from typing import Any, cast

from fastapi import Request

from ce_v5.entrypoints.api.client_ip import client_ip
from ce_v5.entrypoints.api.config import ApiConfig

_CONEXION = "203.0.113.10"  # TEST-NET-3: ficticia, jamas una IP real.
_MENTIRA = "198.51.100.7"  # la que el cliente pretende hacerse pasar.


class _FakeClient:
    def __init__(self, host: str) -> None:
        self.host = host


class _FakeRequest:
    """Doble minimo de Request: solo lo que client_ip mira."""

    def __init__(self, host: str | None, forwarded_for: str | None = None) -> None:
        self.client = None if host is None else _FakeClient(host)
        self.headers: dict[str, str] = {}
        if forwarded_for is not None:
            self.headers["x-forwarded-for"] = forwarded_for


def _request(host: str | None, forwarded_for: str | None = None) -> Request:
    return cast(Request, cast(Any, _FakeRequest(host, forwarded_for)))


def test_sin_proxies_de_confianza_se_ignora_x_forwarded_for() -> None:
    # El cliente MIENTE en la cabecera. Sin proxies propios delante, esa cabecera no
    # vale nada: se usa la IP de la conexion, que no se puede falsificar.
    config = ApiConfig(trusted_proxy_count=0)
    assert client_ip(_request(_CONEXION, _MENTIRA), config) == _CONEXION


def test_con_un_proxy_se_toma_el_ultimo_de_la_cadena() -> None:
    config = ApiConfig(trusted_proxy_count=1)
    cadena = f"{_MENTIRA}, 192.0.2.1"
    # El ultimo lo escribio NUESTRO proxy: es el que el cliente no pudo tocar.
    assert client_ip(_request(_CONEXION, cadena), config) == "192.0.2.1"


def test_con_dos_proxies_se_toma_el_penultimo() -> None:
    config = ApiConfig(trusted_proxy_count=2)
    cadena = f"{_MENTIRA}, 192.0.2.1, 192.0.2.2"
    assert client_ip(_request(_CONEXION, cadena), config) == "192.0.2.1"


def test_una_cadena_mas_corta_de_lo_esperado_es_sospechosa() -> None:
    # Faltan saltos: o la topologia no es la que se declaro, o alguien manipulo la
    # cabecera. No se adivina: se cae a la IP de la conexion.
    config = ApiConfig(trusted_proxy_count=3)
    assert client_ip(_request(_CONEXION, _MENTIRA), config) == _CONEXION


def test_sin_cliente_conocido_no_hay_ip() -> None:
    assert client_ip(_request(None), ApiConfig(trusted_proxy_count=0)) is None
