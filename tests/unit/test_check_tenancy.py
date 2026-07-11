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
) -> PolicyInfo:
    return PolicyInfo(table, name, using_expr, with_check_expr)


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
    bad = PolicyInfo("cosa", "mala", "true", "true")
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
