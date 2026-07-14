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
from collections.abc import Callable, Iterator
from uuid import UUID, uuid4

import pytest

from ce_v5.infra.db.config import (
    OPERATOR_DSN_ENV_VAR,
    DbConfig,
    OperatorDbConfig,
)
from ce_v5.infra.db.identity import register_user
from ce_v5.infra.db.migrations.runner import apply_migrations
from ce_v5.infra.db.provision import (
    OPERATOR_PASSWORD_ENV_VAR,
    provision_app_role,
    provision_operator_role,
)
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase

_APP_DSN = os.environ.get("CE_V5_DATABASE_URL")
_MIGRATIONS_DSN = os.environ.get("CE_V5_MIGRATIONS_DATABASE_URL")
_APP_PASSWORD = os.environ.get("CE_V5_APP_DB_PASSWORD")
_OPERATOR_DSN = os.environ.get(OPERATOR_DSN_ENV_VAR)
_OPERATOR_PASSWORD = os.environ.get(OPERATOR_PASSWORD_ENV_VAR)

_MISSING_ENV = _APP_DSN is None or _MIGRATIONS_DSN is None or _APP_PASSWORD is None


@pytest.fixture(scope="session", autouse=True)
def _provision_and_migrate() -> Iterator[None]:
    """Con el rol de migraciones: provisiona los roles con LOGIN y migra.

    Se salta toda la suite de integracion si falta cualquiera de las variables
    de entorno base. El rol de operador (P06) se provisiona solo si su
    contrasena esta presente; los tests que lo necesitan se saltan si no.
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
        if _OPERATOR_PASSWORD is not None:
            provision_operator_role(database, _OPERATOR_PASSWORD)
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


@pytest.fixture
def operator_db() -> Iterator[PsycopgDatabase]:
    """Conexion con el rol de OPERADOR (CA-03), via el cargador del PASO 4."""
    if _OPERATOR_DSN is None or _OPERATOR_PASSWORD is None:
        pytest.skip("requiere CE_V5_OPERATOR_DATABASE_URL y CE_V5_OPERATOR_DB_PASSWORD")
    database = PsycopgDatabase(DbConfig(dsn=OperatorDbConfig.from_env().dsn))
    try:
        yield database
    finally:
        database.close()


def _wipe_identidad(migrator_db: PsycopgDatabase) -> None:
    with migrator_db.transaction() as session:
        # Identidad (P06b): se limpia con el rol de MIGRACIONES porque el rol de
        # aplicacion no tiene ningun privilegio sobre estas tablas (CA-07) y tienen
        # FORCE RLS.
        #
        # ORDEN OBLIGATORIO: policy_entitlement, policy_override y
        # sensitive_action_audit REFERENCIAN tenant sin cascada (migracion 0007);
        # borrar el tenant primero fallaria por clave foranea. Y app_user arrastra
        # en cascada credenciales, sesiones y pertenencias (0005/0010), asi que va
        # antes que tenant.
        #
        # Esto lo hace el rol de MIGRACIONES en una base de JUGUETE. Los roles de
        # RUNTIME NO pueden borrar auditoria: se lo prohibe el motor, y el check
        # "audit" lo verifica en cada build. Esa garantia NO se toca.
        session.execute("DELETE FROM sensitive_action_audit")
        session.execute("DELETE FROM policy_entitlement")
        session.execute("DELETE FROM policy_override")
        session.execute("DELETE FROM app_user")
        session.execute("DELETE FROM tenant")


@pytest.fixture(autouse=True)
def _limpiar_identidad(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """Aisla cada test: los usuarios de prueba no se acumulan entre ejecuciones.

    Sin esto, refresh_token_hash (UNIQUE) y email (UNIQUE) chocarian contra las filas
    que dejo la ejecucion anterior, y un test solo funcionaria la primera vez.
    """
    _wipe_identidad(migrator_db)
    yield
    _wipe_identidad(migrator_db)


@pytest.fixture
def crear_usuario(app_db: PsycopgDatabase) -> Callable[[], UUID]:
    """Fabrica de usuarios REALES por la ventanilla de identidad (P06b).

    Desde la migracion 0010 la pertenencia a un tenant exige un usuario existente
    (FK): inventar un uuid4() ya no vale. El alta va por la ventanilla porque el rol
    de aplicacion no puede INSERT en app_user.
    """

    def _crear() -> UUID:
        email = f"test-{uuid4().hex}@ejemplo.test"
        return register_user(app_db, email, "hash-de-prueba-no-es-argon2")

    return _crear
