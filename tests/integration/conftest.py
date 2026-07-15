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
    INGESTION_DSN_ENV_VAR,
    OPERATOR_DSN_ENV_VAR,
    DbConfig,
    IngestionDbConfig,
    OperatorDbConfig,
)
from ce_v5.infra.db.identity import register_user
from ce_v5.infra.db.migrations.runner import apply_migrations
from ce_v5.infra.db.provision import (
    INGESTION_PASSWORD_ENV_VAR,
    OPERATOR_PASSWORD_ENV_VAR,
    provision_app_role,
    provision_ingestion_role,
    provision_operator_role,
)
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase

_APP_DSN = os.environ.get("CE_V5_DATABASE_URL")
_MIGRATIONS_DSN = os.environ.get("CE_V5_MIGRATIONS_DATABASE_URL")
_APP_PASSWORD = os.environ.get("CE_V5_APP_DB_PASSWORD")
_OPERATOR_DSN = os.environ.get(OPERATOR_DSN_ENV_VAR)
_OPERATOR_PASSWORD = os.environ.get(OPERATOR_PASSWORD_ENV_VAR)
_INGESTION_DSN = os.environ.get(INGESTION_DSN_ENV_VAR)
_INGESTION_PASSWORD = os.environ.get(INGESTION_PASSWORD_ENV_VAR)

_MISSING_ENV = _APP_DSN is None or _MIGRATIONS_DSN is None or _APP_PASSWORD is None


@pytest.fixture(scope="session", autouse=True)
def _provision_and_migrate() -> Iterator[None]:
    """Con el rol de migraciones: provisiona los roles con LOGIN y migra.

    Se salta TODA la suite de integracion si falta alguna de las variables de entorno
    BASE (no hay PostgreSQL: es el caso del job 'backend' del CI, que corre sin base
    de datos). Ese es el UNICO skip legitimo que queda aqui, y es el mismo criterio
    que el skipif de modulo de cada fichero.

    Lo que NO se salta nunca (regla 5.18): si HAY base de datos pero falta el DSN de
    OPERADOR o el de INGESTA, sus fixtures FALLAN con un mensaje explicito en vez de
    saltarse. Una suite que parece completa y no lo esta es peor que una suite roja:
    es el defecto de T-01 (21 tests nunca ejecutados en local, DOS de ellos ROTOS).
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
        if _INGESTION_PASSWORD is not None:
            provision_ingestion_role(database, _INGESTION_PASSWORD)
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
    """Conexion con el rol de OPERADOR (CA-03), via el cargador del PASO 4.

    FALLA, NO SE SALTA (regla 5.18). Si hay base de datos pero falta el DSN de
    operador, la suite PARECERIA completa sin serlo: los tests del kill switch, de la
    outbox acotada del operador y de sus auditorias no se ejecutarian, y nadie se
    enteraria. Ese es EXACTAMENTE el defecto de T-01, donde 21 tests de integracion
    se saltaron en silencio y DOS estaban ROTOS; solo los cazo Actions.

    El skipif de MODULO sobre CE_V5_DATABASE_URL sigue intacto, asi que el job
    'backend' del CI (que corre sin PostgreSQL) se salta el modulo entero como
    siempre. Lo que aqui se cierra es el caso peligroso: base de datos PRESENTE y DSN
    de operador AUSENTE.
    """
    if _OPERATOR_DSN is None or _OPERATOR_PASSWORD is None:
        pytest.fail(
            "Faltan CE_V5_OPERATOR_DATABASE_URL y/o CE_V5_OPERATOR_DB_PASSWORD, y hay "
            "base de datos: los tests del rol de OPERADOR (kill switch, outbox acotada "
            "por el motor, auditoria de operador) se estarian quedando SIN EJECUTAR. "
            "Un test que se salta en silencio es un test que no existe (regla 5.18, "
            "origen T-01). Ponlas en el entorno; no se saltan."
        )
    database = PsycopgDatabase(DbConfig(dsn=OperatorDbConfig.from_env().dsn))
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def ingestion_db() -> Iterator[PsycopgDatabase]:
    """Conexion con el rol de INGESTA (regla 5.20), via su propio cargador.

    IngestionDbConfig.from_env ABORTA si encuentra en el entorno el DSN de la
    aplicacion o el del operador (guardia bidireccional de 5.20), y el proceso de
    pytest los lleva TODOS. Por eso se le pasa un entorno EXPLICITO con solo lo suyo:
    el guardia funciona, y aqui se le da justamente el entorno que tendria el worker
    de ingesta de verdad. La guardia en si se prueba aparte, en los tests de config.

    FALLA, NO SE SALTA (regla 5.18), por el mismo motivo que operator_db: sin el DSN
    de ingesta, las pruebas negativas de la regla 5.20 (la API no fabrica velas; el
    ingestor no toca identidad, politica ni auditoria; el historico es append-only) y
    la ventanilla agregada de CA-P07-D no se ejecutarian, y la suite mentiria diciendo
    que todo esta verde.
    """
    if _INGESTION_DSN is None or _INGESTION_PASSWORD is None:
        pytest.fail(
            "Faltan CE_V5_INGESTION_DATABASE_URL y/o CE_V5_INGESTION_DB_PASSWORD, y "
            "hay base de datos: los tests del rol de INGESTA (regla 5.20 en sus dos "
            "direcciones, historico append-only y ventanilla agregada CA-P07-D) se "
            "estarian quedando SIN EJECUTAR. Un test que se salta en silencio es un "
            "test que no existe (regla 5.18, origen T-01). Ponlas en el entorno."
        )
    config = IngestionDbConfig.from_env({INGESTION_DSN_ENV_VAR: _INGESTION_DSN})
    database = PsycopgDatabase(DbConfig(dsn=config.dsn))
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
