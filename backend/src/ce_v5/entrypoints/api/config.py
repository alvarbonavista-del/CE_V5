"""Configuracion de la puerta publica (P06b, dictamen CSA C/E/M).

CADENA DE PROXY DE CONFIANZA: X-Forwarded-For la escribe QUIEN LLAMA. Confiar en ella
sin saber cuantos proxies propios hay delante permite a cualquiera FALSIFICAR SU IP y
burlar el limitador y el geo-bloqueo. Por eso:
  CE_V5_TRUSTED_PROXY_COUNT = 0 (POR DEFECTO): se IGNORA X-Forwarded-For por completo y
  se usa la IP de la conexion. Es el valor seguro.
  CE_V5_TRUSTED_PROXY_COUNT = N: se toma la IP que esta N saltos a la izquierda del
  final de la cadena, que es la ultima que NO pudo falsificar el cliente.

GUARDIAS DE ARRANQUE: la configuracion insegura no se avisa, se RECHAZA. Un despliegue
con un comodin en CORS o con cookies sin Secure no arranca. Un aviso en un log lo lee
alguien tres semanas despues; una excepcion la lee quien despliega, ahora.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

ENV_ENV_VAR = "CE_V5_ENV"
TRUSTED_PROXY_COUNT_ENV_VAR = "CE_V5_TRUSTED_PROXY_COUNT"
CORS_ALLOWED_ORIGINS_ENV_VAR = "CE_V5_CORS_ALLOWED_ORIGINS"
COOKIE_SECURE_ENV_VAR = "CE_V5_COOKIE_SECURE"
MAX_BODY_BYTES_ENV_VAR = "CE_V5_MAX_BODY_BYTES"

_PRODUCTION = "production"
_DEFAULT_MAX_BODY_BYTES = 65_536


class ApiConfigError(RuntimeError):
    """Error de configuracion de la puerta publica."""


def _bool_from_env(env: Mapping[str, str], var: str, default: bool) -> bool:
    raw = env.get(var, "").strip().lower()
    if not raw:
        return default
    if raw in ("true", "1", "yes"):
        return True
    if raw in ("false", "0", "no"):
        return False
    raise ApiConfigError(f"{var} debe ser true o false.")


@dataclass(frozen=True, slots=True)
class ApiConfig:
    """Parametros de la puerta publica."""

    environment: str = "development"
    trusted_proxy_count: int = 0
    allowed_origins: tuple[str, ...] = ()
    cookie_secure: bool = True
    max_body_bytes: int = _DEFAULT_MAX_BODY_BYTES

    def __post_init__(self) -> None:
        if self.trusted_proxy_count < 0:
            raise ApiConfigError(
                f"{TRUSTED_PROXY_COUNT_ENV_VAR} no puede ser negativo."
            )
        if "*" in self.allowed_origins:
            raise ApiConfigError(
                "Un comodin '*' en los origenes de CORS esta PROHIBIDO: con "
                "credenciales, permitiria a CUALQUIER web del mundo hacer peticiones "
                "autenticadas con la cookie del usuario. No es una mala practica: es "
                "la puerta abierta. Declara los origenes uno a uno."
            )
        if not self.cookie_secure and self.is_production:
            raise ApiConfigError(
                "Las cookies sin Secure viajan en claro y cualquiera en la red las "
                "copia. En produccion no se admite: la aplicacion no arranca."
            )
        if self.max_body_bytes <= 0:
            raise ApiConfigError(f"{MAX_BODY_BYTES_ENV_VAR} debe ser positivo.")

    @property
    def is_production(self) -> bool:
        """True si el entorno activa los guardias duros de arranque."""
        return self.environment == _PRODUCTION

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ApiConfig:
        """Lee el entorno. El valor SEGURO por defecto es no fiarse de ningun proxy."""
        env: Mapping[str, str] = os.environ if environ is None else environ
        raw = env.get(TRUSTED_PROXY_COUNT_ENV_VAR, "").strip()
        try:
            count = int(raw) if raw else 0
        except ValueError as exc:
            raise ApiConfigError(
                f"{TRUSTED_PROXY_COUNT_ENV_VAR} debe ser un entero."
            ) from exc

        raw_bytes = env.get(MAX_BODY_BYTES_ENV_VAR, "").strip()
        try:
            max_body = int(raw_bytes) if raw_bytes else _DEFAULT_MAX_BODY_BYTES
        except ValueError as exc:
            raise ApiConfigError(
                f"{MAX_BODY_BYTES_ENV_VAR} debe ser un entero."
            ) from exc

        origins = tuple(
            origin.strip()
            for origin in env.get(CORS_ALLOWED_ORIGINS_ENV_VAR, "").split(",")
            if origin.strip()
        )
        return cls(
            environment=env.get(ENV_ENV_VAR, "development").strip() or "development",
            trusted_proxy_count=count,
            allowed_origins=origins,
            cookie_secure=_bool_from_env(env, COOKIE_SECURE_ENV_VAR, True),
            max_body_bytes=max_body,
        )
