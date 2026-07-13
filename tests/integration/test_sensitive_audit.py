"""Tests de integracion de la auditoria de accion sensible (P06 B7, CA-05).

Contra PostgreSQL real, con el rol de APLICACION bajo RLS. Una decision sensible
deja fila legible SOLO por su tenant (aislamiento como P05); UPDATE y DELETE de
la propia fila los rechaza el motor (append-only). El driver solo lo conoce el
adapter (REST-15). DATOS FALSOS SIEMPRE.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from uuid import UUID

import pytest

from ce_v5.core.policy import Decision, ReasonCode, SensitiveActionRecord
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.infra.db.sensitive_audit import PostgresSensitiveActionAudit
from ce_v5.infra.db.tenancy import TenantScopedDatabase, provision_tenant_for_user

_DSN = os.environ.get("CE_V5_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    _DSN is None, reason="requiere CE_V5_DATABASE_URL (PostgreSQL local)"
)


def _record(
    tenant_id: UUID, user_id: UUID, capability_id: str
) -> SensitiveActionRecord:
    return SensitiveActionRecord(
        tenant_id=str(tenant_id),
        user_id=str(user_id),
        capability_id=capability_id,
        decision=Decision.DENY,
        reason_code=ReasonCode.DENIED_BY_KILL_SWITCH,
        policy_version="v1",
        sensitive=True,
        context={"kill_switch_id": "ks-1", "jurisdiction": "AA"},
    )


def test_escribe_y_lee_solo_su_tenant(
    app_db: PsycopgDatabase, crear_usuario: Callable[[], UUID]
) -> None:
    user = crear_usuario()
    tenant = provision_tenant_for_user(app_db, user)
    PostgresSensitiveActionAudit(app_db).record(_record(tenant, user, "execute_order"))

    scoped_db = TenantScopedDatabase(app_db)
    with scoped_db.transaction(user) as scoped:
        rows = scoped.session.fetchall(
            "SELECT capability_id, decision FROM sensitive_action_audit"
        )
    assert [(str(row[0]), str(row[1])) for row in rows] == [("execute_order", "deny")]


def test_update_y_delete_de_la_propia_traza_rechazados(
    app_db: PsycopgDatabase, crear_usuario: Callable[[], UUID]
) -> None:
    user = crear_usuario()
    tenant = provision_tenant_for_user(app_db, user)
    PostgresSensitiveActionAudit(app_db).record(_record(tenant, user, "manual_order"))

    scoped_db = TenantScopedDatabase(app_db)
    with pytest.raises(Exception, match="permission denied"):
        with scoped_db.transaction(user) as scoped:
            scoped.session.execute(
                "UPDATE sensitive_action_audit SET reason_code = 'reescrito'"
            )
    with pytest.raises(Exception, match="permission denied"):
        with scoped_db.transaction(user) as scoped:
            scoped.session.execute("DELETE FROM sensitive_action_audit")


def test_un_tenant_no_ve_la_auditoria_de_otro(
    app_db: PsycopgDatabase, crear_usuario: Callable[[], UUID]
) -> None:
    user_a = crear_usuario()
    tenant_a = provision_tenant_for_user(app_db, user_a)
    user_b = crear_usuario()
    provision_tenant_for_user(app_db, user_b)
    PostgresSensitiveActionAudit(app_db).record(
        _record(tenant_a, user_a, "activate_autotrade")
    )

    scoped_db = TenantScopedDatabase(app_db)
    with scoped_db.transaction(user_b) as scoped:
        rows = scoped.session.fetchall(
            "SELECT capability_id FROM sensitive_action_audit"
        )
    assert rows == []
