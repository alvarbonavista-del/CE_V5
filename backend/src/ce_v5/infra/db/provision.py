"""Provisioning del rol de aplicacion de PostgreSQL (ADR-011).

El rol de aplicacion (ce_v5_app) se conecta en runtime y NO puede tener
SUPERUSER ni BYPASSRLS: si los tuviera, las policies de RLS no le aplicarian
y el aislamiento entre tenants seria decorativo. La migracion 0004 crea el
rol sin credencial; aqui se le da LOGIN con la contrasena del ENTORNO (nunca
en el repositorio, CE-13) y se reafirman sus limites de forma idempotente.

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


def main() -> None:
    """Punto de entrada: provisiona el rol de aplicacion con el rol de migraciones."""
    database = PsycopgDatabase(DbConfig.migrations_from_env())
    try:
        provision_app_role(database, password_from_env())
    finally:
        database.close()
    print(
        f"Rol {APP_ROLE_NAME} provisionado: LOGIN, NOSUPERUSER, NOBYPASSRLS, "
        "no propietario de tablas."
    )


if __name__ == "__main__":
    main()
