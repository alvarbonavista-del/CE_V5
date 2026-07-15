"""Configuracion de conexion a PostgreSQL para el adapter de persistencia.

Lee el DSN de conexion del entorno. No hay valores por defecto con
secretos: si falta la variable obligatoria, se falla de forma explicita
(ADR-013: la persistencia es infraestructura y su config es externa).

Hay CUATRO roles/DSN (ADR-011, CA-03, regla 5.20): el rol de APLICACION
(from_env, se conecta en runtime, sin BYPASSRLS ni SUPERUSER), el rol de
MIGRACIONES (migrations_from_env, dueno de las tablas, nunca corre en runtime),
el rol de OPERADOR (OperatorDbConfig.from_env, escribe kill switches; NUNCA en
un proceso de runtime) y el rol de INGESTA (IngestionDbConfig.from_env, unico
que ESCRIBE market data; solo el worker de ingesta).

GUARDIAS fail-closed, en los DOS sentidos: from_env RECHAZA arrancar si el DSN
de operador (CA-03 punto 2) o el de ingesta (regla 5.20) estan en el entorno; e
IngestionDbConfig.from_env RECHAZA arrancar si aparecen el de operador o el de
la aplicacion. Un proceso no porta credenciales que su funcion no necesita.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DSN_ENV_VAR = "CE_V5_DATABASE_URL"
MIGRATIONS_DSN_ENV_VAR = "CE_V5_MIGRATIONS_DATABASE_URL"
OPERATOR_DSN_ENV_VAR = "CE_V5_OPERATOR_DATABASE_URL"
INGESTION_DSN_ENV_VAR = "CE_V5_INGESTION_DATABASE_URL"


class DbConfigError(RuntimeError):
    """Error de configuracion de la base de datos."""


class OperatorDsnInRuntimeError(DbConfigError):
    """El DSN de operador esta presente en un proceso de runtime (CA-03).

    Ningun proceso permanente (api, workers, cualquier entrypoint) puede
    portar la credencial de operador: la separacion la hace cumplir el
    CODIGO, no un documento. Si esta variable aparece, el proceso NO arranca.
    """


class IngestionDsnInApiError(DbConfigError):
    """Un proceso de API/app porta el DSN de ingesta (regla 5.20).

    La API esta EXPUESTA A INTERNET. Si portase la credencial de ingesta,
    podria ESCRIBIR VELAS: fabricar un hecho de mercado que alimenta reglas,
    senales y, en M5, ordenes reales. No arranca.
    """


class ForeignDsnInIngestionError(DbConfigError):
    """El proceso de ingesta porta una credencial que no le corresponde (5.20).

    El ingestor no toca identidad, ni politica, ni ordenes. Si portase el DSN
    de la aplicacion o el del operador, tendria en la mano un poder que su
    funcion no necesita. No arranca.
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

        SEGUNDA GUARDIA fail-closed (regla 5.20): si el DSN de INGESTA esta
        presente, LANZA IngestionDsnInApiError. La API esta expuesta a internet;
        con esa credencial podria ESCRIBIR VELAS, es decir, fabricar hechos de
        mercado que alimentan reglas, senales y, en M5, ordenes reales.
        """
        env: Mapping[str, str] = os.environ if environ is None else environ
        if env.get(OPERATOR_DSN_ENV_VAR, "").strip():
            raise OperatorDsnInRuntimeError(
                f"{OPERATOR_DSN_ENV_VAR} esta presente en el entorno de un "
                "proceso de runtime. Ningun api/worker/entrypoint puede portar "
                "la credencial de operador (CA-03). El proceso no arranca."
            )
        if env.get(INGESTION_DSN_ENV_VAR, "").strip():
            raise IngestionDsnInApiError(
                f"{INGESTION_DSN_ENV_VAR} esta presente en el entorno de un "
                "proceso de aplicacion. La API no escribe market data: con esa "
                "credencial podria FABRICAR VELAS (regla 5.20). No arranca."
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


@dataclass(frozen=True, slots=True)
class IngestionDbConfig:
    """DSN del rol de INGESTA (regla 5.20). Unico cargador que lee su DSN.

    Solo lo usa el worker de ingesta. GUARDIA fail-closed BIDIRECCIONAL: si en
    su entorno aparece el DSN de operador o el de la aplicacion, NO ARRANCA:
    un proceso no porta credenciales que su funcion no necesita.
    """

    dsn: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> IngestionDbConfig:
        env: Mapping[str, str] = os.environ if environ is None else environ
        if env.get(OPERATOR_DSN_ENV_VAR, "").strip():
            raise ForeignDsnInIngestionError(
                f"{OPERATOR_DSN_ENV_VAR} esta presente en el entorno del worker de "
                "ingesta. El ingestor no opera kill switches (regla 5.20). No arranca."
            )
        if env.get(DSN_ENV_VAR, "").strip():
            raise ForeignDsnInIngestionError(
                f"{DSN_ENV_VAR} esta presente en el entorno del worker de ingesta. "
                "El ingestor no toca identidad, politica ni ordenes: no porta la "
                "credencial de la aplicacion (regla 5.20). No arranca."
            )
        return cls(dsn=_dsn_from_env(env, INGESTION_DSN_ENV_VAR))
