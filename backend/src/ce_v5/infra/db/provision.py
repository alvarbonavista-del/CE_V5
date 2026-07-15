"""Provisioning de los roles con LOGIN de PostgreSQL (ADR-011, CA-03).

El rol de APLICACION (ce_v5_app) se conecta en runtime y NO puede tener
SUPERUSER ni BYPASSRLS: si los tuviera, las policies de RLS no le aplicarian
y el aislamiento entre tenants seria decorativo. El rol de OPERADOR
(ce_v5_operator, CA-03) escribe kill switches y su bitacora, y jamas corre en
un proceso de runtime. Las migraciones 0004/0008 crean ambos roles sin
credencial; aqui se les da LOGIN con la contrasena del ENTORNO (nunca en el
repositorio, CE-13) y se reafirman sus limites de forma idempotente.

Se ejecuta con el rol de MIGRACIONES:
    python -m ce_v5.infra.db.provision
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from ce_v5.infra.db.config import DbConfig, DbConfigError
from ce_v5.infra.db.ports import Database
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase

APP_ROLE_NAME = "ce_v5_app"
APP_PASSWORD_ENV_VAR = "CE_V5_APP_DB_PASSWORD"

OPERATOR_ROLE_NAME = "ce_v5_operator"
OPERATOR_PASSWORD_ENV_VAR = "CE_V5_OPERATOR_DB_PASSWORD"

INGESTION_ROLE_NAME = "ce_v5_ingestion"
INGESTION_PASSWORD_ENV_VAR = "CE_V5_INGESTION_DB_PASSWORD"

_PASSWORD_SETTING = "ce_v5.provision_password"

_CREATE_ROLE_IF_MISSING = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ce_v5_app') THEN
        CREATE ROLE ce_v5_app NOLOGIN NOSUPERUSER NOBYPASSRLS
            NOCREATEDB NOCREATEROLE NOREPLICATION INHERIT;
    END IF;
END
$$
"""

# La contrasena viaja como parametro a set_config (SET LOCAL: se descarta al
# cerrar la transaccion) y se aplica con format(%L), que la escapa. Nunca se
# interpola en el SQL desde Python.
_GRANT_LOGIN = """
DO $$
BEGIN
    EXECUTE format(
        'ALTER ROLE ce_v5_app WITH LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS '
        'NOCREATEDB NOCREATEROLE NOREPLICATION INHERIT',
        current_setting('ce_v5.provision_password')
    );
END
$$
"""

_CREATE_OPERATOR_ROLE_IF_MISSING = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ce_v5_operator') THEN
        CREATE ROLE ce_v5_operator NOLOGIN NOSUPERUSER NOBYPASSRLS
            NOCREATEDB NOCREATEROLE NOREPLICATION INHERIT;
    END IF;
END
$$
"""

# Mismo mecanismo seguro que el rol de aplicacion: la contrasena viaja como
# parametro a set_config y se aplica con format(%L); nunca se interpola en SQL.
_GRANT_OPERATOR_LOGIN = """
DO $$
BEGIN
    EXECUTE format(
        'ALTER ROLE ce_v5_operator WITH LOGIN PASSWORD %L NOSUPERUSER '
        'NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION INHERIT',
        current_setting('ce_v5.provision_password')
    );
END
$$
"""


_CREATE_INGESTION_ROLE_IF_MISSING = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ce_v5_ingestion') THEN
        CREATE ROLE ce_v5_ingestion NOLOGIN NOSUPERUSER NOBYPASSRLS
            NOCREATEDB NOCREATEROLE NOREPLICATION INHERIT;
    END IF;
END
$$
"""

# Mismo mecanismo seguro que los otros dos roles: la contrasena viaja como
# parametro a set_config y se aplica con format(%L); nunca se interpola en SQL.
_GRANT_INGESTION_LOGIN = """
DO $$
BEGIN
    EXECUTE format(
        'ALTER ROLE ce_v5_ingestion WITH LOGIN PASSWORD %L NOSUPERUSER '
        'NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION INHERIT',
        current_setting('ce_v5.provision_password')
    );
END
$$
"""


def password_from_env(environ: Mapping[str, str] | None = None) -> str:
    """Lee del entorno la contrasena del rol de aplicacion. Falla si falta."""
    env: Mapping[str, str] = os.environ if environ is None else environ
    password = env.get(APP_PASSWORD_ENV_VAR, "").strip()
    if not password:
        raise DbConfigError(
            f"Falta la variable de entorno {APP_PASSWORD_ENV_VAR} "
            f"con la contrasena del rol de aplicacion {APP_ROLE_NAME}."
        )
    return password


def operator_password_from_env(environ: Mapping[str, str] | None = None) -> str:
    """Lee del entorno la contrasena del rol de operador. Falla si falta."""
    env: Mapping[str, str] = os.environ if environ is None else environ
    password = env.get(OPERATOR_PASSWORD_ENV_VAR, "").strip()
    if not password:
        raise DbConfigError(
            f"Falta la variable de entorno {OPERATOR_PASSWORD_ENV_VAR} "
            f"con la contrasena del rol de operador {OPERATOR_ROLE_NAME}."
        )
    return password


def ingestion_password_from_env(environ: Mapping[str, str] | None = None) -> str:
    """Lee del entorno la contrasena del rol de ingesta. Falla si falta."""
    env: Mapping[str, str] = os.environ if environ is None else environ
    password = env.get(INGESTION_PASSWORD_ENV_VAR, "").strip()
    if not password:
        raise DbConfigError(
            f"Falta la variable de entorno {INGESTION_PASSWORD_ENV_VAR} "
            f"con la contrasena del rol de ingesta {INGESTION_ROLE_NAME}."
        )
    return password


def provision_app_role(db: Database, password: str) -> None:
    """Crea (si falta) el rol de aplicacion y le da LOGIN sin privilegios de bypass."""
    if not password.strip():
        raise DbConfigError(
            f"La contrasena del rol {APP_ROLE_NAME} no puede estar vacia."
        )
    with db.transaction() as session:
        session.execute(_CREATE_ROLE_IF_MISSING)
        session.execute(
            f"SELECT set_config('{_PASSWORD_SETTING}', %s, true)", (password,)
        )
        session.execute(_GRANT_LOGIN)


def provision_operator_role(db: Database, password: str) -> None:
    """Crea (si falta) el rol de operador y le da LOGIN sin privilegios de bypass."""
    if not password.strip():
        raise DbConfigError(
            f"La contrasena del rol {OPERATOR_ROLE_NAME} no puede estar vacia."
        )
    with db.transaction() as session:
        session.execute(_CREATE_OPERATOR_ROLE_IF_MISSING)
        session.execute(
            f"SELECT set_config('{_PASSWORD_SETTING}', %s, true)", (password,)
        )
        session.execute(_GRANT_OPERATOR_LOGIN)


def provision_ingestion_role(db: Database, password: str) -> None:
    """Crea (si falta) el rol de ingesta y le da LOGIN sin privilegios de bypass."""
    if not password.strip():
        raise DbConfigError(
            f"La contrasena del rol {INGESTION_ROLE_NAME} no puede estar vacia."
        )
    with db.transaction() as session:
        session.execute(_CREATE_INGESTION_ROLE_IF_MISSING)
        session.execute(
            f"SELECT set_config('{_PASSWORD_SETTING}', %s, true)", (password,)
        )
        session.execute(_GRANT_INGESTION_LOGIN)


def main() -> None:
    """Provisiona los roles con LOGIN usando el rol de migraciones.

    El rol de aplicacion es obligatorio. El de operador (CA-03) y el de INGESTA
    (regla 5.20) se provisionan solo si su contrasena esta en el entorno: asi un
    entorno que no opera kill switches ni ingiere market data no necesita
    conocerlas. Una credencial que no se necesita no se reparte.
    """
    database = PsycopgDatabase(DbConfig.migrations_from_env())
    operator_password = os.environ.get(OPERATOR_PASSWORD_ENV_VAR, "").strip()
    ingestion_password = os.environ.get(INGESTION_PASSWORD_ENV_VAR, "").strip()
    try:
        provision_app_role(database, password_from_env())
        if operator_password:
            provision_operator_role(database, operator_password)
        if ingestion_password:
            provision_ingestion_role(database, ingestion_password)
    finally:
        database.close()
    print(
        f"Rol {APP_ROLE_NAME} provisionado: LOGIN, NOSUPERUSER, NOBYPASSRLS, "
        "no propietario de tablas."
    )
    if operator_password:
        print(
            f"Rol {OPERATOR_ROLE_NAME} provisionado: LOGIN, NOSUPERUSER, "
            "NOBYPASSRLS, no propietario de tablas."
        )
    else:
        print(
            f"Rol {OPERATOR_ROLE_NAME} NO provisionado: falta "
            f"{OPERATOR_PASSWORD_ENV_VAR} (CA-03)."
        )
    if ingestion_password:
        print(
            f"Rol {INGESTION_ROLE_NAME} provisionado: LOGIN, NOSUPERUSER, "
            "NOBYPASSRLS, no propietario de tablas."
        )
    else:
        print(
            f"Rol {INGESTION_ROLE_NAME} NO provisionado: falta "
            f"{INGESTION_PASSWORD_ENV_VAR} (regla 5.20)."
        )


if __name__ == "__main__":
    main()
