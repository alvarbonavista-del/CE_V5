"""Siembra el escenario FALSO de la validacion en caliente de P06 (B9).

DATOS INVENTADOS, JAMAS REALES. El catalogo comercial (jurisdicciones, planes,
reglamento) es DATO de negocio de Alvaro; aqui se fabrica un escenario de juguete
solo para demostrar que un kill switch corta SIN reinicio. No sembrar nunca datos
reales con esto.

Que siembra (idempotente: si ya existe, no duplica):
- policy_version 'pv_demo' en estado 'current' (rol de MIGRACIONES).
- Dos reglas ALLOW sin condiciones (match_* nulos = comodin): 'execute_order'
  (sensible) y 'view_dashboard' (no sensible).
- Un tenant y un usuario (reusa provision_tenant_for_user, rol de APLICACION bajo
  RLS). El user_id es FIJO e inventado para que re-sembrar reutilice su tenant.
- Un entitlement de 'execute_order' para ese sujeto: sin el, el gate denegaria por
  entitlement ausente (D6), y la demo no arrancaria en ALLOW.

Roles: las tablas de plataforma (policy_version, policy_rule) y el entitlement se
escriben con el rol de MIGRACIONES (superusuario, ve y escribe todo). El alta del
tenant va con el rol de APLICACION porque provision_tenant_for_user EXIGE un rol
sin BYPASSRLS (el RLS no seria RLS si lo escribiera un superusuario).

Uso: python tools/seed_p06_fake.py
Requiere CE_V5_DATABASE_URL (app) y CE_V5_MIGRATIONS_DATABASE_URL (migraciones).
"""

import sys
from pathlib import Path
from uuid import UUID, uuid4

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))
sys.path.insert(0, str(REPO_ROOT / "contracts"))

from ce_v5.infra.db.config import DbConfig  # noqa: E402
from ce_v5.infra.db.ports import Database  # noqa: E402
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase  # noqa: E402
from ce_v5.infra.db.tenancy import provision_tenant_for_user  # noqa: E402

_POLICY_VERSION = "pv_demo"
_ACTOR = "seed-p06-fake"
# user_id FIJO e INVENTADO: re-sembrar reutiliza su tenant en vez de crear otro.
_DEMO_USER_ID = UUID("dede0000-0000-4000-8000-000000000001")
_ALLOW_CAPS = ("execute_order", "view_dashboard")
_ENTITLED_CAP = "execute_order"


def _seed_policy(migrations_db: Database) -> None:
    with migrations_db.transaction() as session:
        # Una sola version puede estar 'current' (indice unico parcial de 0007):
        # se degradan las demas antes de asentar pv_demo como vigente.
        session.execute(
            "UPDATE policy_version SET status = 'superseded' "
            "WHERE status = 'current' AND policy_version <> %s",
            (_POLICY_VERSION,),
        )
        session.execute(
            "INSERT INTO policy_version (policy_version, status, actor) "
            "VALUES (%s, 'current', %s) "
            "ON CONFLICT (policy_version) DO UPDATE SET status = 'current'",
            (_POLICY_VERSION, _ACTOR),
        )
        session.execute(
            "DELETE FROM policy_rule WHERE policy_version = %s", (_POLICY_VERSION,)
        )
        for capability_id in _ALLOW_CAPS:
            session.execute(
                "INSERT INTO policy_rule (rule_id, policy_version, capability_id, "
                "effect, reason_code) "
                "VALUES (%s, %s, %s, 'allow', 'allowed_by_policy')",
                (str(uuid4()), _POLICY_VERSION, capability_id),
            )


def _tenant_for_user(migrations_db: Database, app_db: Database, user_id: UUID) -> UUID:
    with migrations_db.transaction() as session:
        row = session.fetchone(
            "SELECT tenant_id FROM user_tenant_membership WHERE user_id = %s",
            (str(user_id),),
        )
    if row is not None:
        return UUID(str(row[0]))
    # Alta real bajo RLS con el rol de aplicacion (no el de migraciones).
    return provision_tenant_for_user(app_db, user_id)


def _seed_entitlement(migrations_db: Database, tenant_id: UUID, user_id: UUID) -> None:
    with migrations_db.transaction() as session:
        session.execute(
            "DELETE FROM policy_entitlement WHERE tenant_id = %s AND user_id = %s "
            "AND capability_id = %s",
            (str(tenant_id), str(user_id), _ENTITLED_CAP),
        )
        session.execute(
            "INSERT INTO policy_entitlement (entitlement_id, tenant_id, user_id, "
            "capability_id, source) VALUES (%s, %s, %s, %s, 'admin')",
            (str(uuid4()), str(tenant_id), str(user_id), _ENTITLED_CAP),
        )


def main() -> None:
    app_db = PsycopgDatabase(DbConfig.from_env())
    migrations_db = PsycopgDatabase(DbConfig.migrations_from_env())
    try:
        _seed_policy(migrations_db)
        tenant_id = _tenant_for_user(migrations_db, app_db, _DEMO_USER_ID)
        _seed_entitlement(migrations_db, tenant_id, _DEMO_USER_ID)
    finally:
        app_db.close()
        migrations_db.close()

    print("Escenario FALSO sembrado (datos INVENTADOS, jamas reales).")
    print(f"  policy_version : {_POLICY_VERSION} (current)")
    print("  reglas ALLOW   : execute_order (sensible), view_dashboard (no sensible)")
    print(f"  entitlement    : {_ENTITLED_CAP} para el sujeto")
    print()
    print("Copia-pega para la validacion en caliente:")
    print(f"  tenant_id = {tenant_id}")
    print(f"  user_id   = {_DEMO_USER_ID}")
    print()
    print("  python -m ce_v5.entrypoints.hot_validation_policy \\")
    print(f"      {tenant_id} {_DEMO_USER_ID}")


if __name__ == "__main__":
    main()
