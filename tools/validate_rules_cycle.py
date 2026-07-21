"""Validacion en caliente del ciclo de una regla: T1-T5 + proyeccion (CA-P08-01/D6).

Con role-switching REAL (migraciones prepara/limpia; app autora; ce_v5_rules procesa el
ciclo bajo sesion system-driven), demuestra el orquestador process_rule_cycle contra el
PostgreSQL local: entrar en FIRING emite evaluation_completed + firing + la proyeccion
por producto (alert.raised/signal.raised) con causation = event_id(firing); reprocesar
la misma vela no reemite (dedup); pasar a FALSE resuelve sin proyectar; el veto activo
suprime la proyeccion; y todo va atomico (estado + N eventos en el mismo commit).

Sandbox/local, NUNCA datos reales: tenant y reglas FALSOS, borrados al final.

Uso:
    CE_V5_RULES_DATABASE_URL=... python tools/validate_rules_cycle.py
Exige CE_V5_DATABASE_URL (app), CE_V5_RULES_DATABASE_URL (reglas) y
CE_V5_MIGRATIONS_DATABASE_URL (migraciones).
"""

from __future__ import annotations

import os
from decimal import Decimal
from uuid import UUID, uuid4

from ce_v5.entrypoints.worker_rules.cycle import process_rule_cycle
from ce_v5.infra.db.config import (
    DSN_ENV_VAR,
    MIGRATIONS_DSN_ENV_VAR,
    RULES_DSN_ENV_VAR,
    DbConfig,
    DbConfigError,
)
from ce_v5.infra.db.identity import register_user
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.infra.db.rules import insert_rule_definition
from ce_v5.infra.db.tenancy import (
    SystemScopedDatabase,
    TenantScopedDatabase,
    TenantScopedSession,
    provision_tenant_for_user,
)
from ce_v5.platform.rules.canonical import canonical_rule_hash
from ce_v5.platform.rules.catalog import DataSourceCatalog
from ce_v5.platform.rules.compiler import ExecutionPlan, compile
from ce_v5.platform.rules.rawclose import (
    MARKET_CLOSE_SOURCE_ID,
    market_close_declaration,
)
from ce_v5.platform.rules.runtime import RuntimeState
from source.families.rule import EvaluationLifecycleState
from source.rules.condition import Condition
from source.rules.feature import Feature
from source.rules.group import Group
from source.rules.market_rules import (
    AlertRule,
    AnyRule,
    MarketScope,
    RuleProduct,
    TradingSignalRule,
)
from source.rules.reference import DataSourceRef
from source.rules.rule import BindingKind, TargetBinding
from source.rules.scalar import ScalarType, ScalarValue
from source.rules.term import SourceTerm, Term, TermKind
from source.rules.veto import Veto
from source.rules.vocab import (
    CombineMode,
    ComparisonOperator,
    RuleCombineMode,
    TriggerPolicy,
    VetoMode,
)

_PASSWORD_HASH = "hash-de-prueba-no-es-argon2"
_OUTBOX_BY_STREAM = (
    "SELECT event_type, event_id::text, envelope FROM outbox "
    "WHERE stream_key = %s ORDER BY event_type"
)
FIRE = {MARKET_CLOSE_SOURCE_ID: (Decimal("40000"),)}  # close 40000 > 30000 -> TRUE
FALL = {MARKET_CLOSE_SOURCE_ID: (Decimal("20000"),)}  # close 20000 < 30000 -> FALSE


def _dsn(var: str) -> DbConfig:
    value = os.environ.get(var, "").strip()
    if not value:
        raise DbConfigError(f"Falta {var} para la validacion del ciclo de reglas.")
    return DbConfig(dsn=value)


def _fake_email() -> str:
    return f"fake-{uuid4().hex}@ejemplo.test"


def _gt(threshold: str) -> Condition:
    return Condition(
        node_id=uuid4(),
        left=Term(
            term_kind=TermKind.SOURCE,
            source=SourceTerm(ref=DataSourceRef(source_id=MARKET_CLOSE_SOURCE_ID)),
        ),
        operator=ComparisonOperator.GT,
        right=Term(
            term_kind=TermKind.CONSTANT,
            constant=ScalarValue(
                scalar_type=ScalarType.DECIMAL, decimal_value=threshold
            ),
        ),
    )


def _mkrule(tenant_id: UUID, product: RuleProduct, *, with_veto: bool) -> AnyRule:
    group = Group(
        node_id=uuid4(),
        evaluation_context="1h",
        combine_mode=CombineMode.ALL,
        features=(
            Feature(
                node_id=uuid4(),
                conditions=(_gt("30000"),),
                combine_mode=CombineMode.ALL,
            ),
        ),
    )
    veto = (
        Veto(node_id=uuid4(), conditions=(_gt("0"),), veto_mode=VetoMode.ANY_BLOCKS)
        if with_veto
        else None
    )
    rule_id = uuid4()
    binding = TargetBinding(binding_kind=BindingKind.MARKET)
    scope = MarketScope(exchange="binance", symbol="BTC-USDT")
    if product is RuleProduct.ALERT:
        return AlertRule(
            product=RuleProduct.ALERT,
            rule_id=rule_id,
            tenant_id=tenant_id,
            name="regla-de-ciclo",
            target_binding=binding,
            trigger_policy=TriggerPolicy.CANDLE_CLOSE,
            groups=(group,),
            veto=veto,
            rule_combine_mode=RuleCombineMode.ALL,
            enabled=True,
            market_scope=scope,
        )
    return TradingSignalRule(
        product=RuleProduct.TRADING_SIGNAL,
        rule_id=rule_id,
        tenant_id=tenant_id,
        name="regla-de-ciclo",
        target_binding=binding,
        trigger_policy=TriggerPolicy.CANDLE_CLOSE,
        groups=(group,),
        veto=veto,
        rule_combine_mode=RuleCombineMode.ALL,
        enabled=True,
        market_scope=scope,
    )


def _author(scoped: TenantScopedSession, rule: AnyRule) -> None:
    insert_rule_definition(scoped, rule, canonical_rule_hash(rule))


def main() -> None:  # noqa: PLR0912, PLR0915
    failures: list[str] = []
    tenant: UUID | None = None
    user: UUID | None = None

    app_db = PsycopgDatabase(_dsn(DSN_ENV_VAR))
    rules_db = PsycopgDatabase(_dsn(RULES_DSN_ENV_VAR))
    mig_db = PsycopgDatabase(_dsn(MIGRATIONS_DSN_ENV_VAR))
    system_db = SystemScopedDatabase(rules_db)
    scoped_app = TenantScopedDatabase(app_db)

    catalog = DataSourceCatalog()
    catalog.register(market_close_declaration())
    catalog.validate()

    def events_for(rule_id: UUID) -> list[tuple[str, str, dict[str, object]]]:
        with mig_db.transaction() as sess:
            rows = sess.fetchall(_OUTBOX_BY_STREAM, (f"rule:{rule_id}",))
        return [
            (str(r[0]), str(r[1]), r[2] if isinstance(r[2], dict) else {}) for r in rows
        ]

    def check(label: str, ok: bool) -> None:
        print(f"  [{'OK' if ok else 'FAIL'}] {label}")
        if not ok:
            failures.append(label)

    rule_ids: list[UUID] = []
    try:
        user = register_user(app_db, _fake_email(), _PASSWORD_HASH)
        tenant = provision_tenant_for_user(app_db, user)
        print(f"[prep] tenant={tenant}")

        alert = _mkrule(tenant, RuleProduct.ALERT, with_veto=False)
        signal = _mkrule(tenant, RuleProduct.TRADING_SIGNAL, with_veto=False)
        vetoed = _mkrule(tenant, RuleProduct.ALERT, with_veto=True)
        atom = _mkrule(tenant, RuleProduct.ALERT, with_veto=False)
        rules = [alert, signal, vetoed, atom]
        rule_ids = [r.rule_id for r in rules]
        with scoped_app.transaction(user) as s:
            for r in rules:
                _author(s, r)

        plans: dict[UUID, ExecutionPlan] = {
            r.rule_id: compile(r, catalog) for r in rules
        }
        inactive = RuntimeState(EvaluationLifecycleState.INACTIVE)

        # ---- T1/T2: entrar en FIRING (AlertRule) ----
        print("== T1/T2: FIRING emite evaluation_completed + firing + alert.raised ==")
        st_alert = process_rule_cycle(
            system_db,
            alert,
            plans[alert.rule_id],
            FIRE,
            inactive,
            1000,
            tenant_id=tenant,
            rule_id=alert.rule_id,
        )
        evs = events_for(alert.rule_id)
        types = [t for t, _, _ in evs]
        check(
            "T1: outbox lleva rule.evaluation_completed",
            "rule.evaluation_completed" in types,
        )
        check("T1: outbox lleva rule.firing", "rule.firing" in types)
        check(
            "estado -> FIRING", st_alert.eval_state is EvaluationLifecycleState.FIRING
        )
        firing_id = next((eid for t, eid, _ in evs if t == "rule.firing"), None)
        raised = next((env for t, _, env in evs if t == "alert.raised"), None)
        check("T2: alert.raised presente", raised is not None)
        check(
            "T2: alert.raised.causation_id == event_id(rule.firing)",
            raised is not None and raised.get("causation_id") == firing_id,
        )

        # ---- T3: dedup (reprocesar la MISMA vela ya en FIRING) ----
        print("== T3: dedup -> reprocesar misma vela no reemite ==")
        before = len(evs)
        st_dup = process_rule_cycle(
            system_db,
            alert,
            plans[alert.rule_id],
            FIRE,
            st_alert,
            1000,
            tenant_id=tenant,
            rule_id=alert.rule_id,
        )
        after = len(events_for(alert.rule_id))
        check("T3: cero eventos nuevos", after == before)
        check(
            "T3: sigue FIRING sin segundo firing",
            st_dup.eval_state is EvaluationLifecycleState.FIRING,
        )

        # ---- T4: pasar a FALSE -> RESOLVED sin proyeccion ----
        print(
            "== T4: FALSE -> evaluation_completed + resolved(condition_false), "
            "sin raised =="
        )
        st_res = process_rule_cycle(
            system_db,
            alert,
            plans[alert.rule_id],
            FALL,
            st_dup,
            2000,
            tenant_id=tenant,
            rule_id=alert.rule_id,
        )
        evs4 = events_for(alert.rule_id)
        resolved = next((env for t, _, env in evs4 if t == "rule.resolved"), None)
        check(
            "estado -> RESOLVED", st_res.eval_state is EvaluationLifecycleState.RESOLVED
        )
        check("T4: rule.resolved presente", resolved is not None)
        check(
            "T4: resolved_reason=condition_false",
            resolved is not None
            and _payload_of(resolved).get("resolved_reason") == "condition_false",
        )
        raised_count = sum(1 for t, _, _ in evs4 if t == "alert.raised")
        check("T4: NINGUN alert.raised nuevo (resolved no proyecta)", raised_count == 1)

        # ---- T5: evaluation_completed solo no proyecta (via el resolved de T4) ----
        print("== T5: evaluation_completed sin firing NO proyecta ==")
        ec_in_resolve = any(
            t == "rule.evaluation_completed"
            for t, _, env in evs4
            if _payload_of(env).get("new_state") == "resolved"
        )
        check("T5: hubo evaluation_completed en el RESOLVED", ec_in_resolve)
        check("T5: y aun asi no aparecio proyeccion nueva", raised_count == 1)

        # ---- Proyeccion por producto: TradingSignalRule -> signal.raised ----
        print("== Proyeccion por producto: TradingSignalRule -> signal.raised ==")
        process_rule_cycle(
            system_db,
            signal,
            plans[signal.rule_id],
            FIRE,
            inactive,
            1000,
            tenant_id=tenant,
            rule_id=signal.rule_id,
        )
        sev = [t for t, _, _ in events_for(signal.rule_id)]
        check("signal.raised presente", "signal.raised" in sev)
        check("y NO alert.raised", "alert.raised" not in sev)

        # ---- Veto activo (V=TRUE) en el flanco: no proyecta nada ----
        print("== Veto activo (V=TRUE): matched pero sin firing ni proyeccion ==")
        st_veto = process_rule_cycle(
            system_db,
            vetoed,
            plans[vetoed.rule_id],
            FIRE,
            inactive,
            1000,
            tenant_id=tenant,
            rule_id=vetoed.rule_id,
        )
        vev = [t for t, _, _ in events_for(vetoed.rule_id)]
        check("veto: NO rule.firing", "rule.firing" not in vev)
        check("veto: NO alert.raised", "alert.raised" not in vev)
        check(
            "veto: estado NO paso a FIRING",
            st_veto.eval_state is not EvaluationLifecycleState.FIRING,
        )

        # ---- Atomicidad: un fallo (idempotency colisiona) -> cero rastro nuevo ----
        print(
            "== Atomicidad: reintento colisionante -> rollback total (cero rastro) =="
        )
        st_atom = process_rule_cycle(
            system_db,
            atom,
            plans[atom.rule_id],
            FIRE,
            inactive,
            5000,
            tenant_id=tenant,
            rule_id=atom.rule_id,
        )
        atom_before = len(events_for(atom.rule_id))
        lanzo = False
        try:  # mismo open_time + prev INACTIVE -> los MISMOS idempotency_key
            process_rule_cycle(
                system_db,
                atom,
                plans[atom.rule_id],
                FIRE,
                inactive,
                5000,
                tenant_id=tenant,
                rule_id=atom.rule_id,
            )
        except Exception:  # noqa: BLE001
            lanzo = True
        atom_after = len(events_for(atom.rule_id))
        check("atomicidad: el reintento colisionante fallo", lanzo)
        check("atomicidad: cero eventos nuevos (rollback)", atom_after == atom_before)
        check(
            "atomicidad: el firing inicial sigue intacto",
            st_atom.eval_state is EvaluationLifecycleState.FIRING,
        )
    finally:
        with mig_db.transaction() as m:
            for rid in rule_ids:
                m.execute("DELETE FROM outbox WHERE stream_key = %s", (f"rule:{rid}",))
            if tenant is not None:
                m.execute("DELETE FROM tenant WHERE tenant_id = %s", (str(tenant),))
            if user is not None:
                m.execute("DELETE FROM app_user WHERE user_id = %s", (str(user),))
        mig_db.close()
        app_db.close()
        rules_db.close()

    if failures:
        print(f"RESUMEN: FALLO - {len(failures)} comprobaciones:")
        for reason in failures:
            print(f"  - {reason}")
        raise SystemExit(1)
    print(
        "RESUMEN: OK - ciclo rule.firing->raised demostrado "
        "(T1-T5 + proyeccion + veto)."
    )


def _payload_of(envelope: dict[str, object]) -> dict[str, object]:
    payload = envelope.get("payload")
    return payload if isinstance(payload, dict) else {}


if __name__ == "__main__":
    main()
