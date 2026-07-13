"""Siembra el escenario FALSO de la validacion en caliente de P06b (Bloque H).

DATOS INVENTADOS, JAMAS REALES. El reglamento de verdad (que capacidad se concede a
quien, en que jurisdiccion y con que plan) es DATO de negocio de Alvaro; aqui se fabrica
un escenario de juguete cuyo unico proposito es demostrar que la puerta publica
autentica, resuelve identidad y falla CERRADA, y que un kill switch la corta SIN
reinicio.

Que siembra (idempotente: re-sembrar deja el mismo estado, no duplica):
- policy_version 'pv_demo_p06b' en estado 'current' (las demas se degradan: el indice
  unico parcial de 0007 solo admite una vigente).
- Una regla ALLOW para 'subscribe_realtime': la capability NO SENSIBLE que gatea la
  suscripcion realtime, y la UNICA que P06b gatea.
- Una regla ALLOW para 'view_dashboard' (tambien no sensible).
- NINGUN entitlement para 'execute_order', Y A PROPOSITO. Sin regla y sin entitlement, y
  con la jurisdiccion desconocida (no hay proveedor de geo/KYC en v5.0), lo SENSIBLE se
  deniega por defecto. Que la demo arranque con execute_order en DENY no es un fallo del
  escenario: es la tesis que se quiere ver en pantalla.

NO siembra ningun usuario: el arnes de validacion da de alta el suyo por la puerta real
(POST /v1/auth/register), que es precisamente lo que se quiere probar.

Rol: MIGRACIONES. policy_version y policy_rule son tablas de PLATAFORMA, no de tenant.

Uso: python tools/seed_p06b_fake.py
Requiere CE_V5_MIGRATIONS_DATABASE_URL (migraciones).
"""

import sys
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))
sys.path.insert(0, str(REPO_ROOT / "contracts"))

from ce_v5.infra.db.config import DbConfig  # noqa: E402
from ce_v5.infra.db.ports import Database  # noqa: E402
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase  # noqa: E402

_POLICY_VERSION = "pv_demo_p06b"
_ACTOR = "seed-p06b-fake"
# La capability que gatea la suscripcion realtime (entrypoints/api/realtime.py).
_REALTIME_CAP = "subscribe_realtime"
_ALLOW_CAPS = (_REALTIME_CAP, "view_dashboard")
# Sensible y DELIBERADAMENTE sin conceder: se deniega sola.
_SENSITIVE_CAP = "execute_order"


def _seed_policy(migrations_db: Database) -> None:
    with migrations_db.transaction() as session:
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


def main() -> None:
    migrations_db = PsycopgDatabase(DbConfig.migrations_from_env())
    try:
        _seed_policy(migrations_db)
    finally:
        migrations_db.close()

    print("Escenario FALSO de P06b sembrado (datos INVENTADOS, jamas reales).")
    print(f"  policy_version : {_POLICY_VERSION} (current)")
    print(f"  regla ALLOW    : {_REALTIME_CAP} (no sensible; gatea el realtime)")
    print("  regla ALLOW    : view_dashboard (no sensible)")
    print(f"  entitlement    : NINGUNO para {_SENSITIVE_CAP} (a proposito)")
    print()
    print("Lo sensible se deniega por defecto: sin entitlement y con la jurisdiccion")
    print("desconocida, execute_order sale DENY. La API no concede nada por su cuenta.")
    print()
    print("Siguiente paso (terminal del arnes):")
    print("  python tools/validate_p06b_api.py")


if __name__ == "__main__":
    main()
