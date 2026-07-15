"""Las DOCE pruebas del 7.8 ENDURECIDO (CA-P07-G), LEIDAS DEL CATALOGO real.

Bloqueante 1 (Central+CSA): las doce negativas de CA-P07-G no se leen con regex sobre
las migraciones .sql (eso comprueba lo que alguien ESCRIBIO), sino del CATALOGO
(pg_policies / pg_get_expr / pg_get_function_result via check_tenancy.load_schema) o
corriendo el MOTOR real. Un regex ve el texto; el catalogo ve lo que la base TIENE.

BASELINE (una vez por prueba): tables, app_role = load_schema(migrator_db) -- la MISMA
load_schema que el CI corre contra el catalogo. Sobre la PolicyInfo REAL leida de
pg_policies ('market_intent_owner_read') se construyen las perturbaciones con
dataclasses.replace y se corre check_schema (la MISMA funcion pura del CI). Asi la base
es catalogo real y la perturbacion es la variante que se prueba que MUERDE.

P6-P10 (motor real) reproducen aqui, self-contained, el escenario de
test_market_access.py (que conserva sus originales) para que las DOCE salgan juntas en
la salida verbose. Base de JUGUETE: nunca datos reales (DOC_ENTREGABLES sec.5).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from uuid import UUID, uuid4

import pytest

import check_tenancy
from ce_v5.infra.db.provision import (
    APP_ROLE_NAME,
    INGESTION_ROLE_NAME,
    OPERATOR_ROLE_NAME,
)
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.infra.db.tenancy import TenantScopedDatabase, provision_tenant_for_user
from check_tenancy import (
    AppRoleInfo,
    PolicyInfo,
    TableInfo,
    check_schema,
    load_schema,
)

_DSN = os.environ.get("CE_V5_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    _DSN is None, reason="requiere CE_V5_DATABASE_URL (PostgreSQL local)"
)

_INTENT_TABLE = "market_subscription_intent"
_ALLOWLISTED = "market_intent_owner_read"

_INSERT_INTENT = """
INSERT INTO market_subscription_intent (
    intent_id, tenant_id, user_id, stream_scope, market_stream_key,
    exchange, market_type, symbol, data_kind, timeframe, source_type, source_ref
) VALUES (%s, %s, %s, %s, %s, 'binance', 'spot', 'BTC-USDT', 'candles', '1m', %s, %s)
"""


# -- Baseline del CATALOGO -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Baseline:
    """El estado REAL leido de pg_policies/pg_catalog sobre el que se perturba."""

    tables: list[TableInfo]
    app_role: AppRoleInfo
    intent: TableInfo
    policy: PolicyInfo


def _tabla(tables: list[TableInfo], name: str) -> TableInfo:
    for table in tables:
        if table.name == name:
            return table
    msg = f"la tabla {name!r} no aparece en el catalogo"
    raise AssertionError(msg)


def _policy(table: TableInfo, name: str) -> PolicyInfo:
    for policy in table.policies:
        if policy.name == name:
            return policy
    msg = f"la policy {name!r} no aparece en el catalogo de {table.name}"
    raise AssertionError(msg)


@pytest.fixture
def baseline(migrator_db: PsycopgDatabase) -> _Baseline:
    """Lee el CATALOGO real (pg_policies incluido) con el rol OWNER (migraciones)."""
    tables, app_role = load_schema(migrator_db)
    assert app_role is not None, "el rol de aplicacion debe existir en el catalogo"
    intent = _tabla(tables, _INTENT_TABLE)
    policy = _policy(intent, _ALLOWLISTED)
    return _Baseline(tables=tables, app_role=app_role, intent=intent, policy=policy)


def _con_policy(table: TableInfo, policy: PolicyInfo) -> TableInfo:
    """La TableInfo con su policy del mismo nombre sustituida por la perturbada."""
    nuevas = tuple(policy if p.name == policy.name else p for p in table.policies)
    return replace(table, policies=nuevas)


def _hay(violations: list[str], *fragmentos: str) -> bool:
    """True si alguna violacion contiene TODOS los fragmentos (regla + tabla/policy)."""
    return any(all(frag in v for frag in fragmentos) for v in violations)


# -- Motor real: helpers self-contained (originales en test_market_access.py) ---


def _entero(valor: object) -> int:
    assert isinstance(valor, int)
    return valor


def _dos_sujetos(
    app_db: PsycopgDatabase, crear_usuario: Callable[[], UUID]
) -> tuple[UUID, UUID]:
    user_a, user_b = crear_usuario(), crear_usuario()
    provision_tenant_for_user(app_db, user_a)
    provision_tenant_for_user(app_db, user_b)
    return user_a, user_b


def _crear_intent(
    app_db: PsycopgDatabase,
    user_id: UUID,
    stream_key: str,
    *,
    stream_scope: str = "public_market",
    source_ref: str = "widget-1",
) -> None:
    scoped_db = TenantScopedDatabase(app_db)
    with scoped_db.transaction(user_id) as scoped:
        scoped.session.execute(
            _INSERT_INTENT,
            (
                str(uuid4()),
                str(scoped.context.tenant_id),
                str(user_id),
                stream_scope,
                stream_key,
                "widget",
                source_ref,
            ),
        )


def _demanda(ingestion_db: PsycopgDatabase, stream_key: str) -> int | None:
    with ingestion_db.transaction() as session:
        row = session.fetchone(
            "SELECT out_intent_count FROM market_public_demand() "
            "WHERE out_market_stream_key = %s",
            (stream_key,),
        )
    return None if row is None else _entero(row[0])


def _stream_key() -> str:
    return f"market:candles:binance:spot:BTC-USDT:1m:{uuid4().hex[:8]}"


class TestLasDocePruebasDesdeElCatalogo:
    """CA-P07-G endurecido, las doce, cada una del CATALOGO o del MOTOR real."""

    def test_p01_policy_perturbada_a_ce_v5_app_muerde_r8a(
        self, baseline: _Baseline
    ) -> None:
        # La policy allowlistada REAL, ensanchada al rol de la API -> R8a.
        perturbada = replace(
            baseline.policy, roles=(*baseline.policy.roles, APP_ROLE_NAME)
        )
        violations = check_schema(
            [_con_policy(baseline.intent, perturbada)], baseline.app_role
        )
        assert _hay(violations, "R8a", _ALLOWLISTED, APP_ROLE_NAME)

    def test_p02_policy_perturbada_a_ce_v5_ingestion_muerde_r8a(
        self, baseline: _Baseline
    ) -> None:
        perturbada = replace(
            baseline.policy, roles=(*baseline.policy.roles, INGESTION_ROLE_NAME)
        )
        violations = check_schema(
            [_con_policy(baseline.intent, perturbada)], baseline.app_role
        )
        assert _hay(violations, "R8a", _ALLOWLISTED, INGESTION_ROLE_NAME)

    def test_p03_policy_perturbada_a_ce_v5_operator_muerde_r8a(
        self, baseline: _Baseline
    ) -> None:
        perturbada = replace(
            baseline.policy, roles=(*baseline.policy.roles, OPERATOR_ROLE_NAME)
        )
        violations = check_schema(
            [_con_policy(baseline.intent, perturbada)], baseline.app_role
        )
        assert _hay(violations, "R8a", _ALLOWLISTED, OPERATOR_ROLE_NAME)

    def test_p04_using_sin_public_market_muerde_r8d(self, baseline: _Baseline) -> None:
        # Se quita el fragmento 'public_market' del USING REAL del catalogo -> R8d: sin
        # ese filtro la policy podria leer intereses privados/BYOC.
        sin_filtro = baseline.policy.using_expr.replace("public_market", "user")
        perturbada = replace(baseline.policy, using_expr=sin_filtro)
        violations = check_schema(
            [_con_policy(baseline.intent, perturbada)], baseline.app_role
        )
        assert _hay(violations, "R8d", _ALLOWLISTED)

    def test_p05_tabla_throwaway_sin_tenant_ni_allowlist_muerde_r5(
        self, migrator_db: PsycopgDatabase
    ) -> None:
        # Prueba TOTALMENTE del catalogo: se crea (rol OWNER) una tabla desechable de
        # alcance user con RLS ENABLE+FORCE y una policy USING(true) NO allowlistada;
        # load_schema la lee del catalogo REAL y check_schema devuelve R5 para ella.
        _crear_probe(migrator_db)
        try:
            tables, app_role = load_schema(migrator_db)
            violations = check_schema(tables, app_role)
            assert _hay(violations, "R5", "_p07_probe"), (
                f"esperaba R5 para _p07_probe; violaciones: {violations}"
            )
        finally:
            # DROP en finally: limpia aunque la asercion falle.
            with migrator_db.transaction() as session:
                session.execute("DROP TABLE IF EXISTS _p07_probe")

    def test_p06_dos_tenants_mismo_stream_el_worker_ve_count_2(
        self,
        app_db: PsycopgDatabase,
        ingestion_db: PsycopgDatabase,
        crear_usuario: Callable[[], UUID],
    ) -> None:
        # MOTOR real: dos tenants distintos piden el MISMO flujo publico; la ventanilla
        # (rol ingesta) ve count=2 (por eso se abre UN solo stream, ADR-014).
        stream_key = _stream_key()
        user_a, user_b = _dos_sujetos(app_db, crear_usuario)
        _crear_intent(app_db, user_a, stream_key)
        _crear_intent(app_db, user_b, stream_key)

        assert _demanda(ingestion_db, stream_key) == 2

    def test_p07_la_ventanilla_no_devuelve_ningun_identificador_de_sujeto(
        self,
        app_db: PsycopgDatabase,
        ingestion_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        crear_usuario: Callable[[], UUID],
    ) -> None:
        # Del CATALOGO: la firma de la ventanilla (pg_get_function_result) es
        # EXACTAMENTE (out_market_stream_key, out_intent_count) y NO nombra sujeto.
        with migrator_db.transaction() as session:
            row = session.fetchone(
                "SELECT pg_get_function_result(p.oid) FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE p.proname = 'market_public_demand' AND n.nspname = 'public'"
            )
        assert row is not None
        firma = str(row[0])
        assert "out_market_stream_key" in firma
        assert "out_intent_count" in firma
        for prohibido in ("tenant_id", "user_id", "intent_id", "email", "session"):
            assert prohibido not in firma, f"la ventanilla nombra {prohibido!r}"

        # Y del MOTOR real: la ejecucion devuelve EXACTAMENTE dos columnas.
        stream_key = _stream_key()
        user_a, _ = _dos_sujetos(app_db, crear_usuario)
        _crear_intent(app_db, user_a, stream_key)
        with ingestion_db.transaction() as session:
            fila = session.fetchone(
                "SELECT * FROM market_public_demand() WHERE out_market_stream_key = %s",
                (stream_key,),
            )
        assert fila is not None
        assert len(fila) == 2  # ni tenant_id, ni user_id, ni intent_id: no caben.

    def test_p08_un_intent_privado_no_aparece_en_la_ventanilla(
        self,
        app_db: PsycopgDatabase,
        ingestion_db: PsycopgDatabase,
        crear_usuario: Callable[[], UUID],
    ) -> None:
        # MOTOR real: un interes PRIVADO (stream_scope='user') NO pasa por la ventanilla
        # -- lo filtra la POLICY DEL DUENO, no una promesa del codigo (CA-P07-G).
        stream_key = _stream_key()
        user_a, _ = _dos_sujetos(app_db, crear_usuario)
        _crear_intent(app_db, user_a, stream_key, stream_scope="user")

        assert _demanda(ingestion_db, stream_key) is None

    def test_p09_el_ingestor_no_puede_select_la_tabla_de_intereses(
        self, ingestion_db: PsycopgDatabase
    ) -> None:
        # MOTOR real: el ingestor NO puede ni mirar la tabla base; su unico acceso a la
        # demanda es la ventanilla agregada (permission denied).
        with pytest.raises(Exception) as excinfo:  # noqa: PT011, B017
            with ingestion_db.transaction() as session:
                session.fetchall(f"SELECT * FROM {_INTENT_TABLE}")
        assert "permission denied" in str(excinfo.value).lower()

    def test_p10_la_app_solo_opera_sus_propios_intents_por_rls(
        self,
        app_db: PsycopgDatabase,
        crear_usuario: Callable[[], UUID],
    ) -> None:
        # MOTOR real: con el contexto de A no se ve ni se borra lo de B (RLS).
        stream_key = _stream_key()
        user_a, user_b = _dos_sujetos(app_db, crear_usuario)
        _crear_intent(app_db, user_a, stream_key)
        _crear_intent(app_db, user_b, stream_key)

        scoped_db = TenantScopedDatabase(app_db)
        with scoped_db.transaction(user_a) as scoped:
            rows = scoped.session.fetchall(f"SELECT user_id FROM {_INTENT_TABLE}")
            assert [UUID(str(row[0])) for row in rows] == [user_a]
            scoped.session.execute(
                f"DELETE FROM {_INTENT_TABLE} WHERE user_id = %s", (str(user_b),)
            )

        # Lo de B sigue intacto, comprobado bajo el contexto de B.
        with scoped_db.transaction(user_b) as scoped:
            rows = scoped.session.fetchall(f"SELECT user_id FROM {_INTENT_TABLE}")
            assert [UUID(str(row[0])) for row in rows] == [user_b]

    def test_p11_command_no_select_muerde_r8b_y_with_check_muerde_r8c(
        self, baseline: _Baseline
    ) -> None:
        # La excepcion es de LECTURA pura: command != SELECT -> R8b; WITH CHECK (que
        # solo tiene sentido al escribir) -> R8c. Dos perturbaciones de la policy REAL.
        con_all = replace(baseline.policy, command="ALL")
        viol_b = check_schema(
            [_con_policy(baseline.intent, con_all)], baseline.app_role
        )
        assert _hay(viol_b, "R8b", _ALLOWLISTED)

        con_check = replace(baseline.policy, with_check_expr="(true)")
        viol_c = check_schema(
            [_con_policy(baseline.intent, con_check)], baseline.app_role
        )
        assert _hay(viol_c, "R8c", _ALLOWLISTED)

    def test_p12_sin_force_rls_muerde_r9_y_la_tabla_real_si_tiene_rls_forzado(
        self, baseline: _Baseline
    ) -> None:
        # Negativo: la policy allowlistada sobre una tabla SIN FORCE RLS -> R9 (la
        # excepcion se apoya en que la RLS esta activa).
        sin_force = replace(baseline.intent, has_force_rls=False)
        violations = check_schema([sin_force], baseline.app_role)
        assert _hay(violations, "R9", _ALLOWLISTED)

        # Positivo, del catalogo: la tabla REAL SI tiene RLS ENABLE + FORCE.
        assert baseline.intent.has_rls is True
        assert baseline.intent.has_force_rls is True

    def test_p12_bis_el_estado_real_del_catalogo_es_el_seguro(
        self, baseline: _Baseline
    ) -> None:
        # POSITIVO puro del catalogo (pg_policies): la policy real es de LECTURA, para
        # el rol OWNER y NINGUN rol de runtime, con el filtro 'public_market' y sin WITH
        # CHECK. Es el estado REAL, no uno inventado.
        policy = baseline.policy
        assert policy.command.upper() == "SELECT"
        assert policy.roles  # aplica a alguien (el dueno de la ventanilla)
        alcanzados = [r for r in policy.roles if r in check_tenancy.RUNTIME_ROLES]
        assert alcanzados == [], f"alcanza roles de runtime: {alcanzados}"
        assert "public_market" in policy.using_expr
        assert policy.with_check_expr.strip() == ""

        # Y check_schema sobre la tabla REAL (sin perturbar) no ve NINGUNA violacion: el
        # estado leido del catalogo es conforme.
        assert check_schema([baseline.intent], baseline.app_role) == []


def _crear_probe(database: PsycopgDatabase) -> None:
    """Crea la tabla desechable _p07_probe (rol OWNER): alcance user, RLS ENABLE+FORCE y
    una policy USING(true) NO allowlistada. Se borra en el finally del test que la usa.
    """
    with database.transaction() as session:
        session.execute("DROP TABLE IF EXISTS _p07_probe")
        session.execute(
            "CREATE TABLE _p07_probe ("
            "id uuid PRIMARY KEY, tenant_id uuid NOT NULL, user_id uuid NOT NULL)"
        )
        session.execute(
            "COMMENT ON TABLE _p07_probe IS 'throwaway P07 probe. isolation_scope=user'"
        )
        session.execute("ALTER TABLE _p07_probe ENABLE ROW LEVEL SECURITY")
        session.execute("ALTER TABLE _p07_probe FORCE ROW LEVEL SECURITY")
        session.execute(
            "CREATE POLICY _p07_probe_all ON _p07_probe FOR ALL USING (true)"
        )
