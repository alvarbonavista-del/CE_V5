"""Tests de integracion de politica: kill switch y auditoria (P06, CA-03).

Contra PostgreSQL real. Separacion de poderes por el MOTOR (grants + RLS), no
por el codigo: el rol de aplicacion lee kill switches pero no los escribe; el
rol de operador los escribe pero no ve datos de tenant. La auditoria de
seguridad por sujeto es append-only y aislada por tenant, como en P05. El
driver solo lo conoce el adapter (REST-15): aqui se captura Exception. NUNCA
datos reales: base de juguete (DOC_ENTREGABLES sec.5).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from uuid import UUID, uuid4

import pytest

from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.infra.db.tenancy import TenantScopedDatabase, provision_tenant_for_user

_DSN = os.environ.get("CE_V5_DATABASE_URL")
_OPERATOR_DSN = os.environ.get("CE_V5_OPERATOR_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    _DSN is None or _OPERATOR_DSN is None,
    reason="requiere CE_V5_DATABASE_URL y CE_V5_OPERATOR_DATABASE_URL",
)

_INSERT_CAP_SWITCH = (
    "INSERT INTO kill_switch "
    "(kill_switch_id, scope, target_ref, reason_code, actor) "
    "VALUES (%s, 'capability', %s, %s, %s)"
)

_INSERT_AUDIT = (
    "INSERT INTO sensitive_action_audit "
    "(audit_id, tenant_id, capability_id, decision, reason_code, "
    "policy_version, sensitive) "
    "VALUES (%s, %s, %s, 'deny', %s, %s, true)"
)


def _insert_capability_switch(operator_db: PsycopgDatabase) -> UUID:
    """El operador inserta un kill switch de capability con objetivo unico."""
    ks_id = uuid4()
    with operator_db.transaction() as session:
        session.execute(
            _INSERT_CAP_SWITCH, (str(ks_id), f"cap-{ks_id}", "manual", "operador")
        )
    return ks_id


def test_operador_inserta_y_app_lee_kill_switch(
    app_db: PsycopgDatabase, operator_db: PsycopgDatabase
) -> None:
    ks_id = _insert_capability_switch(operator_db)
    with app_db.transaction() as session:
        rows = session.fetchall(
            "SELECT kill_switch_id FROM kill_switch WHERE kill_switch_id = %s",
            (str(ks_id),),
        )
    assert [UUID(str(row[0])) for row in rows] == [ks_id]


def test_app_no_puede_insertar_kill_switch(app_db: PsycopgDatabase) -> None:
    # El motor rechaza por privilegios (no hay GRANT INSERT ni policy de
    # escritura para ce_v5_app), no el codigo de aplicacion.
    with pytest.raises(Exception, match="permission denied"):
        with app_db.transaction() as session:
            session.execute(
                _INSERT_CAP_SWITCH,
                (str(uuid4()), f"cap-{uuid4()}", "manual", "app"),
            )


def test_app_no_puede_actualizar_kill_switch(
    app_db: PsycopgDatabase, operator_db: PsycopgDatabase
) -> None:
    ks_id = _insert_capability_switch(operator_db)
    with pytest.raises(Exception, match="permission denied"):
        with app_db.transaction() as session:
            session.execute(
                "UPDATE kill_switch SET active = false, deactivated_at = now() "
                "WHERE kill_switch_id = %s",
                (str(ks_id),),
            )


def test_operador_actualiza_kill_switch(
    operator_db: PsycopgDatabase,
) -> None:
    ks_id = _insert_capability_switch(operator_db)
    with operator_db.transaction() as session:
        session.execute(
            "UPDATE kill_switch SET active = false, deactivated_at = now() "
            "WHERE kill_switch_id = %s",
            (str(ks_id),),
        )
        rows = session.fetchall(
            "SELECT active FROM kill_switch WHERE kill_switch_id = %s",
            (str(ks_id),),
        )
    assert rows[0][0] is False


def test_nadie_puede_borrar_kill_switch(
    app_db: PsycopgDatabase, operator_db: PsycopgDatabase
) -> None:
    ks_id = _insert_capability_switch(operator_db)
    # Ni el operador puede borrar: kill switch no se borra, se desactiva.
    for database in (app_db, operator_db):
        with pytest.raises(Exception, match="permission denied"):
            with database.transaction() as session:
                session.execute(
                    "DELETE FROM kill_switch WHERE kill_switch_id = %s",
                    (str(ks_id),),
                )


def test_check_rechaza_global_con_target_ref(
    operator_db: PsycopgDatabase,
) -> None:
    with pytest.raises(Exception) as excinfo:
        with operator_db.transaction() as session:
            session.execute(
                "INSERT INTO kill_switch "
                "(kill_switch_id, scope, target_ref, reason_code, actor) "
                "VALUES (%s, 'global', %s, %s, %s)",
                (str(uuid4()), "objetivo", "manual", "operador"),
            )
    assert "kill_switch_scope_coherente" in str(excinfo.value)


def test_check_rechaza_usuario_sin_user_id(
    operator_db: PsycopgDatabase,
) -> None:
    with pytest.raises(Exception) as excinfo:
        with operator_db.transaction() as session:
            session.execute(
                "INSERT INTO kill_switch "
                "(kill_switch_id, scope, tenant_id, reason_code, actor) "
                "VALUES (%s, 'user', %s, %s, %s)",
                (str(uuid4()), str(uuid4()), "manual", "operador"),
            )
    assert "kill_switch_scope_coherente" in str(excinfo.value)


def test_sensitive_audit_insert_y_lectura_propio_tenant(
    app_db: PsycopgDatabase, crear_usuario: Callable[[], UUID]
) -> None:
    user = crear_usuario()
    tenant = provision_tenant_for_user(app_db, user)
    scoped_db = TenantScopedDatabase(app_db)
    audit_id = uuid4()
    with scoped_db.transaction(user) as scoped:
        scoped.session.execute(
            _INSERT_AUDIT,
            (
                str(audit_id),
                str(tenant),
                "execute_order",
                "denied_by_kill_switch",
                "2026.07.0",
            ),
        )
        rows = scoped.session.fetchall(
            "SELECT audit_id FROM sensitive_action_audit WHERE audit_id = %s",
            (str(audit_id),),
        )
    assert [UUID(str(row[0])) for row in rows] == [audit_id]


def test_sensitive_audit_sin_update_ni_delete(
    app_db: PsycopgDatabase, crear_usuario: Callable[[], UUID]
) -> None:
    user = crear_usuario()
    tenant = provision_tenant_for_user(app_db, user)
    scoped_db = TenantScopedDatabase(app_db)
    audit_id = uuid4()
    with scoped_db.transaction(user) as scoped:
        scoped.session.execute(
            _INSERT_AUDIT,
            (
                str(audit_id),
                str(tenant),
                "execute_order",
                "denied_by_plan",
                "2026.07.0",
            ),
        )
    with pytest.raises(Exception, match="permission denied"):
        with scoped_db.transaction(user) as scoped:
            scoped.session.execute(
                "UPDATE sensitive_action_audit SET reason_code = %s "
                "WHERE audit_id = %s",
                ("otro", str(audit_id)),
            )
    with pytest.raises(Exception, match="permission denied"):
        with scoped_db.transaction(user) as scoped:
            scoped.session.execute(
                "DELETE FROM sensitive_action_audit WHERE audit_id = %s",
                (str(audit_id),),
            )


def test_sensitive_audit_fuga_cross_tenant_bloqueada(
    app_db: PsycopgDatabase, crear_usuario: Callable[[], UUID]
) -> None:
    user_a = crear_usuario()
    tenant_a = provision_tenant_for_user(app_db, user_a)
    user_b = crear_usuario()
    provision_tenant_for_user(app_db, user_b)
    scoped_db = TenantScopedDatabase(app_db)
    audit_id = uuid4()
    with scoped_db.transaction(user_a) as scoped:
        scoped.session.execute(
            _INSERT_AUDIT,
            (
                str(audit_id),
                str(tenant_a),
                "manual_order",
                "denied_by_kyc",
                "2026.07.0",
            ),
        )
    # Bajo el contexto de B, la fila de A no es visible (RLS, como en P05).
    with scoped_db.transaction(user_b) as scoped:
        rows = scoped.session.fetchall(
            "SELECT audit_id FROM sensitive_action_audit WHERE audit_id = %s",
            (str(audit_id),),
        )
    assert rows == []
