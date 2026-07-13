"""Validacion en caliente CRITICA de P05: aislamiento entre usuarios (ADR-011).

Demuestra por el CAMINO DEL CODIGO (nunca SQL a mano) contra el PostgreSQL
local que un intento de fuga cross-tenant FALLA: lecturas, borrados y
escrituras de A sobre el tenant de B quedan bloqueados por RLS; sin
pertenencia se falla cerrado; y un rol capaz de saltarse el RLS es rechazado.

Sandbox/local, NUNCA datos reales: base de juguete (DOC_ENTREGABLES sec.5).
Los usuarios son FALSOS y se dan de alta en cada ejecucion por la ventanilla
auth_register_user (P06b, CA-07): desde la migracion 0010 la pertenencia exige un
usuario existente (FK), asi que ya no vale inventarse un uuid4.

Uso: python tools/validate_p05_tenancy.py
Exige CE_V5_DATABASE_URL (rol de aplicacion) y CE_V5_MIGRATIONS_DATABASE_URL
(rol de migraciones, solo para la comprobacion 7).
"""

from __future__ import annotations

from uuid import uuid4

from ce_v5.core.tenancy import TenantResolutionError
from ce_v5.infra.db.config import DbConfig
from ce_v5.infra.db.identity import register_user
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.infra.db.tenancy import (
    AppRoleError,
    MembershipRepository,
    TenantScopedDatabase,
    provision_tenant_for_user,
)

# Credencial FALSA de validacion: la ventanilla recibe el hash ya calculado, y este
# no es un Argon2id real (aqui no se autentica a nadie, solo se necesita un usuario).
_PASSWORD_HASH = "hash-de-prueba-no-es-argon2"


def _fake_email() -> str:
    """Email FALSO y unico por ejecucion (jamas un buzon real)."""
    return f"fake-{uuid4().hex}@ejemplo.test"


def main() -> None:
    failures: list[str] = []

    app_db = PsycopgDatabase(DbConfig.from_env())
    try:
        scoped_db = TenantScopedDatabase(app_db)

        # 0. CANON: los usuarios existen de verdad (ventanilla auth_register_user).
        # Sin esto, la pertenencia violaria la FK de la 0010.
        user_a = register_user(app_db, _fake_email(), _PASSWORD_HASH)
        user_b = register_user(app_db, _fake_email(), _PASSWORD_HASH)

        # 1. ALTA: cada usuario obtiene su propio tenant.
        tenant_a = provision_tenant_for_user(app_db, user_a)
        tenant_b = provision_tenant_for_user(app_db, user_b)
        print(f"[1] ALTA -> A {user_a} -> tenant {tenant_a}")
        print(f"            B {user_b} -> tenant {tenant_b}")

        # 2. AISLAMIENTO NORMAL: bajo A solo se ve A y su unico tenant.
        with scoped_db.transaction(user_a) as scoped:
            members = MembershipRepository(scoped).members()
            row = scoped.session.fetchone("SELECT count(*) FROM tenant")
        assert row is not None
        total = row[0]
        assert isinstance(total, int)
        ok2 = members == [user_a] and total == 1
        print(
            f"[2] AISLAMIENTO -> members(A)==[A]:{members == [user_a]} "
            f"count(tenant) bajo A:{total} (esperado True y 1)"
        )
        if not ok2:
            failures.append(
                "aislamiento normal: members(A) debia ser [A] y count(tenant) 1"
            )

        # 3. FUGA DE LECTURA BLOQUEADA: A no puede leer la fila del tenant de B.
        with scoped_db.transaction(user_a) as scoped:
            leak = scoped.session.fetchall(
                "SELECT tenant_id FROM tenant WHERE tenant_id = %s",
                (str(tenant_b),),
            )
        print(
            f"[3] FUGA LECTURA -> SELECT del tenant de B bajo A devuelve "
            f"{len(leak)} fila(s) (esperado 0)"
        )
        if len(leak) != 0:
            failures.append("fuga de lectura: A pudo leer la fila del tenant de B")

        # 4. FUGA DE BORRADO BLOQUEADA: el DELETE de A no toca la fila de B.
        with scoped_db.transaction(user_a) as scoped:
            deleted = scoped.session.fetchall(
                "DELETE FROM tenant WHERE tenant_id = %s RETURNING tenant_id",
                (str(tenant_b),),
            )
        with scoped_db.transaction(user_b) as scoped:
            sigue = scoped.session.fetchall(
                "SELECT tenant_id FROM tenant WHERE tenant_id = %s",
                (str(tenant_b),),
            )
        sobrevive = len(sigue) == 1
        print(
            f"[4] FUGA BORRADO -> DELETE del tenant de B bajo A borro "
            f"{len(deleted)} fila(s); sigue existiendo bajo B:{sobrevive} "
            "(esperado 0 y True)"
        )
        if len(deleted) != 0 or not sobrevive:
            failures.append(
                "fuga de borrado: A borro (o pudo borrar) la fila del tenant de B"
            )

        # 5. FUGA DE ESCRITURA BLOQUEADA: INSERT de A con el tenant de B falla.
        lanzo5 = False
        mensaje5 = ""
        try:
            with scoped_db.transaction(user_a) as scoped:
                scoped.session.execute(
                    "INSERT INTO user_tenant_membership (user_id, tenant_id) "
                    "VALUES (%s, %s)",
                    (str(uuid4()), str(tenant_b)),
                )
        except Exception as exc:
            # Se captura Exception a proposito: el driver no se importa aqui
            # (REST-15), asi que la clase concreta del error no es visible.
            lanzo5 = True
            mensaje5 = str(exc)
        ok5 = lanzo5 and "row-level security" in mensaje5.lower()
        print(f"[5] FUGA ESCRITURA -> INSERT con el tenant de B bajo A lanzo:{lanzo5}")
        if mensaje5:
            print(f"        mensaje real de PostgreSQL: {mensaje5.strip()}")
        if not ok5:
            failures.append(
                "fuga de escritura: el INSERT cross-tenant no fue bloqueado por RLS"
            )

        # 6. SIN PERTENENCIA, FALLA CERRADO.
        huerfano = uuid4()
        lanzo6 = False
        try:
            with scoped_db.transaction(huerfano):
                pass
        except TenantResolutionError:
            lanzo6 = True
        print(
            f"[6] FALLA CERRADO -> transaction() sin pertenencia lanza "
            f"TenantResolutionError:{lanzo6} (esperado True)"
        )
        if not lanzo6:
            failures.append(
                "sin pertenencia: no fallo cerrado (esperaba TenantResolutionError)"
            )
    finally:
        app_db.close()

    # 7. ROL CON BYPASS RECHAZADO: el rol de migraciones puede saltarse el RLS,
    # asi que el sistema se niega a operar con el en vez de fingir aislamiento.
    mig_db = PsycopgDatabase(DbConfig.migrations_from_env())
    try:
        scoped_mig = TenantScopedDatabase(mig_db)
        lanzo7 = False
        mensaje7 = ""
        try:
            with scoped_mig.transaction(user_a):
                pass
        except AppRoleError as exc:
            lanzo7 = True
            mensaje7 = str(exc)
        print(
            f"[7] ROL BYPASS -> transaction() con el rol de migraciones lanza "
            f"AppRoleError:{lanzo7} (esperado True)"
        )
        if mensaje7:
            print(f"        mensaje: {mensaje7.strip()}")
        if not lanzo7:
            failures.append(
                "rol con bypass: el rol de migraciones pudo operar bajo RLS"
            )
    finally:
        mig_db.close()

    if failures:
        print(f"RESUMEN: FALLO - {len(failures)} comprobacion(es) no se cumplieron:")
        for reason in failures:
            print(f"  - {reason}")
        raise SystemExit(1)
    print("RESUMEN: OK - aislamiento entre usuarios demostrado (7/7).")


if __name__ == "__main__":
    main()
