"""Vuelca las dos auditorias de P06 en texto legible para el informe (B9).

Con el rol de MIGRACIONES (para verlo TODO, saltando RLS y grants), imprime en
texto plano, sin colores ni adornos, las dos trazas que deja la validacion en
caliente:
- operator_audit: la traza CANONICA de la accion de OPERADOR (CA-05): que hizo,
  quien, por que, sobre que kill switch, con que correlation_id y event_id.
- sensitive_action_audit: la traza de SEGURIDAD por SUJETO (ADR-012): cada
  decision del gate sobre una capability sensible, con su reason_code, la
  policy_version vigente y el contexto resumido.

Salida pensada para pegar en el informe de entrega.

Uso: python tools/show_p06_audit.py
Requiere CE_V5_MIGRATIONS_DATABASE_URL.
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))
sys.path.insert(0, str(REPO_ROOT / "contracts"))

from ce_v5.infra.db.config import DbConfig  # noqa: E402
from ce_v5.infra.db.ports import Database  # noqa: E402
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase  # noqa: E402

_OPERATOR_AUDIT_SQL = (
    "SELECT recorded_at, action, actor, reason_code, kill_switch_id, "
    "policy_version, previous_current, new_current, correlation_id, event_id "
    "FROM operator_audit ORDER BY recorded_at"
)

_SENSITIVE_AUDIT_SQL = (
    "SELECT evaluated_at, capability_id, decision, reason_code, policy_version, "
    "sensitive, context FROM sensitive_action_audit ORDER BY evaluated_at"
)


def _context_text(value: object) -> str:
    if value is None:
        return "{}"
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _dump_operator_audit(database: Database) -> None:
    with database.transaction() as session:
        rows = session.fetchall(_OPERATOR_AUDIT_SQL)
    print("=== operator_audit (traza canonica del OPERADOR, CA-05) ===")
    if not rows:
        print("  (vacio)")
    for row in rows:
        print(
            f"  [{row[0]}] {row[1]} actor={row[2]} reason={row[3]} "
            f"kill_switch={row[4]} policy_version={row[5]} "
            f"prev={row[6]} new={row[7]} correlation={row[8]} event={row[9]}"
        )
    print()


def _dump_sensitive_audit(database: Database) -> None:
    with database.transaction() as session:
        rows = session.fetchall(_SENSITIVE_AUDIT_SQL)
    print("=== sensitive_action_audit (traza de SEGURIDAD por SUJETO, ADR-012) ===")
    if not rows:
        print("  (vacio)")
    for row in rows:
        print(
            f"  [{row[0]}] {row[1]} {row[2]} reason={row[3]} "
            f"policy_version={row[4]} sensitive={row[5]} "
            f"context={_context_text(row[6])}"
        )
    print()


def main() -> None:
    database = PsycopgDatabase(DbConfig.migrations_from_env())
    try:
        _dump_operator_audit(database)
        _dump_sensitive_audit(database)
    finally:
        database.close()


if __name__ == "__main__":
    main()
