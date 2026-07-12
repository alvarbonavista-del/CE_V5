"""Tests de integracion de las primitivas de operador (P06 B6b, CA-04/CA-05).

Contra PostgreSQL real. El operador escribe con su propio rol; el evaluador lee
con el rol de aplicacion. DATOS FALSOS SIEMPRE. El driver solo lo conoce el
adapter (REST-15): aqui se captura Exception con match. Las filas de
policy_version se siembran con el rol de MIGRACIONES (superusuario).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest

from ce_v5.core.clock import SimulatedClock, SystemClock
from ce_v5.core.policy import (
    Decision,
    EvidenceSource,
    KycStatus,
    PolicyEvaluator,
    PolicyInputs,
    ReasonCode,
    ResolvedJurisdiction,
)
from ce_v5.infra.db.operator_admin import OperatorAdmin, OperatorAdminError
from ce_v5.infra.db.policy_store import PostgresPolicyStore
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.infra.db.tenancy import provision_tenant_for_user
from source.families.policy import KillSwitchScope

_DSN = os.environ.get("CE_V5_DATABASE_URL")
_OPERATOR_DSN = os.environ.get("CE_V5_OPERATOR_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    _DSN is None or _OPERATOR_DSN is None,
    reason="requiere CE_V5_DATABASE_URL y CE_V5_OPERATOR_DATABASE_URL",
)


def _wipe(db: PsycopgDatabase) -> None:
    with db.transaction() as session:
        session.execute("DELETE FROM operator_audit")
        session.execute("DELETE FROM policy_rule")
        session.execute("DELETE FROM policy_override")
        session.execute("DELETE FROM policy_entitlement")
        session.execute("DELETE FROM outbox")
        session.execute("DELETE FROM kill_switch")
        session.execute("DELETE FROM policy_version")


@pytest.fixture(autouse=True)
def _clean(migrator_db: PsycopgDatabase) -> Iterator[None]:
    _wipe(migrator_db)
    yield
    _wipe(migrator_db)


def _seed_version(db: PsycopgDatabase, version: str, status: str) -> None:
    with db.transaction() as session:
        session.execute(
            "INSERT INTO policy_version (policy_version, status, actor) "
            "VALUES (%s, %s, 'seed')",
            (version, status),
        )


def _seed_rule(db: PsycopgDatabase, version: str, capability_id: str) -> None:
    with db.transaction() as session:
        session.execute(
            "INSERT INTO policy_rule (rule_id, policy_version, capability_id, "
            "effect, reason_code) VALUES (%s, %s, %s, 'allow', 'allowed_by_policy')",
            (str(uuid4()), version, capability_id),
        )


def _inputs(tenant_id: UUID, user_id: UUID) -> PolicyInputs:
    return PolicyInputs(
        subject_tenant_id=str(tenant_id),
        subject_user_id=str(user_id),
        jurisdiction=ResolvedJurisdiction("AA", EvidenceSource.KYC, False),
        kyc_status=KycStatus.VERIFIED,
        vpn_detected=False,
        plan="plan_x",
        role=None,
    )


def _admin(operator_db: PsycopgDatabase) -> OperatorAdmin:
    return OperatorAdmin(operator_db, SimulatedClock(start_ms=1000))


def test_activate_escribe_tres_filas_y_event_id_coincide(
    operator_db: PsycopgDatabase, app_db: PsycopgDatabase, migrator_db: PsycopgDatabase
) -> None:
    _seed_version(migrator_db, "pv1", "current")
    result = _admin(operator_db).activate_kill_switch(
        scope=KillSwitchScope.CAPABILITY,
        target_ref="view_dashboard",
        tenant_id=None,
        user_id=None,
        reason_code="manual",
        actor="op",
        correlation_id="corr-1",
    )
    with app_db.transaction() as session:
        killed = session.fetchall(
            "SELECT active FROM kill_switch WHERE kill_switch_id = %s",
            (result.kill_switch_id,),
        )
    assert killed == [(True,)]
    with migrator_db.transaction() as session:
        outbox = session.fetchall(
            "SELECT event_id FROM outbox "
            "WHERE event_type = 'policy.kill_switch_activated'"
        )
        audit = session.fetchall(
            "SELECT event_id, correlation_id FROM operator_audit "
            "WHERE action = 'kill_switch_activated'"
        )
    assert len(outbox) == 1
    assert len(audit) == 1
    assert str(outbox[0][0]) == str(audit[0][0]) == result.event_id
    assert str(audit[0][1]) == "corr-1"


def test_evaluador_ve_el_switch_y_deniega(
    operator_db: PsycopgDatabase, app_db: PsycopgDatabase, migrator_db: PsycopgDatabase
) -> None:
    _seed_version(migrator_db, "pv1", "current")
    _seed_rule(migrator_db, "pv1", "view_dashboard")
    user = uuid4()
    tenant = provision_tenant_for_user(app_db, user)
    evaluator = PolicyEvaluator(PostgresPolicyStore(app_db), SystemClock())
    before = evaluator.evaluate(_inputs(tenant, user), ["view_dashboard"])
    assert before.decisions["view_dashboard"].decision is Decision.ALLOW

    _admin(operator_db).activate_kill_switch(
        scope=KillSwitchScope.CAPABILITY,
        target_ref="view_dashboard",
        tenant_id=None,
        user_id=None,
        reason_code="incidente",
        actor="op",
        correlation_id="c1",
    )
    after = evaluator.evaluate(_inputs(tenant, user), ["view_dashboard"])
    decision = after.decisions["view_dashboard"]
    assert decision.decision is Decision.DENY
    assert decision.reason_code is ReasonCode.DENIED_BY_KILL_SWITCH


def test_deactivate_devuelve_la_capability(
    operator_db: PsycopgDatabase, app_db: PsycopgDatabase, migrator_db: PsycopgDatabase
) -> None:
    _seed_version(migrator_db, "pv1", "current")
    _seed_rule(migrator_db, "pv1", "view_dashboard")
    user = uuid4()
    tenant = provision_tenant_for_user(app_db, user)
    admin = _admin(operator_db)
    activated = admin.activate_kill_switch(
        scope=KillSwitchScope.CAPABILITY,
        target_ref="view_dashboard",
        tenant_id=None,
        user_id=None,
        reason_code="incidente",
        actor="op",
        correlation_id="c1",
    )
    evaluator = PolicyEvaluator(PostgresPolicyStore(app_db), SystemClock())
    denied = evaluator.evaluate(_inputs(tenant, user), ["view_dashboard"])
    assert denied.decisions["view_dashboard"].decision is Decision.DENY

    admin.deactivate_kill_switch(
        UUID(activated.kill_switch_id),
        reason_code="resuelto",
        actor="op",
        correlation_id="c2",
    )
    restored = evaluator.evaluate(_inputs(tenant, user), ["view_dashboard"])
    assert restored.decisions["view_dashboard"].decision is Decision.ALLOW
    with migrator_db.transaction() as session:
        outbox = session.fetchall(
            "SELECT 1 FROM outbox WHERE event_type = 'policy.kill_switch_deactivated'"
        )
        audit = session.fetchall(
            "SELECT 1 FROM operator_audit WHERE action = 'kill_switch_deactivated'"
        )
    assert len(outbox) == 1
    assert len(audit) == 1


def test_deactivate_inexistente_falla_ruidoso(
    operator_db: PsycopgDatabase, migrator_db: PsycopgDatabase
) -> None:
    _seed_version(migrator_db, "pv1", "current")
    with pytest.raises(OperatorAdminError, match="no existe o ya estaba inactivo"):
        _admin(operator_db).deactivate_kill_switch(
            uuid4(), reason_code="x", actor="op", correlation_id="c1"
        )


def test_publish_transiciona_current_y_audita(
    operator_db: PsycopgDatabase, migrator_db: PsycopgDatabase
) -> None:
    _seed_version(migrator_db, "pv1", "current")
    _seed_version(migrator_db, "pv2", "draft")
    result = _admin(operator_db).publish_policy_version(
        "pv2", actor="op", reason_code="release", correlation_id="c1"
    )
    assert result.previous_current == "pv1"
    assert result.new_current == "pv2"
    with migrator_db.transaction() as session:
        rows = session.fetchall("SELECT policy_version, status FROM policy_version")
        outbox = session.fetchall(
            "SELECT 1 FROM outbox WHERE event_type = 'policy.version_published'"
        )
        audit = session.fetchall(
            "SELECT previous_current, new_current FROM operator_audit "
            "WHERE action = 'policy_version_published'"
        )
    statuses = {str(v): str(s) for v, s in rows}
    assert statuses["pv1"] == "superseded"
    assert statuses["pv2"] == "current"
    assert len(outbox) == 1
    assert (str(audit[0][0]), str(audit[0][1])) == ("pv1", "pv2")


def test_dos_current_a_la_vez_es_imposible(migrator_db: PsycopgDatabase) -> None:
    _seed_version(migrator_db, "pv1", "current")
    # Ni el superusuario esquiva el indice unico parcial: la DB lo rechaza.
    with pytest.raises(Exception, match="policy_version_una_current"):
        with migrator_db.transaction() as session:
            session.execute(
                "INSERT INTO policy_version (policy_version, status, actor) "
                "VALUES ('pv2', 'current', 'seed')"
            )


def test_app_no_puede_publicar(
    app_db: PsycopgDatabase, migrator_db: PsycopgDatabase
) -> None:
    _seed_version(migrator_db, "pv1", "current")
    _seed_version(migrator_db, "pv2", "draft")
    with pytest.raises(Exception, match="permission denied"):
        with app_db.transaction() as session:
            session.execute(
                "UPDATE policy_version SET status = 'current' "
                "WHERE policy_version = 'pv2'"
            )


def test_operator_audit_es_append_only(
    operator_db: PsycopgDatabase, migrator_db: PsycopgDatabase
) -> None:
    _seed_version(migrator_db, "pv1", "current")
    _admin(operator_db).activate_kill_switch(
        scope=KillSwitchScope.GLOBAL,
        target_ref=None,
        tenant_id=None,
        user_id=None,
        reason_code="manual",
        actor="op",
        correlation_id="c1",
    )
    with pytest.raises(Exception, match="permission denied"):
        with operator_db.transaction() as session:
            session.execute("UPDATE operator_audit SET reason_code = 'reescrito'")
    with pytest.raises(Exception, match="permission denied"):
        with operator_db.transaction() as session:
            session.execute("DELETE FROM operator_audit")


def test_operador_no_puede_fabricar_hechos(
    operator_db: PsycopgDatabase, migrator_db: PsycopgDatabase
) -> None:
    # OTRA familia: la policy RLS de 0009 lo rechaza (no puede fabricar hechos).
    with pytest.raises(Exception, match="row-level security"):
        with operator_db.transaction() as session:
            session.execute(
                "INSERT INTO outbox (event_id, idempotency_key, stream_key, "
                "event_type, envelope) VALUES (%s, %s, 's', "
                "'execution.order_submitted', '{}'::jsonb)",
                (str(uuid4()), "idem-exec-" + uuid4().hex),
            )
    # policy.*: aceptado (puede denegar de mas, no fabricar hechos ajenos).
    with operator_db.transaction() as session:
        session.execute(
            "INSERT INTO outbox (event_id, idempotency_key, stream_key, "
            "event_type, envelope) VALUES (%s, %s, 's', "
            "'policy.subject_invalidated', '{}'::jsonb)",
            (str(uuid4()), "idem-policy-" + uuid4().hex),
        )
    with migrator_db.transaction() as session:
        rows = session.fetchall(
            "SELECT 1 FROM outbox WHERE event_type = 'policy.subject_invalidated'"
        )
    assert len(rows) == 1


def test_atomicidad_o_las_tres_o_ninguna(
    operator_db: PsycopgDatabase, app_db: PsycopgDatabase, migrator_db: PsycopgDatabase
) -> None:
    _seed_version(migrator_db, "pv1", "current")
    admin = _admin(operator_db)
    activated = admin.activate_kill_switch(
        scope=KillSwitchScope.GLOBAL,
        target_ref=None,
        tenant_id=None,
        user_id=None,
        reason_code="manual",
        actor="op",
        correlation_id="c1",
    )
    ks_id = activated.kill_switch_id
    # Inyecta el fallo en la ESCRITURA DE LA OUTBOX sin tocar el codigo de
    # produccion: pre-siembra una fila con el idempotency_key EXACTO que
    # generara el deactivate, para que su INSERT en outbox choque (UNIQUE) y la
    # transaccion entera (incluida la desactivacion) se deshaga.
    with migrator_db.transaction() as session:
        session.execute(
            "INSERT INTO outbox (event_id, idempotency_key, stream_key, "
            "event_type, envelope) VALUES (%s, %s, 's', "
            "'policy.kill_switch_deactivated', '{}'::jsonb)",
            (str(uuid4()), f"policy.kill_switch_deactivated:{ks_id}"),
        )
    with pytest.raises(Exception, match="duplicate key"):
        admin.deactivate_kill_switch(
            UUID(ks_id), reason_code="stop", actor="op", correlation_id="c2"
        )
    # El kill switch sigue ACTIVO: o las tres filas, o ninguna.
    with app_db.transaction() as session:
        rows = session.fetchall(
            "SELECT active FROM kill_switch WHERE kill_switch_id = %s", (ks_id,)
        )
    assert rows == [(True,)]
