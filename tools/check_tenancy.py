"""Check 7.8: tenancy y RLS (DOC_ESTRUCTURA sec.7.8, ADR-011).

Materializa en CI las reglas de aislamiento del ADR-011: cada tabla declara
su isolation_scope; las tablas de alcance tenant o user llevan tenant_id (y
user_id cuando toca), tienen RLS habilitado Y forzado, y toda su policy ata
la fila al tenant de la transaccion via app_current_tenant_id(); las tablas
sin tenant_id solo se admiten si estan en una allowlist explicita; y el rol
de aplicacion existe sin poder saltarse el RLS. Corre en cada build contra la
base ya migrada. La logica pura (check_schema) es testeable sin PostgreSQL;
solo load_schema toca el catalogo.
"""

import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "backend" / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(REPO_ROOT / "contracts"))

from ce_v5.infra.db.config import DbConfig  # noqa: E402
from ce_v5.infra.db.ports import Database  # noqa: E402
from ce_v5.infra.db.provision import APP_ROLE_NAME  # noqa: E402
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase  # noqa: E402

ISOLATION_SCOPES = ("public_market", "tenant", "user", "system")

# Toda tabla nueva SIN columna tenant_id exige una entrada explicita aqui con
# su alcance permitido. Es deliberado: sumar una linea a esta allowlist es una
# decision visible en el diff (alguien la revisa en el PR), no un descuido que
# se cuela. Si una tabla sin tenant_id no aparece aqui, el check 7.8 falla.
TABLAS_SIN_TENANT_PERMITIDAS: dict[str, str] = {
    "outbox": "system",
    "inbox": "system",
    "audit_log": "system",
    "schema_migrations": "system",
}

_TENANT_SCOPES = ("tenant", "user")
_APP_TENANT_FN = "app_current_tenant_id()"
_SCOPE_RE = re.compile(r"isolation_scope=([a-z_]+)")


@dataclass(frozen=True, slots=True)
class PolicyInfo:
    """Una policy de RLS y sus expresiones USING / WITH CHECK reconstruidas."""

    table: str
    name: str
    using_expr: str
    with_check_expr: str


@dataclass(frozen=True, slots=True)
class TableInfo:
    """Una tabla del esquema public con lo que el check 7.8 necesita mirar."""

    name: str
    declared_scope: str | None
    has_rls: bool
    has_force_rls: bool
    columns: frozenset[str]
    policies: tuple[PolicyInfo, ...]


@dataclass(frozen=True, slots=True)
class AppRoleInfo:
    """Atributos del rol de aplicacion que deciden si el RLS le aplica."""

    name: str
    is_superuser: bool
    can_bypass_rls: bool


def _policy_uses_tenant(policy: PolicyInfo) -> bool:
    return (
        _APP_TENANT_FN in policy.using_expr or _APP_TENANT_FN in policy.with_check_expr
    )


def _table_violations(table: TableInfo) -> list[str]:
    out: list[str] = []
    scope = table.declared_scope

    # R1: isolation_scope declarado y reconocido.
    if scope is None:
        out.append(
            f"{table.name}: R1 sin isolation_scope en el COMMENT de la tabla "
            "(ADR-011, 7.8): toda tabla declara su alcance de aislamiento."
        )
        return out
    if scope not in ISOLATION_SCOPES:
        out.append(
            f"{table.name}: R1 isolation_scope '{scope}' no reconocido "
            f"(ADR-011, 7.8); validos: {', '.join(ISOLATION_SCOPES)}."
        )
        return out

    has_tenant = "tenant_id" in table.columns
    scoped = scope in _TENANT_SCOPES

    # R2: alcance tenant o user exige columna tenant_id.
    if scoped and not has_tenant:
        out.append(
            f"{table.name}: R2 alcance '{scope}' sin columna tenant_id (ADR-011, 7.8)."
        )

    # R3: alcance user exige ademas user_id u owner_user_id.
    if scope == "user" and not (
        "user_id" in table.columns or "owner_user_id" in table.columns
    ):
        out.append(
            f"{table.name}: R3 alcance 'user' sin columna user_id ni "
            "owner_user_id (ADR-011, 7.8)."
        )

    # R4: alcance tenant o user exige RLS habilitado Y forzado.
    if scoped and not (table.has_rls and table.has_force_rls):
        out.append(
            f"{table.name}: R4 alcance '{scope}' requiere RLS habilitado y "
            "forzado (ENABLE + FORCE ROW LEVEL SECURITY) (ADR-011, 7.8)."
        )

    # R5: alcance tenant o user exige policies, todas atadas al tenant.
    if scoped:
        if not table.policies:
            out.append(
                f"{table.name}: R5 alcance '{scope}' sin ninguna policy de RLS "
                "(ADR-011, 7.8)."
            )
        for policy in table.policies:
            if not _policy_uses_tenant(policy):
                out.append(
                    f"{table.name}: R5 la policy '{policy.name}' no referencia "
                    f"{_APP_TENANT_FN} en USING ni en WITH CHECK (ADR-011, "
                    "7.8): no ata la fila al tenant de la transaccion."
                )

    # R6: tabla sin tenant_id solo se admite allowlistada y con alcance acorde.
    if not has_tenant and not scoped:
        allowed = TABLAS_SIN_TENANT_PERMITIDAS.get(table.name)
        if allowed is None:
            out.append(
                f"{table.name}: R6 tabla sin tenant_id fuera de la allowlist "
                "TABLAS_SIN_TENANT_PERMITIDAS (ADR-011, 7.8): declara su "
                "alcance alli de forma explicita o anade tenant_id."
            )
        elif allowed != scope:
            out.append(
                f"{table.name}: R6 la allowlist permite alcance '{allowed}' "
                f"pero la tabla declara '{scope}' (ADR-011, 7.8)."
            )

    return out


def _app_role_violations(app_role: AppRoleInfo | None) -> list[str]:
    # R7: el rol de aplicacion existe y no puede saltarse el RLS.
    if app_role is None:
        return [
            f"R7 el rol de aplicacion {APP_ROLE_NAME} no existe (ADR-011, "
            "7.8): sin ese rol el RLS no tiene a quien aplicarse."
        ]
    out: list[str] = []
    if app_role.is_superuser:
        out.append(
            f"R7 el rol de aplicacion {app_role.name} tiene SUPERUSER "
            "(ADR-011, 7.8): se saltaria el RLS y el aislamiento seria decorativo."
        )
    if app_role.can_bypass_rls:
        out.append(
            f"R7 el rol de aplicacion {app_role.name} tiene BYPASSRLS "
            "(ADR-011, 7.8): se saltaria el RLS y el aislamiento seria decorativo."
        )
    return out


def check_schema(
    tables: Sequence[TableInfo], app_role: AppRoleInfo | None
) -> list[str]:
    """Logica pura del check 7.8: devuelve las violaciones (vacia = verde)."""
    violations: list[str] = []
    for table in tables:
        violations.extend(_table_violations(table))
    violations.extend(_app_role_violations(app_role))
    return violations


_TABLES_SQL = """
SELECT c.relname,
       obj_description(c.oid, 'pg_class'),
       c.relrowsecurity,
       c.relforcerowsecurity
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname = 'public'
ORDER BY c.relname
"""

_COLUMNS_SQL = """
SELECT c.relname, a.attname
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname = 'public'
  AND a.attnum > 0 AND NOT a.attisdropped
"""

_POLICIES_SQL = """
SELECT tablename, policyname, coalesce(qual, ''), coalesce(with_check, '')
FROM pg_policies
WHERE schemaname = 'public'
"""

_ROLE_SQL = "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s"


def _scope_from_comment(comment: str | None) -> str | None:
    if comment is None:
        return None
    match = _SCOPE_RE.search(comment)
    return match.group(1) if match is not None else None


def load_schema(database: Database) -> tuple[list[TableInfo], AppRoleInfo | None]:
    """Lee el esquema del catalogo (pg_catalog / pg_policies, nunca information_schema).

    information_schema oculta objetos segun los privilegios del rol conectado
    y podria dejar pasar una tabla sin grants; el catalogo del sistema los ve
    todos. Por eso el check corre con el DSN de migraciones.
    """
    with database.transaction() as session:
        table_rows = session.fetchall(_TABLES_SQL)
        column_rows = session.fetchall(_COLUMNS_SQL)
        policy_rows = session.fetchall(_POLICIES_SQL)
        role_row = session.fetchone(_ROLE_SQL, (APP_ROLE_NAME,))

    columns: dict[str, set[str]] = {}
    for relname, attname in column_rows:
        columns.setdefault(str(relname), set()).add(str(attname))

    policies: dict[str, list[PolicyInfo]] = {}
    for tablename, policyname, qual, with_check in policy_rows:
        policies.setdefault(str(tablename), []).append(
            PolicyInfo(
                table=str(tablename),
                name=str(policyname),
                using_expr=str(qual),
                with_check_expr=str(with_check),
            )
        )

    tables: list[TableInfo] = []
    for relname, comment, relrowsecurity, relforce in table_rows:
        name = str(relname)
        tables.append(
            TableInfo(
                name=name,
                declared_scope=_scope_from_comment(
                    None if comment is None else str(comment)
                ),
                has_rls=bool(relrowsecurity),
                has_force_rls=bool(relforce),
                columns=frozenset(columns.get(name, set())),
                policies=tuple(policies.get(name, [])),
            )
        )

    app_role: AppRoleInfo | None = None
    if role_row is not None:
        app_role = AppRoleInfo(
            name=APP_ROLE_NAME,
            is_superuser=bool(role_row[0]),
            can_bypass_rls=bool(role_row[1]),
        )
    return tables, app_role


def main() -> int:
    database = PsycopgDatabase(DbConfig.migrations_from_env())
    try:
        tables, app_role = load_schema(database)
    finally:
        database.close()

    violations = check_schema(tables, app_role)
    if violations:
        print("FAIL check 7.8 (tenancy/RLS):")
        for v in violations:
            print(f"  - {v}")
        return 1

    print(
        f"OK check 7.8 (tenancy/RLS): {len(tables)} tablas revisadas, "
        "aislamiento y RLS conformes (ADR-011)."
    )
    for table in tables:
        print(f"  - {table.name}: isolation_scope={table.declared_scope}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
