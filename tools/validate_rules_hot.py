"""Validacion en caliente de P08: aislamiento del motor de reglas (CA-P08-03).

Cierra las pruebas de COMPORTAMIENTO de CA-P08-03 (7, 9, 10, 11, 12, 16) por el CAMINO
DEL CODIGO (nunca SQL de identidad/scope a mano) contra el PostgreSQL local, con
role-switching REAL: el rol de MIGRACIONES prepara y limpia; el rol de APLICACION
escribe la autoria (bajo sesion user-driven); el rol de REGLAS descubre por la
ventanilla y escribe el estado (bajo sesion system-driven). Ademas demuestra el
POSITIVO de la primitiva atomica record_transition y su ROLLBACK (cero rastro si falla).

Sandbox/local, NUNCA datos reales: tenants y reglas FALSOS, dados de alta en cada
ejecucion y borrados al final (sin residuo).

Uso (los tres DSN en linea; el de reglas SOLO para esta corrida):
    CE_V5_RULES_DATABASE_URL=... python tools/validate_rules_hot.py
Exige CE_V5_DATABASE_URL (app), CE_V5_RULES_DATABASE_URL (reglas) y
CE_V5_MIGRATIONS_DATABASE_URL (migraciones, para preparar y limpiar).

Las guardias de arranque de DbConfig.from_env son para procesos PERMANENTES: prohiben
que un runtime porte un DSN ajeno. Este arnes de validacion es role-switching legitimo,
asi que lee cada DSN por su variable directamente (no es un proceso de runtime).
"""

from __future__ import annotations

import os
from uuid import UUID, uuid4

from ce_v5.infra.db.config import (
    DSN_ENV_VAR,
    MIGRATIONS_DSN_ENV_VAR,
    RULES_DSN_ENV_VAR,
    DbConfig,
    DbConfigError,
)
from ce_v5.infra.db.identity import register_user
from ce_v5.infra.db.outbox import OutboxEvent
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.infra.db.rules import (
    discover_rules,
    insert_rule_definition,
    read_state,
    record_transition,
)
from ce_v5.infra.db.tenancy import (
    SystemScopedDatabase,
    TenantScopedDatabase,
    TenantScopedSession,
    provision_tenant_for_user,
)
from ce_v5.platform.rules.canonical import canonical_rule_hash
from source.rules.condition import Condition
from source.rules.feature import Feature
from source.rules.group import Group
from source.rules.market_rules import AlertRule, AnyRule, MarketScope, RuleProduct
from source.rules.reference import DataSourceRef
from source.rules.rule import BindingKind, TargetBinding
from source.rules.scalar import ScalarType, ScalarValue
from source.rules.term import SourceTerm, Term, TermKind
from source.rules.vocab import (
    CombineMode,
    ComparisonOperator,
    RuleCombineMode,
    TriggerPolicy,
)

_PASSWORD_HASH = "hash-de-prueba-no-es-argon2"
_STATE_INSERT_SQL = (
    "INSERT INTO rule_lifecycle_state (rule_id, tenant_id, state) VALUES (%s, %s, %s)"
)
_OUTBOX_COUNT_SQL = "SELECT count(*) FROM outbox WHERE event_id = %s"


def _dsn(var: str) -> DbConfig:
    value = os.environ.get(var, "").strip()
    if not value:
        raise DbConfigError(f"Falta {var} para la validacion en caliente de reglas.")
    return DbConfig(dsn=value)


def _fake_email() -> str:
    return f"fake-{uuid4().hex}@ejemplo.test"


def _condition() -> Condition:
    return Condition(
        node_id=uuid4(),
        left=Term(
            term_kind=TermKind.SOURCE,
            source=SourceTerm(ref=DataSourceRef(source_id="market.close")),
        ),
        operator=ComparisonOperator.GT,
        right=Term(
            term_kind=TermKind.CONSTANT,
            constant=ScalarValue(scalar_type=ScalarType.DECIMAL, decimal_value="30000"),
        ),
    )


def _mkrule(
    rule_id: UUID, tenant_id: UUID, symbol: str, context: str, *, enabled: bool
) -> AnyRule:
    """Construye un AlertRule minimo. tenant_id es el que ira en el JSON (puede ser
    FALSO): la COLUMNA la fija el contexto de la sesion, no esto."""
    group = Group(
        node_id=uuid4(),
        evaluation_context=context,
        combine_mode=CombineMode.ALL,
        features=(
            Feature(
                node_id=uuid4(),
                conditions=(_condition(),),
                combine_mode=CombineMode.ALL,
            ),
        ),
    )
    return AlertRule(
        product=RuleProduct.ALERT,
        rule_id=rule_id,
        tenant_id=tenant_id,
        name="regla-de-prueba",
        target_binding=TargetBinding(binding_kind=BindingKind.MARKET),
        trigger_policy=TriggerPolicy.CANDLE_CLOSE,
        groups=(group,),
        rule_combine_mode=RuleCombineMode.ALL,
        enabled=enabled,
        market_scope=MarketScope(exchange="binance", symbol=symbol),
    )


def _author(scoped: TenantScopedSession, rule: AnyRule) -> None:
    """Autora una regla: computa el hash canonico (Bloque 1, platform) y lo pasa al
    repo de infra, que no importa platform (fronteras de capa, check 7.1)."""
    insert_rule_definition(scoped, rule, canonical_rule_hash(rule))


def _event(rule_id: UUID, event_type: str) -> OutboxEvent:
    return OutboxEvent(
        event_id=uuid4(),
        idempotency_key=f"{event_type}:{rule_id}:{uuid4().hex}",
        stream_key=f"rule:{rule_id}",
        event_type=event_type,
        envelope={"rule_id": str(rule_id)},
    )


def main() -> None:  # noqa: PLR0912, PLR0915
    failures: list[str] = []
    created_event_ids: list[UUID] = []
    tenant_a: UUID | None = None
    tenant_b: UUID | None = None
    user_a: UUID | None = None
    user_b: UUID | None = None

    app_db = PsycopgDatabase(_dsn(DSN_ENV_VAR))
    rules_db = PsycopgDatabase(_dsn(RULES_DSN_ENV_VAR))
    system_db = SystemScopedDatabase(rules_db)
    scoped_app = TenantScopedDatabase(app_db)
    try:
        # PREP: usuarios y tenants reales (FK de la 0010/0013).
        user_a = register_user(app_db, _fake_email(), _PASSWORD_HASH)
        user_b = register_user(app_db, _fake_email(), _PASSWORD_HASH)
        tenant_a = provision_tenant_for_user(app_db, user_a)
        tenant_b = provision_tenant_for_user(app_db, user_b)
        print(f"[prep] tenant A={tenant_a} tenant B={tenant_b}")

        # AUTORIA (rol de app, sesion user-driven). r_a9 lleva un tenant_id FALSO en su
        # JSON para la prueba 9; la COLUMNA la fija el contexto (A).
        r_a1, r_a2, r_a3, r_b1 = uuid4(), uuid4(), uuid4(), uuid4()
        r_a9, r_b2 = uuid4(), uuid4()
        fake_tenant = uuid4()
        with scoped_app.transaction(user_a) as s:
            _author(s, _mkrule(r_a1, tenant_a, "BTC-USDT", "1h", enabled=True))
            _author(s, _mkrule(r_a2, tenant_a, "BTC-USDT", "4h", enabled=True))
            _author(s, _mkrule(r_a3, tenant_a, "BTC-USDT", "1h", enabled=False))
            _author(s, _mkrule(r_a9, fake_tenant, "ETH-USDT", "1h", enabled=True))
        with scoped_app.transaction(user_b) as s:
            _author(s, _mkrule(r_b1, tenant_b, "BTC-USDT", "1h", enabled=True))
            _author(s, _mkrule(r_b2, tenant_b, "BTC-USDT", "1h", enabled=True))

        # (7) DESCUBRIMIENTO CROSS-TENANT del mercado+timeframe exacto: solo activas.
        with rules_db.transaction() as s:
            discovered = discover_rules(s, "binance", "BTC-USDT", "1h")
        found = {d.rule_id for d in discovered}
        tenants = {d.tenant_id for d in discovered}
        ok7 = (
            r_a1 in found
            and r_b1 in found
            and r_a2 not in found  # otro timeframe
            and r_a3 not in found  # deshabilitada
            and r_a9 not in found  # otro mercado
            and {tenant_a, tenant_b} <= tenants  # cross-tenant real (A y B)
        )
        print(
            f"[7] DESCUBRIMIENTO -> A1&B1 presentes, A2(4h)/A3(off)/A9(ETH) excluidas, "
            f"tenants{{A,B}}: {ok7}"
        )
        if not ok7:
            failures.append(
                "(7) descubrimiento no filtro por mercado/timeframe/enabled"
            )

        # (9) La COLUMNA manda sobre el JSON para identidad/scope.
        with rules_db.transaction() as s:
            eth = discover_rules(s, "binance", "ETH-USDT", "1h")
        a9 = next((d for d in eth if d.rule_id == r_a9), None)
        json_tenant = a9.definition.get("tenant_id") if a9 is not None else None
        ok9 = (
            a9 is not None
            and a9.tenant_id == tenant_a  # columna = tenant real
            and a9.tenant_id != fake_tenant  # no el del JSON
            and json_tenant == str(fake_tenant)  # el JSON si tiene el falso, se ignora
        )
        print(
            f"[9] COLUMNA>JSON -> columna={a9.tenant_id if a9 else None} (esperado A), "
            f"json={json_tenant} (falso, ignorado): {ok9}"
        )
        if not ok9:
            failures.append("(9) el scope no uso la columna e ignoro el JSON")

        # (10) SIN TENANT FIJADO, escribir estado FALLA (fail-closed).
        lanzo10, msg10 = False, ""
        try:
            with rules_db.transaction() as s:
                s.execute(_STATE_INSERT_SQL, (str(r_a1), str(tenant_a), "pending"))
        except Exception as exc:  # noqa: BLE001
            lanzo10, msg10 = True, str(exc)
        ok10 = lanzo10 and "row-level security" in msg10.lower()
        print(f"[10] SIN TENANT -> escribir estado lanza RLS: {ok10}")
        if not ok10:
            failures.append("(10) sin tenant fijado se pudo escribir estado")

        # (11) Con A fijado, escribir estado con tenant_id=B FALLA (WITH CHECK).
        lanzo11, msg11 = False, ""
        try:
            with system_db.transaction(tenant_a) as s:
                s.session.execute(
                    _STATE_INSERT_SQL, (str(r_b1), str(tenant_b), "pending")
                )
        except Exception as exc:  # noqa: BLE001
            lanzo11, msg11 = True, str(exc)
        ok11 = lanzo11 and "row-level security" in msg11.lower()
        print(f"[11] A escribe estado de B -> WITH CHECK lo rechaza: {ok11}")
        if not ok11:
            failures.append("(11) A pudo escribir una fila de estado con tenant B")

        # (12) Con A fijado, leer estado de B da CERO filas (primero se crea el de B).
        ev_b = _event(r_b1, "rule.firing")
        record_transition(
            system_db,
            tenant_id=tenant_b,
            rule_id=r_b1,
            new_state="firing",
            last_evaluated_open_time=1000,
            event=ev_b,
        )
        created_event_ids.append(ev_b.event_id)
        with system_db.transaction(tenant_a) as s:
            from_a = read_state(s.session, r_b1)
        with system_db.transaction(tenant_b) as s:
            from_b = read_state(s.session, r_b1)
        ok12 = from_a is None and from_b is not None and from_b.state == "firing"
        print(
            f"[12] A lee estado de B -> {0 if from_a is None else 1} filas (esp. 0); "
            f"bajo B existe: {from_b is not None}: {ok12}"
        )
        if not ok12:
            failures.append("(12) A pudo leer el estado de B")

        # (16) Una transaccion scopeada a A no cruza a B: escribir estado de B dentro de
        # ella FALLA y hace rollback (cero rastro). record_transition abre EXACTAMENTE
        # esta transaccion system-driven, asi que nunca puede cruzar tenants.
        lanzo16, msg16 = False, ""
        try:
            with system_db.transaction(tenant_a) as s:
                s.session.execute(
                    _STATE_INSERT_SQL, (str(r_b2), str(tenant_b), "firing")
                )
        except Exception as exc:  # noqa: BLE001
            lanzo16, msg16 = True, str(exc)
        with system_db.transaction(tenant_b) as s:
            trace = read_state(s.session, r_b2)
        ok16 = lanzo16 and "row-level security" in msg16.lower() and trace is None
        print(f"[16] tx(A) no cruza a B -> RLS rechaza, sin rastro ({trace}): {ok16}")
        if not ok16:
            failures.append("(16) una transaccion scopeada a A pudo tocar a B")

        # POSITIVO: record_transition atomico -> estado firing + evento rule.firing.
        ev_pos = _event(r_a1, "rule.firing")
        record_transition(
            system_db,
            tenant_id=tenant_a,
            rule_id=r_a1,
            new_state="firing",
            last_evaluated_open_time=2000,
            event=ev_pos,
        )
        created_event_ids.append(ev_pos.event_id)
        with system_db.transaction(tenant_a) as s:
            st = read_state(s.session, r_a1)
        with rules_db.transaction() as s:
            row = s.fetchone(_OUTBOX_COUNT_SQL, (str(ev_pos.event_id),))
        ev_count = row[0] if row is not None else 0
        ok_pos = (
            st is not None
            and st.state == "firing"
            and st.last_evaluated_open_time == 2000
            and ev_count == 1
        )
        print(
            f"[+] POSITIVO atomico -> estado={st.state if st else None} y evento en "
            f"outbox (count={ev_count}): {ok_pos}"
        )
        if not ok_pos:
            failures.append("(+) record_transition no dejo estado + evento juntos")

        # ROLLBACK: un evento de familia PROHIBIDA (execution.*) rompe el commit; ni el
        # estado ni el evento quedan (atomicidad). r_a2 no tenia estado previo.
        ev_bad = _event(r_a2, "execution.forbidden")
        lanzo_rb = False
        try:
            record_transition(
                system_db,
                tenant_id=tenant_a,
                rule_id=r_a2,
                new_state="firing",
                last_evaluated_open_time=3000,
                event=ev_bad,
            )
        except Exception:  # noqa: BLE001
            lanzo_rb = True
        with system_db.transaction(tenant_a) as s:
            st_bad = read_state(s.session, r_a2)
        with rules_db.transaction() as s:
            row_bad = s.fetchone(_OUTBOX_COUNT_SQL, (str(ev_bad.event_id),))
        bad_count = row_bad[0] if row_bad is not None else 0
        ok_rb = lanzo_rb and st_bad is None and bad_count == 0
        print(
            f"[+] ROLLBACK -> evento prohibido rechazado, sin estado "
            f"(None={st_bad is None}) y sin evento (count={bad_count}): {ok_rb}"
        )
        if not ok_rb:
            failures.append("(+) el intento fallido dejo rastro (no fue atomico)")
    finally:
        # LIMPIEZA con el rol de migraciones (superusuario: bypass RLS). Borrar el
        # tenant cascada a rule_definition -> rule_lifecycle_state y a la pertenencia.
        mig_db = PsycopgDatabase(_dsn(MIGRATIONS_DSN_ENV_VAR))
        try:
            with mig_db.transaction() as s:
                for event_id in created_event_ids:
                    s.execute(
                        "DELETE FROM outbox WHERE event_id = %s", (str(event_id),)
                    )
                for tenant in (tenant_a, tenant_b):
                    if tenant is not None:
                        s.execute(
                            "DELETE FROM tenant WHERE tenant_id = %s", (str(tenant),)
                        )
                for user in (user_a, user_b):
                    if user is not None:
                        s.execute(
                            "DELETE FROM app_user WHERE user_id = %s", (str(user),)
                        )
        finally:
            mig_db.close()
        app_db.close()
        rules_db.close()

    total = 6 + 2
    if failures:
        print(f"RESUMEN: FALLO - {len(failures)} de {total} no se cumplieron:")
        for reason in failures:
            print(f"  - {reason}")
        raise SystemExit(1)
    print(
        "RESUMEN: OK - aislamiento del motor demostrado: 7/9/10/11/12/16 + positivo y "
        f"rollback atomicos ({total}/{total})."
    )


if __name__ == "__main__":
    main()
