"""Fixtures compartidas de integracion de la base de datos (ADR-011).

Requieren PostgreSQL local. Las migraciones se aplican SIEMPRE con el rol de
MIGRACIONES (dueno de las tablas); NUNCA con el rol de aplicacion (ADR-011).
Antes de correr los tests, con el rol de migraciones se provisiona el rol de
aplicacion (LOGIN sin BYPASSRLS ni SUPERUSER) y se aplica el esquema. Los
tests de datos operan luego con el rol de aplicacion, sometido al RLS.

Base de datos de JUGUETE: nunca datos reales (DOC_ENTREGABLES sec.5).
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from ce_v5.infra.db.config import DbConfig
from ce_v5.infra.db.migrations.runner import apply_migrations
from ce_v5.infra.db.provision import provision_app_role
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase

_APP_DSN = os.environ.get("CE_V5_DATABASE_URL")
_MIGRATIONS_DSN = os.environ.get("CE_V5_MIGRATIONS_DATABASE_URL")
_APP_PASSWORD = os.environ.get("CE_V5_APP_DB_PASSWORD")

_MISSING_ENV = _APP_DSN is None or _MIGRATIONS_DSN is None or _APP_PASSWORD is None


@pytest.fixture(scope="session", autouse=True)
def _provision_and_migrate() -> Iterator[None]:
    """Con el rol de migraciones: provisiona el rol de aplicacion y migra.

    Se salta toda la suite de integracion si falta cualquiera de las
    variables de entorno necesarias.
    """
    if _MISSING_ENV:
        pytest.skip(
            "requiere CE_V5_DATABASE_URL, CE_V5_MIGRATIONS_DATABASE_URL y "
            "CE_V5_APP_DB_PASSWORD"
        )
    assert _MIGRATIONS_DSN is not None and _APP_PASSWORD is not None
    database = PsycopgDatabase(DbConfig(dsn=_MIGRATIONS_DSN))
    try:
        provision_app_role(database, _APP_PASSWORD)
        apply_migrations(database)
    finally:
        database.close()
    yield


@pytest.fixture
def migrator_db() -> Iterator[PsycopgDatabase]:
    """Conexion con el rol de MIGRACIONES (para probar el propio runner)."""
    assert _MIGRATIONS_DSN is not None
    database = PsycopgDatabase(DbConfig(dsn=_MIGRATIONS_DSN))
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def app_db() -> Iterator[PsycopgDatabase]:
    """Conexion con el rol de APLICACION (sometido al RLS, ADR-011)."""
    assert _APP_DSN is not None
    database = PsycopgDatabase(DbConfig(dsn=_APP_DSN))
    try:
        yield database
    finally:
        database.close()
