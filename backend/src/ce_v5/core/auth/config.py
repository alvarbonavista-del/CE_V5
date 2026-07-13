"""Configuracion de autenticacion (P06b, ADR-019).

El secreto de firma NO tiene valor por defecto y NO vive en el repositorio (CE-13):
si falta o es corto, la aplicacion se NIEGA a arrancar. Un secreto debil no es una
molestia menor: con el, cualquiera se fabrica un pase valido y la autenticacion
entera deja de significar nada.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

JWT_SECRET_ENV_VAR = "CE_V5_JWT_SECRET"
ACCESS_TTL_ENV_VAR = "CE_V5_ACCESS_TOKEN_TTL_SECONDS"
REFRESH_TTL_ENV_VAR = "CE_V5_REFRESH_TOKEN_TTL_SECONDS"

# Identidad del emisor y del destinatario del token. Se EXIGEN al verificar: un token
# emitido para otro sistema no vale aqui.
TOKEN_ISSUER = "ce_v5"
TOKEN_AUDIENCE = "ce_v5-api"

_MIN_SECRET_LENGTH = 32
_DEFAULT_ACCESS_TTL_SECONDS = 900
_DEFAULT_REFRESH_TTL_SECONDS = 1_209_600


class AuthConfigError(RuntimeError):
    """Error de configuracion de autenticacion."""


def _int_from_env(env: Mapping[str, str], var: str, default: int) -> int:
    raw = env.get(var, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise AuthConfigError(f"{var} debe ser un entero de segundos.") from exc


@dataclass(frozen=True, slots=True)
class AuthConfig:
    """Parametros de emision y verificacion de tokens."""

    jwt_secret: str
    access_ttl_seconds: int = _DEFAULT_ACCESS_TTL_SECONDS
    refresh_ttl_seconds: int = _DEFAULT_REFRESH_TTL_SECONDS

    def __post_init__(self) -> None:
        if len(self.jwt_secret) < _MIN_SECRET_LENGTH:
            raise AuthConfigError(
                f"El secreto de firma debe tener al menos {_MIN_SECRET_LENGTH} "
                "caracteres: con un secreto debil, cualquiera puede fabricarse un "
                "token valido."
            )
        if self.access_ttl_seconds <= 0 or self.refresh_ttl_seconds <= 0:
            raise AuthConfigError("Las vidas de los tokens deben ser positivas.")
        if self.access_ttl_seconds >= self.refresh_ttl_seconds:
            raise AuthConfigError(
                "El token de acceso debe vivir MENOS que el refresh: su vida corta "
                "es lo que acota el dano de un token robado."
            )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AuthConfig:
        """Construye la configuracion desde el entorno. Falla si falta el secreto."""
        env: Mapping[str, str] = os.environ if environ is None else environ
        secret = env.get(JWT_SECRET_ENV_VAR, "").strip()
        if not secret:
            raise AuthConfigError(
                f"Falta la variable de entorno {JWT_SECRET_ENV_VAR} con el secreto "
                "de firma de los tokens."
            )
        return cls(
            jwt_secret=secret,
            access_ttl_seconds=_int_from_env(
                env, ACCESS_TTL_ENV_VAR, _DEFAULT_ACCESS_TTL_SECONDS
            ),
            refresh_ttl_seconds=_int_from_env(
                env, REFRESH_TTL_ENV_VAR, _DEFAULT_REFRESH_TTL_SECONDS
            ),
        )
