"""Check "audit" (P06): las dos auditorias son inmutables y aisladas.

Materializa en CI las garantias de auditoria de P06 contra la base ya migrada.
FALLA si sensitive_action_audit u operator_audit no existen o les falta una
columna; si a alguna le falta RLS ENABLE + FORCE; si el rol de aplicacion puede
EDITAR (UPDATE/DELETE/TRUNCATE) cualquiera de las dos; si el rol de operador
tiene CUALQUIER privilegio sobre sensitive_action_audit (no es suyo); o si el rol
de operador puede REESCRIBIR (UPDATE/DELETE) operator_audit (escribir su traza
si; reescribirla jamas).

Lee el catalogo con pg_catalog y has_table_privilege (NUNCA information_schema,
que oculta objetos segun privilegios), con el DSN de migraciones para visibilidad
total. La logica pura (check_audit) es testeable sin PostgreSQL; solo
load_audit_facts toca el catalogo.
"""

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "backend" / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(REPO_ROOT / "contracts"))

from ce_v5.infra.db.config import DbConfig  # noqa: E402
from ce_v5.infra.db.ports import Database  # noqa: E402
from ce_v5.infra.db.provision import (  # noqa: E402
    APP_ROLE_NAME,
    OPERATOR_ROLE_NAME,
)
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase  # noqa: E402

_SENSITIVE = "sensitive_action_audit"
_OPERATOR_AUDIT = "operator_audit"

_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    _SENSITIVE: frozenset(
        {
            "audit_id",
            "tenant_id",
            "user_id",
            "capability_id",
            "decision",
            "reason_code",
            "policy_version",
            "sensitive",
            "context",
            "evaluated_at",
            # Discriminador de CA-11: sin el, los dos vocabularios de reason_code
            # (politica y auth) se confundirian en la misma columna.
            "audit_kind",
        }
    ),
    _OPERATOR_AUDIT: frozenset(
        {
            "audit_id",
            "action",
            "actor",
            "reason_code",
            "kill_switch_id",
            "policy_version",
            "previous_current",
            "new_current",
            "correlation_id",
            "event_id",
            "recorded_at",
        }
    ),
}

# (rol, tabla, privilegio) que NO debe estar concedido; se comprueba con
# has_table_privilege. El rol de aplicacion no puede EDITAR ninguna auditoria; el
# de operador no puede tocar la de sujeto ni reescribir la suya.
_FORBIDDEN: tuple[tuple[str, str, str], ...] = (
    (APP_ROLE_NAME, _SENSITIVE, "UPDATE"),
    (APP_ROLE_NAME, _SENSITIVE, "DELETE"),
    (APP_ROLE_NAME, _SENSITIVE, "TRUNCATE"),
    (APP_ROLE_NAME, _OPERATOR_AUDIT, "UPDATE"),
    (APP_ROLE_NAME, _OPERATOR_AUDIT, "DELETE"),
    (APP_ROLE_NAME, _OPERATOR_AUDIT, "TRUNCATE"),
    (OPERATOR_ROLE_NAME, _SENSITIVE, "SELECT"),
    (OPERATOR_ROLE_NAME, _SENSITIVE, "INSERT"),
    (OPERATOR_ROLE_NAME, _SENSITIVE, "UPDATE"),
    (OPERATOR_ROLE_NAME, _SENSITIVE, "DELETE"),
    (OPERATOR_ROLE_NAME, _SENSITIVE, "TRUNCATE"),
    (OPERATOR_ROLE_NAME, _OPERATOR_AUDIT, "UPDATE"),
    (OPERATOR_ROLE_NAME, _OPERATOR_AUDIT, "DELETE"),
)


@dataclass(frozen=True, slots=True)
class AuditTable:
    """Estado de una tabla de auditoria segun el catalogo."""

    name: str
    exists: bool
    columns: frozenset[str]
    has_rls: bool
    has_force_rls: bool


def _forbidden_reason(role: str, table: str) -> str:
    if table == _SENSITIVE and role == OPERATOR_ROLE_NAME:
        return "no es suyo: es auditoria por sujeto"
    if role == OPERATOR_ROLE_NAME:
        return "puede escribir su traza, jamas reescribirla"
    return "una auditoria editable no es una auditoria"


def check_audit(
    tables: Mapping[str, AuditTable],
    has_privilege: Mapping[tuple[str, str, str], bool],
) -> list[str]:
    """Logica pura del check audit: devuelve las violaciones (vacia = verde)."""
    problems: list[str] = []
    for name in (_SENSITIVE, _OPERATOR_AUDIT):
        table = tables.get(name)
        if table is None or not table.exists:
            problems.append(f"{name}: la tabla de auditoria no existe (P06).")
            continue
        missing = _REQUIRED_COLUMNS[name] - table.columns
        if missing:
            problems.append(
                f"{name}: faltan columnas obligatorias: {sorted(missing)} (P06)."
            )
        if not (table.has_rls and table.has_force_rls):
            problems.append(
                f"{name}: sin RLS ENABLE + FORCE; una auditoria sin RLS forzado "
                "no aisla (P06)."
            )
    for role, tbl, privilege in _FORBIDDEN:
        if has_privilege.get((role, tbl, privilege), False):
            problems.append(
                f"{tbl}: el rol {role} tiene {privilege} "
                f"({_forbidden_reason(role, tbl)}) (P06)."
            )
    return problems


_TABLES_SQL = """
SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname = 'public'
"""

_COLUMNS_SQL = """
SELECT c.relname, a.attname
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname = 'public'
  AND a.attnum > 0 AND NOT a.attisdropped
"""


def _read_tables(rows: Sequence[tuple[object, ...]]) -> dict[str, tuple[bool, bool]]:
    return {str(row[0]): (bool(row[1]), bool(row[2])) for row in rows}


def _read_columns(rows: Sequence[tuple[object, ...]]) -> dict[str, set[str]]:
    columns: dict[str, set[str]] = {}
    for relname, attname in rows:
        columns.setdefault(str(relname), set()).add(str(attname))
    return columns


def _read_privileges(
    database_session_rows: Sequence[tuple[object, ...]],
) -> dict[tuple[str, str, str], bool]:
    return {
        (str(row[0]), str(row[1]), str(row[2])): bool(row[3])
        for row in database_session_rows
    }


def load_audit_facts(
    database: Database,
) -> tuple[dict[str, AuditTable], dict[tuple[str, str, str], bool]]:
    """Lee del catalogo el estado de las tablas de auditoria y sus privilegios."""
    with database.transaction() as session:
        rls = _read_tables(session.fetchall(_TABLES_SQL))
        columns = _read_columns(session.fetchall(_COLUMNS_SQL))
        tables: dict[str, AuditTable] = {}
        for name in (_SENSITIVE, _OPERATOR_AUDIT):
            if name in rls:
                has_rls, has_force = rls[name]
                tables[name] = AuditTable(
                    name=name,
                    exists=True,
                    columns=frozenset(columns.get(name, set())),
                    has_rls=has_rls,
                    has_force_rls=has_force,
                )
            else:
                tables[name] = AuditTable(name, False, frozenset(), False, False)

        combos = [combo for combo in _FORBIDDEN if tables[combo[1]].exists]
        has_privilege: dict[tuple[str, str, str], bool] = {}
        if combos:
            placeholders = ", ".join(["(%s, %s, %s)"] * len(combos))
            params: list[str] = [value for combo in combos for value in combo]
            priv_sql = (
                "SELECT v.role, v.tbl, v.priv, "
                "has_table_privilege(v.role, v.tbl, v.priv) "
                f"FROM (VALUES {placeholders}) AS v(role, tbl, priv)"
            )
            has_privilege = _read_privileges(session.fetchall(priv_sql, params))
    return tables, has_privilege


def main() -> int:
    database = PsycopgDatabase(DbConfig.migrations_from_env())
    try:
        tables, has_privilege = load_audit_facts(database)
    finally:
        database.close()

    problems = check_audit(tables, has_privilege)
    if problems:
        print("FAIL check audit (auditoria de seguridad, P06):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(
        "OK check audit (auditoria de seguridad, P06): sensitive_action_audit y "
        "operator_audit son inmutables y aisladas."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
