"""Configuracion de conexion a PostgreSQL para el adapter de persistencia.

Lee el DSN de conexion del entorno. No hay valores por defecto con
secretos: si falta la variable obligatoria, se falla de forma explicita
(ADR-013: la persistencia es infraestructura y su config es externa).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DSN_ENV_VAR = "CE_V5_DATABASE_URL"


class DbConfigError(RuntimeError):
    """Error de configuracion de la base de datos."""


@dataclass(frozen=True, slots=True)
class DbConfig:
    """Parametros de conexion resueltos para el adapter de PostgreSQL."""

    dsn: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> DbConfig:
        """Construye la config desde el entorno.

        Usa os.environ si no se pasa un mapping explicito (util en tests).
        Lanza DbConfigError si falta o esta vacia la variable obligatoria.
        """
        env: Mapping[str, str] = os.environ if environ is None else environ
        dsn = env.get(DSN_ENV_VAR, "").strip()
        if not dsn:
            raise DbConfigError(
                f"Falta la variable de entorno {DSN_ENV_VAR} "
                "con el DSN de conexion a PostgreSQL."
            )
        return cls(dsn=dsn)
