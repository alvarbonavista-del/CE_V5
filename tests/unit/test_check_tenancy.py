"""Unit tests de la logica pura del check 7.8 (tenancy/RLS, ADR-011).

Construyen TableInfo/PolicyInfo/AppRoleInfo a mano y ejercitan check_schema
sin PostgreSQL: un test por regla que demuestra que la violacion se detecta,
mas un caso verde con el esquema real actual.
"""

from __future__ import annotations

import check_tenancy
from check_tenancy import AppRoleInfo, PolicyInfo, TableInfo

_GOOD_ROLE = AppRoleInfo(name="ce_v5_app", is_superuser=False, can_bypass_rls=False)


def _policy(
    table: str = "cosa",
    name: str = "iso",
    using_expr: str = "tenant_id = app_current_tenant_id()",
    with_check_expr: str = "tenant_id = app_current_tenant_id()",
    roles: tuple[str, ...] = ("ce_v5_app",),
    command: str = "ALL",
) -> PolicyInfo:
    # roles y command NO tienen valor por defecto en PolicyInfo A PROPOSITO: un
    # default vacio en 'roles' haria que, si el cargador dejara de rellenarlo, el
    # check diera VERDE creyendo que ninguna policy alcanza a un rol de runtime. Un
    # default que hace pasar un control de seguridad es una trampa. El default vive
    # aqui, en el helper de tests, donde no puede enganar a nadie: ("ce_v5_app",) /
    # "ALL" es lo que tienen las policies reales de tenant.
    return PolicyInfo(table, name, using_expr, with_check_expr, roles, command)


def _table(
    name: str = "cosa",
    *,
    scope: str | None = "tenant",
    has_rls: bool = True,
    has_force_rls: bool = True,
    columns: frozenset[str] = frozenset({"tenant_id"}),
    policies: tuple[PolicyInfo, ...] | None = None,
) -> TableInfo:
    pols = (_policy(name),) if policies is None else policies
    return TableInfo(
        name=name,
        declared_scope=scope,
        has_rls=has_rls,
        has_force_rls=has_force_rls,
        columns=columns,
        policies=pols,
    )


def _system_table(name: str) -> TableInfo:
    return TableInfo(
        name=name,
        declared_scope="system",
        has_rls=False,
        has_force_rls=False,
        columns=frozenset({"id"}),
        policies=(),
    )


def test_esquema_real_actual_no_tiene_violaciones() -> None:
    tables = [
        _system_table("outbox"),
        _system_table("inbox"),
        _system_table("audit_log"),
        _system_table("schema_migrations"),
        TableInfo(
            name="tenant",
            declared_scope="tenant",
            has_rls=True,
            has_force_rls=True,
            columns=frozenset({"tenant_id", "created_at"}),
            policies=(
                PolicyInfo(
                    "tenant",
                    "tenant_isolation",
                    "tenant_id = app_current_tenant_id()",
                    "tenant_id = app_current_tenant_id()",
                    ("ce_v5_app",),
                    "ALL",
                ),
            ),
        ),
        TableInfo(
            name="user_tenant_membership",
            declared_scope="user",
            has_rls=True,
            has_force_rls=True,
            columns=frozenset({"user_id", "tenant_id", "created_at"}),
            policies=(
                PolicyInfo(
                    "user_tenant_membership",
                    "user_tenant_membership_isolation",
                    "(tenant_id = app_current_tenant_id()) OR "
                    "(user_id = app_current_user_id())",
                    "tenant_id = app_current_tenant_id()",
                    ("ce_v5_app",),
                    "ALL",
                ),
            ),
        ),
    ]
    assert check_tenancy.check_schema(tables, _GOOD_ROLE) == []


def test_r1_sin_isolation_scope_es_violacion() -> None:
    violations = check_tenancy.check_schema([_table(scope=None)], _GOOD_ROLE)
    assert len(violations) == 1
    assert "R1" in violations[0]
    assert "isolation_scope" in violations[0]


def test_r1_scope_no_reconocido_es_violacion() -> None:
    violations = check_tenancy.check_schema([_table(scope="galaxia")], _GOOD_ROLE)
    assert len(violations) == 1
    assert "no reconocido" in violations[0]


def test_r2_tenant_sin_tenant_id_es_violacion() -> None:
    table = _table(scope="tenant", columns=frozenset())
    violations = check_tenancy.check_schema([table], _GOOD_ROLE)
    assert len(violations) == 1
    assert "R2" in violations[0]


def test_r3_user_sin_user_id_ni_owner_es_violacion() -> None:
    table = _table(scope="user", columns=frozenset({"tenant_id"}))
    violations = check_tenancy.check_schema([table], _GOOD_ROLE)
    assert len(violations) == 1
    assert "R3" in violations[0]


def test_r4_sin_rls_es_violacion() -> None:
    table = _table(scope="tenant", has_rls=False, has_force_rls=False)
    violations = check_tenancy.check_schema([table], _GOOD_ROLE)
    assert len(violations) == 1
    assert "R4" in violations[0]


def test_r4_rls_sin_force_es_violacion() -> None:
    table = _table(scope="tenant", has_rls=True, has_force_rls=False)
    violations = check_tenancy.check_schema([table], _GOOD_ROLE)
    assert len(violations) == 1
    assert "R4" in violations[0]


def test_r5_policy_sin_contexto_de_tenant_es_violacion() -> None:
    # P3 del dictamen: R5 SIGUE MORDIENDO. Una policy sin atadura al tenant y sin
    # allowlistar es violacion, exactamente igual que antes de CA-P07-G.
    bad = PolicyInfo("cosa", "mala", "true", "true", ("ce_v5_app",), "ALL")
    table = _table(scope="tenant", policies=(bad,))
    violations = check_tenancy.check_schema([table], _GOOD_ROLE)
    assert len(violations) == 1
    assert "R5" in violations[0]


def test_r5_tenant_sin_ninguna_policy_es_violacion() -> None:
    table = _table(scope="tenant", policies=())
    violations = check_tenancy.check_schema([table], _GOOD_ROLE)
    assert len(violations) == 1
    assert "sin ninguna policy" in violations[0]


def test_r6_tabla_nueva_sin_tenant_id_fuera_de_allowlist_es_violacion() -> None:
    table = _table(
        name="metrica_nueva",
        scope="system",
        columns=frozenset({"id"}),
        policies=(),
    )
    violations = check_tenancy.check_schema([table], _GOOD_ROLE)
    assert len(violations) == 1
    assert "R6" in violations[0]


def test_r6_tabla_system_con_tenant_id_no_allowlistada_falla() -> None:
    # Antes PASABA (la allowlist solo se consultaba para tablas sin tenant_id);
    # ahora TODA tabla system debe estar allowlistada aunque lleve tenant_id.
    table = _table(
        name="fuga_system",
        scope="system",
        columns=frozenset({"tenant_id", "id"}),
        policies=(),
    )
    violations = check_tenancy.check_schema([table], _GOOD_ROLE)
    assert len(violations) == 1
    assert "R6" in violations[0]
    assert "no allowlistada" in violations[0]


def test_r7_rol_con_bypassrls_es_violacion() -> None:
    role = AppRoleInfo(name="ce_v5_app", is_superuser=False, can_bypass_rls=True)
    violations = check_tenancy.check_schema([], role)
    assert len(violations) == 1
    assert "BYPASSRLS" in violations[0]


def test_r7_rol_con_superuser_es_violacion() -> None:
    role = AppRoleInfo(name="ce_v5_app", is_superuser=True, can_bypass_rls=False)
    violations = check_tenancy.check_schema([], role)
    assert len(violations) == 1
    assert "SUPERUSER" in violations[0]


def test_r7_rol_inexistente_es_violacion() -> None:
    violations = check_tenancy.check_schema([], None)
    assert len(violations) == 1
    assert "no existe" in violations[0]


class TestPolicyAllowlistadaCAP07G:
    """R8a-d y R9: la excepcion a R5 es MAS ESTRECHA que la regla que relaja.

    La unica policy allowlistada del sistema es la del DUENO de la ventanilla
    market_public_demand. Estos tests demuestran que la allowlist no es un agujero:
    cada condicion que la justifica se verifica, y si se pierde, el build rompe.
    """

    TABLA = "market_subscription_intent"
    POLICY = "market_intent_owner_read"
    # El DUENO de las tablas (rol de migraciones): NO es un rol de runtime.
    DUENO = ("ce_v5",)
    USING_OK = "stream_scope = 'public_market'::text"

    def _intent_table(
        self,
        policies: tuple[PolicyInfo, ...],
        *,
        has_force_rls: bool = True,
    ) -> TableInfo:
        return TableInfo(
            name=self.TABLA,
            declared_scope="user",
            has_rls=True,
            has_force_rls=has_force_rls,
            columns=frozenset({"tenant_id", "user_id", "stream_scope"}),
            policies=policies,
        )

    def _owner_policy(
        self,
        *,
        name: str | None = None,
        using_expr: str | None = None,
        with_check_expr: str = "",
        roles: tuple[str, ...] | None = None,
        command: str = "SELECT",
    ) -> PolicyInfo:
        return PolicyInfo(
            table=self.TABLA,
            name=self.POLICY if name is None else name,
            using_expr=self.USING_OK if using_expr is None else using_expr,
            with_check_expr=with_check_expr,
            roles=self.DUENO if roles is None else roles,
            command=command,
        )

    def _isolation_policy(self) -> PolicyInfo:
        """La policy REAL de runtime: esa si ata la fila al tenant."""
        return PolicyInfo(
            table=self.TABLA,
            name="market_intent_isolation",
            using_expr="(tenant_id = app_current_tenant_id()) AND "
            "(user_id = app_current_user_id())",
            with_check_expr="(tenant_id = app_current_tenant_id()) AND "
            "(user_id = app_current_user_id())",
            roles=("ce_v5_app",),
            command="ALL",
        )

    def test_p12_policy_allowlistada_conforme_no_es_violacion(self) -> None:
        # P12 (caso base): la excepcion declarada y conforme NO produce violacion.
        table = self._intent_table((self._isolation_policy(), self._owner_policy()))
        assert check_tenancy.check_schema([table], _GOOD_ROLE) == []

    def test_p12_la_misma_policy_con_otro_nombre_si_es_violacion(self) -> None:
        # P12 (la otra mitad): la excepcion vale SOLO para la entrada declarada.
        # Con otro nombre, la MISMA policy vuelve a caer en R5. Si esto no fuese
        # asi, bastaria renombrar una policy para colarse por la allowlist.
        table = self._intent_table(
            (self._isolation_policy(), self._owner_policy(name="otra_cualquiera"))
        )
        violations = check_tenancy.check_schema([table], _GOOD_ROLE)
        assert len(violations) == 1
        assert "R5" in violations[0]

    def test_p1_r8a_policy_allowlistada_que_alcanza_a_un_rol_de_runtime(self) -> None:
        # P1: los TRES roles de runtime deben romper el build.
        for role in ("ce_v5_app", "ce_v5_ingestion", "ce_v5_operator"):
            table = self._intent_table(
                (self._isolation_policy(), self._owner_policy(roles=(role,)))
            )
            violations = check_tenancy.check_schema([table], _GOOD_ROLE)
            assert len(violations) == 1, role
            assert "R8a" in violations[0]
            assert role in violations[0]

    def test_p2_r8d_policy_allowlistada_sin_su_filtro(self) -> None:
        # P2: si pierde stream_scope='public_market', la ventanilla podria leer los
        # intereses PRIVADOS/BYOC. Es exactamente lo que la excepcion prometia no
        # hacer, asi que la excepcion deja de valer.
        table = self._intent_table(
            (self._isolation_policy(), self._owner_policy(using_expr="true"))
        )
        violations = check_tenancy.check_schema([table], _GOOD_ROLE)
        assert len(violations) == 1
        assert "R8d" in violations[0]
        assert "filtro" in violations[0]

    def test_p9_r8b_policy_allowlistada_que_no_es_de_lectura(self) -> None:
        # P9 (primera mitad): la excepcion es de LECTURA. ALL o UPDATE la anulan.
        for command in ("ALL", "UPDATE"):
            table = self._intent_table(
                (self._isolation_policy(), self._owner_policy(command=command))
            )
            violations = check_tenancy.check_schema([table], _GOOD_ROLE)
            assert len(violations) == 1, command
            assert "R8b" in violations[0]

    def test_p9_r8c_policy_allowlistada_con_with_check(self) -> None:
        # P9 (segunda mitad): WITH CHECK solo tiene sentido al escribir.
        table = self._intent_table(
            (
                self._isolation_policy(),
                self._owner_policy(with_check_expr="true"),
            )
        )
        violations = check_tenancy.check_schema([table], _GOOD_ROLE)
        assert len(violations) == 1
        assert "R8c" in violations[0]

    def test_p10_r9_policy_allowlistada_sobre_tabla_sin_force_rls(self) -> None:
        # P10: la excepcion se APOYA en que la RLS esta activa. Si la RLS cae, la
        # excepcion no vale nada (y R4 tambien protesta: son dos violaciones).
        table = self._intent_table(
            (self._isolation_policy(), self._owner_policy()),
            has_force_rls=False,
        )
        violations = check_tenancy.check_schema([table], _GOOD_ROLE)
        assert any("R9" in v for v in violations)
        assert any("R4" in v for v in violations)
