"""Configuracion de conexion a PostgreSQL para el adapter de persistencia.

Lee el DSN de conexion del entorno. No hay valores por defecto con
secretos: si falta la variable obligatoria, se falla de forma explicita
(ADR-013: la persistencia es infraestructura y su config es externa).

Hay TRES roles/DSN (ADR-011, CA-03): el rol de APLICACION (from_env, se
conecta en runtime, sin BYPASSRLS ni SUPERUSER), el rol de MIGRACIONES
(migrations_from_env, dueno de las tablas, nunca corre en runtime) y el rol
de OPERADOR (OperatorDbConfig.from_env, escribe kill switches; NUNCA en un
proceso de runtime). GUARDIA fail-closed: from_env RECHAZA arrancar si el
DSN de operador esta presente en el entorno (CA-03 punto 2).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DSN_ENV_VAR = "CE_V5_DATABASE_URL"
MIGRATIONS_DSN_ENV_VAR = "CE_V5_MIGRATIONS_DATABASE_URL"
OPERATOR_DSN_ENV_VAR = "CE_V5_OPERATOR_DATABASE_URL"


class DbConfigError(RuntimeError):
    """Error de configuracion de la base de datos."""


class OperatorDsnInRuntimeError(DbConfigError):
    """El DSN de operador esta presente en un proceso de runtime (CA-03).

    Ningun proceso permanente (api, workers, cualquier entrypoint) puede
    portar la credencial de operador: la separacion la hace cumplir el
    CODIGO, no un documento. Si esta variable aparece, el proceso NO arranca.
    """


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

        GUARDIA fail-closed (CA-03 punto 2): si el DSN de operador esta
        presente en el entorno, LANZA OperatorDsnInRuntimeError y el proceso
        no arranca. Un proceso de runtime jamas porta la credencial de
        operador; la separacion la hace cumplir el codigo, no un documento.
        """
        env: Mapping[str, str] = os.environ if environ is None else environ
        if env.get(OPERATOR_DSN_ENV_VAR, "").strip():
            raise OperatorDsnInRuntimeError(
                f"{OPERATOR_DSN_ENV_VAR} esta presente en el entorno de un "
                "proceso de runtime. Ningun api/worker/entrypoint puede portar "
                "la credencial de operador (CA-03). El proceso no arranca."
            )
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


@dataclass(frozen=True, slots=True)
class OperatorDbConfig:
    """DSN del rol de OPERADOR (CA-03). Cargador APARTE, unico que lee su DSN.

    Solo lo usan la herramienta de operador y la validacion en caliente, nunca
    un proceso de runtime (de eso se encarga la guardia de DbConfig.from_env).
    """

    dsn: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> OperatorDbConfig:
        """Construye la config del ROL DE OPERADOR desde el entorno.

        EXIGE CE_V5_OPERATOR_DATABASE_URL; lanza DbConfigError si falta.
        """
        env: Mapping[str, str] = os.environ if environ is None else environ
        return cls(dsn=_dsn_from_env(env, OPERATOR_DSN_ENV_VAR))
