"""Configuracion de conexion a PostgreSQL para el adapter de persistencia.

Lee el DSN de conexion del entorno. No hay valores por defecto con
secretos: si falta la variable obligatoria, se falla de forma explicita
(ADR-013: la persistencia es infraestructura y su config es externa).

Hay DOS roles/DSN distintos (ADR-011): el rol de APLICACION (from_env, se
conecta en runtime, sin BYPASSRLS ni SUPERUSER) y el rol de MIGRACIONES
(migrations_from_env, dueno de las tablas, nunca corre en runtime).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DSN_ENV_VAR = "CE_V5_DATABASE_URL"
MIGRATIONS_DSN_ENV_VAR = "CE_V5_MIGRATIONS_DATABASE_URL"


class DbConfigError(RuntimeError):
    """Error de configuracion de la base de datos."""


def _dsn_from_env(env: Mapping[str, str], var: str) -> str:
    dsn = env.get(var, "").strip()
    if not dsn:
        raise DbConfigError(
            f"Falta la variable de entorno {var} con el DSN de conexion a PostgreSQL."
        )
    return dsn


@dataclass(frozen=True, slots=True)
class DbConfig:
    """Parametros de conexion resueltos para el adapter de PostgreSQL."""

    dsn: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> DbConfig:
        """Construye la config del ROL DE APLICACION desde el entorno.

        Usa os.environ si no se pasa un mapping explicito (util en tests).
        Lanza DbConfigError si falta o esta vacia la variable obligatoria.
        """
        env: Mapping[str, str] = os.environ if environ is None else environ
        return cls(dsn=_dsn_from_env(env, DSN_ENV_VAR))

    @classmethod
    def migrations_from_env(cls, environ: Mapping[str, str] | None = None) -> DbConfig:
        """Construye la config del ROL DE MIGRACIONES desde el entorno.

        DSN del rol de migraciones; NO se usa en runtime (ADR-011).
        Simetrico a from_env: usa os.environ si no se pasa un mapping y lanza
        DbConfigError si falta o esta vacia la variable obligatoria.
        """
        env: Mapping[str, str] = os.environ if environ is None else environ
        return cls(dsn=_dsn_from_env(env, MIGRATIONS_DSN_ENV_VAR))
