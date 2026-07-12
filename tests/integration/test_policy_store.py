"""Tests de integracion del PostgresPolicyStore (P06 B4b), contra PostgreSQL.

DATOS FALSOS SIEMPRE: jurisdicciones inventadas ('AA'/'BB'), planes 'plan_x';
NUNCA jurisdicciones o planes reales (el catalogo comercial es de Alvaro y no se
siembra aqui). policy_version/policy_rule se siembran con el rol de MIGRACIONES
(superusuario en la base de juguete): el rol de aplicacion no puede escribirlas,
y eso mismo se demuestra. El driver solo lo conoce el adapter (REST-15): aqui se
captura Exception.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest

from ce_v5.core.clock import SystemClock
from ce_v5.core.policy import (
    Decision,
    EvidenceSource,
    KycStatus,
    PolicyEvaluator,
    PolicyInputs,
    ReasonCode,
    ResolvedJurisdiction,
)
from ce_v5.infra.db.policy_store import PolicyDataError, PostgresPolicyStore
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.infra.db.tenancy import provision_tenant_for_user

_DSN = os.environ.get("CE_V5_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    _DSN is None, reason="requiere CE_V5_DATABASE_URL (PostgreSQL local)"
)


def _wipe(db: PsycopgDatabase) -> None:
    with db.transaction() as session:
        session.execute("DELETE FROM policy_rule")
        session.execute("DELETE FROM policy_override")
        session.execute("DELETE FROM policy_entitlement")
        session.execute("DELETE FROM operator_audit")
        session.execute("DELETE FROM kill_switch")
        session.execute("DELETE FROM policy_version")


@pytest.fixture(autouse=True)
def _clean_policy_tables(migrator_db: PsycopgDatabase) -> Iterator[None]:
    # El rol de migraciones (superusuario) esquiva RLS y grants: limpia el
    # estado de politica antes y despues de cada test para aislarlos.
    _wipe(migrator_db)
    yield
    _wipe(migrator_db)


def _seed_version(db: PsycopgDatabase, version: str, status: str = "current") -> None:
    with db.transaction() as session:
        session.execute(
            "INSERT INTO policy_version (policy_version, status, actor) "
            "VALUES (%s, %s, 'seed')",
            (version, status),
        )


def _seed_rule(
    db: PsycopgDatabase,
    version: str,
    capability_id: str,
    effect: str,
    reason_code: str,
) -> None:
    with db.transaction() as session:
        session.execute(
            "INSERT INTO policy_rule (rule_id, policy_version, capability_id, "
            "effect, reason_code) VALUES (%s, %s, %s, %s, %s)",
            (str(uuid4()), version, capability_id, effect, reason_code),
        )


def _seed_entitlement(
    db: PsycopgDatabase, tenant_id: UUID, user_id: UUID | None, capability_id: str
) -> None:
    with db.transaction() as session:
        session.execute(
            "INSERT INTO policy_entitlement (entitlement_id, tenant_id, user_id, "
            "capability_id, source) VALUES (%s, %s, %s, %s, 'plan')",
            (str(uuid4()), str(tenant_id), _uuid_or_none(user_id), capability_id),
        )


def _seed_override(
    db: PsycopgDatabase,
    tenant_id: UUID,
    user_id: UUID | None,
    capability_id: str,
    effect: str,
    reason_code: str,
) -> None:
    with db.transaction() as session:
        session.execute(
            "INSERT INTO policy_override (override_id, tenant_id, user_id, "
            "capability_id, effect, reason_code, actor) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'seed')",
            (
                str(uuid4()),
                str(tenant_id),
                _uuid_or_none(user_id),
                capability_id,
                effect,
                reason_code,
            ),
        )


def _uuid_or_none(value: UUID | None) -> str | None:
    return None if value is None else str(value)


def _inputs(tenant_id: UUID, user_id: UUID) -> PolicyInputs:
    return PolicyInputs(
        subject_tenant_id=str(tenant_id),
        subject_user_id=str(user_id),
        jurisdiction=ResolvedJurisdiction(
            jurisdiction="AA", source=EvidenceSource.KYC, conflicting=False
        ),
        kyc_status=KycStatus.VERIFIED,
        vpn_detected=False,
        plan="plan_x",
        role=None,
    )


def test_app_no_puede_escribir_el_catalogo(app_db: PsycopgDatabase) -> None:
    with pytest.raises(Exception, match="permission denied"):
        with app_db.transaction() as session:
            session.execute(
                "INSERT INTO policy_version (policy_version, status, actor) "
                "VALUES (%s, 'draft', 'app')",
                ("pv_intruso",),
            )
    with pytest.raises(Exception, match="permission denied"):
        with app_db.transaction() as session:
            session.execute(
                "INSERT INTO policy_rule (rule_id, policy_version, capability_id, "
                "effect, reason_code) VALUES (%s, %s, %s, 'allow', %s)",
                (str(uuid4()), "pv_intruso", "view_dashboard", "allowed_by_policy"),
            )


def test_current_policy_version(
    app_db: PsycopgDatabase, migrator_db: PsycopgDatabase
) -> None:
    store = PostgresPolicyStore(app_db)
    assert store.current_policy_version() is None
    _seed_version(migrator_db, "pv1", status="current")
    assert store.current_policy_version() == "pv1"


def test_rules_solo_de_la_version_pedida(
    app_db: PsycopgDatabase, migrator_db: PsycopgDatabase
) -> None:
    _seed_version(migrator_db, "pv1", status="current")
    _seed_version(migrator_db, "pv2", status="superseded")
    _seed_rule(migrator_db, "pv1", "cap_a", "allow", "allowed_by_policy")
    _seed_rule(migrator_db, "pv2", "cap_b", "allow", "allowed_by_policy")
    store = PostgresPolicyStore(app_db)
    assert {rule.capability_id for rule in store.rules("pv1")} == {"cap_a"}


def test_entitlements_del_sujeto_y_aislamiento(
    app_db: PsycopgDatabase, migrator_db: PsycopgDatabase
) -> None:
    _seed_version(migrator_db, "pv1", status="current")
    user_a = uuid4()
    tenant_a = provision_tenant_for_user(app_db, user_a)
    user_b = uuid4()
    tenant_b = provision_tenant_for_user(app_db, user_b)
    _seed_entitlement(migrator_db, tenant_a, None, "cap_tenant")
    _seed_entitlement(migrator_db, tenant_a, user_a, "cap_user")
    _seed_entitlement(migrator_db, tenant_b, user_b, "cap_de_b")

    store = PostgresPolicyStore(app_db)
    caps = {e.capability_id for e in store.entitlements(str(tenant_a), str(user_a))}
    assert caps == {"cap_tenant", "cap_user"}


def test_overrides_del_sujeto_y_aislamiento(
    app_db: PsycopgDatabase, migrator_db: PsycopgDatabase
) -> None:
    _seed_version(migrator_db, "pv1", status="current")
    user_a = uuid4()
    tenant_a = provision_tenant_for_user(app_db, user_a)
    user_b = uuid4()
    tenant_b = provision_tenant_for_user(app_db, user_b)
    _seed_override(migrator_db, tenant_a, None, "cap_tenant", "deny", "denied_by_plan")
    _seed_override(
        migrator_db, tenant_a, user_a, "cap_user", "allow", "allowed_by_override"
    )
    _seed_override(migrator_db, tenant_b, user_b, "cap_de_b", "deny", "denied_by_plan")

    store = PostgresPolicyStore(app_db)
    caps = {o.capability_id for o in store.overrides(str(tenant_a), str(user_a))}
    assert caps == {"cap_tenant", "cap_user"}


def test_active_kill_switches_ve_y_deja_de_ver(
    app_db: PsycopgDatabase, operator_db: PsycopgDatabase
) -> None:
    store = PostgresPolicyStore(app_db)
    assert store.active_kill_switches() == []

    ks_id = uuid4()
    with operator_db.transaction() as session:
        session.execute(
            "INSERT INTO kill_switch (kill_switch_id, scope, reason_code, actor) "
            "VALUES (%s, 'global', 'manual', 'operador')",
            (str(ks_id),),
        )
    activos = store.active_kill_switches()
    assert [k.kill_switch_id for k in activos] == [str(ks_id)]
    assert activos[0].scope == "global"

    with operator_db.transaction() as session:
        session.execute(
            "UPDATE kill_switch SET active = false, deactivated_at = now() "
            "WHERE kill_switch_id = %s",
            (str(ks_id),),
        )
    assert store.active_kill_switches() == []


def test_policy_data_error_reason_code_fuera_de_catalogo(
    app_db: PsycopgDatabase, migrator_db: PsycopgDatabase
) -> None:
    _seed_version(migrator_db, "pv1", status="current")
    with migrator_db.transaction() as session:
        session.execute(
            "INSERT INTO policy_rule (rule_id, policy_version, capability_id, "
            "effect, reason_code) VALUES (%s, %s, %s, 'allow', %s)",
            (str(uuid4()), "pv1", "cap_x", "motivo_inventado"),
        )
    store = PostgresPolicyStore(app_db)
    with pytest.raises(PolicyDataError, match="motivo_inventado"):
        store.rules("pv1")


def test_end_to_end_allow_no_sensible_deny_sensible_sin_entitlement(
    app_db: PsycopgDatabase, migrator_db: PsycopgDatabase
) -> None:
    _seed_version(migrator_db, "pv1", status="current")
    _seed_rule(migrator_db, "pv1", "view_dashboard", "allow", "allowed_by_policy")
    _seed_rule(migrator_db, "pv1", "execute_order", "allow", "allowed_by_policy")
    user = uuid4()
    tenant = provision_tenant_for_user(app_db, user)

    evaluator = PolicyEvaluator(PostgresPolicyStore(app_db), SystemClock())
    result = evaluator.evaluate(
        _inputs(tenant, user), ["view_dashboard", "execute_order"]
    )
    assert result.decisions["view_dashboard"].decision is Decision.ALLOW
    sensible = result.decisions["execute_order"]
    assert sensible.decision is Decision.DENY
    assert sensible.reason_code is ReasonCode.DENIED_BY_MISSING_ENTITLEMENT
