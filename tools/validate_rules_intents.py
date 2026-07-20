"""Validacion en caliente 7.2: estado operacional acumulado + intents (D2, tests 11-21).

PARTE A -- el RuntimeState sobrevive entre ticks. Se procesan velas REALES por el
handler del worker y se comprueba que los contadores ACUMULAN a traves de ticks: M velas
NOT_EVALUABLE consecutivas -> is_stale con su motivo; N excepciones consecutivas ->
is_quarantined; y una evaluacion buena entre medias RESETEA el contador. Esto cierra
CA-P08-04 D2/D3 en el WORKER REAL, no solo en la funcion pura: con el read_state parcial
de la 7.1 los contadores volvian a cero en cada vela y esos umbrales eran
inalcanzables.

PARTE B -- SubscriptionIntent desde autoria (CA-P08-07 D2), tests 11-21: la regla y sus
intents se escriben y se retiran ATOMICAMENTE, el ciclo va por enabled y NO por salud
(la cuarentena NO apaga el stream), y el ref-count de P07 los cuenta sin cambio alguno.

Sandbox/local, NUNCA datos reales: tenants, usuarios, reglas y velas FALSOS, borrados al
final.

Uso:
    CE_V5_RULES_DATABASE_URL=... python tools/validate_rules_intents.py
Exige CE_V5_DATABASE_URL (app), CE_V5_RULES_DATABASE_URL (reglas) y
CE_V5_MIGRATIONS_DATABASE_URL (migraciones).
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from uuid import UUID, uuid4

from ce_v5.core.bus import BusMessage
from ce_v5.entrypoints.worker_rules.composition import build_catalog, build_handler
from ce_v5.infra.db.config import (
    DSN_ENV_VAR,
    MIGRATIONS_DSN_ENV_VAR,
    RULES_DSN_ENV_VAR,
    DbConfig,
    DbConfigError,
)
from ce_v5.infra.db.identity import register_user
from ce_v5.infra.db.market_store import PostgresIntentStore, PostgresPublicDemand
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.infra.db.rules import (
    RuleLifecycleState,
    create_rule_with_intents,
    delete_rule_with_intents,
    read_state,
    rule_stream_keys,
    set_rule_enabled,
)
from ce_v5.infra.db.tenancy import (
    SystemScopedDatabase,
    TenantScopedDatabase,
    TenantScopedSession,
    provision_tenant_for_user,
)
from ce_v5.platform.rules.canonical import canonical_rule_hash
from ce_v5.platform.rules.rawclose import MARKET_CLOSE_SOURCE_ID
from source.families.market import (
    MarketCandleEventType,
    MarketType,
    SubscriptionIntent,
    Timeframe,
)
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
from source.rules.vocab import (
    CombineMode,
    ComparisonOperator,
    RuleCombineMode,
    TriggerPolicy,
)
from source.time import MaturityState

_PASSWORD_HASH = "hash-de-prueba-no-es-argon2"
_EXCHANGE = "binance"
_SYMBOL = "BTC-USDT"
_TF_MS = 3_600_000
_NOW_MS = 1_700_000_000_000

# Umbrales de CA-P08-05 (defaults del runtime): 3 velas / 3 excepciones.
_STALE_THRESHOLD = 3
_QUARANTINE_THRESHOLD = 3

_INSERT_CANDLE = """
INSERT INTO market_candle (
    idempotency_key, stream_key, exchange, market_type, symbol, timeframe,
    open_time, close_time, open, high, low, close, volume, maturity_state
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'closed')
"""

_COUNT_RULE_SQL = "SELECT count(*) FROM rule_definition WHERE rule_id = %s"
_COUNT_INTENTS_SQL = (
    "SELECT count(*) FROM market_subscription_intent WHERE source_ref = %s"
)
_QUARANTINE_SQL = """
INSERT INTO rule_lifecycle_state (rule_id, tenant_id, state, is_quarantined,
    quarantine_reason) VALUES (%s, %s, 'firing', true, 'repeated_exceptions')
ON CONFLICT (rule_id) DO UPDATE SET is_quarantined = true,
    quarantine_reason = 'repeated_exceptions'
"""


def _dsn(var: str) -> DbConfig:
    value = os.environ.get(var, "").strip()
    if not value:
        raise DbConfigError(f"Falta {var} para la validacion de intents.")
    return DbConfig(dsn=value)


def _fake_email() -> str:
    return f"fake-{uuid4().hex}@ejemplo.test"


def _stream_key(timeframe: Timeframe) -> str:
    return (
        f"market:candles:{_EXCHANGE}:{MarketType.SPOT.value}:"
        f"{_SYMBOL}:{timeframe.value}"
    )


def _gt_close(threshold: str) -> Condition:
    """close > threshold (acceso directo: 1 barra de historia)."""
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


def _gt_texto() -> Condition:
    """close > "mucho": compila (el compilador solo mira fuentes) y revienta al evaluar.

    La comparacion de tipos rica esta diferida en v5.0, asi que _scalar_to_decimal falla
    fuerte ante un escalar no numerico. Sirve para provocar una excepcion de EVAL real
    sin parchear el evaluador ni inyectar un doble.
    """
    condition = _gt_close("30000")
    return condition.model_copy(
        update={
            "right": Term(
                term_kind=TermKind.CONSTANT,
                constant=ScalarValue(
                    scalar_type=ScalarType.STRING, string_value="mucho"
                ),
            )
        }
    )


def _group(timeframe: Timeframe, condition: Condition) -> Group:
    return Group(
        node_id=uuid4(),
        evaluation_context=timeframe.value,
        combine_mode=CombineMode.ALL,
        features=(
            Feature(
                node_id=uuid4(),
                conditions=(condition,),
                combine_mode=CombineMode.ALL,
            ),
        ),
    )


def _mkrule(
    tenant_id: UUID,
    *,
    product: RuleProduct = RuleProduct.ALERT,
    timeframes: tuple[Timeframe, ...] = (Timeframe.H1,),
    symbol: str = _SYMBOL,
    enabled: bool = True,
    condition: Condition | None = None,
) -> AnyRule:
    """Regla con UN grupo por timeframe (un contexto distinto = un intent)."""
    cond = _gt_close("30000") if condition is None else condition
    groups = tuple(_group(tf, cond) for tf in timeframes)
    common = {
        "rule_id": uuid4(),
        "tenant_id": tenant_id,
        "name": "regla-de-intents",
        "target_binding": TargetBinding(binding_kind=BindingKind.MARKET),
        "trigger_policy": TriggerPolicy.CANDLE_CLOSE,
        "groups": groups,
        "rule_combine_mode": RuleCombineMode.ALL,
        "enabled": enabled,
        "market_scope": MarketScope(exchange=_EXCHANGE, symbol=symbol),
    }
    if product is RuleProduct.ALERT:
        return AlertRule(product=RuleProduct.ALERT, **common)
    return TradingSignalRule(product=RuleProduct.TRADING_SIGNAL, **common)


def _candle_message(open_time: int, close: Decimal, timeframe: Timeframe) -> BusMessage:
    """market.candle_closed listo para el handler del worker."""
    payload = {
        "exchange": _EXCHANGE,
        "market_type": MarketType.SPOT.value,
        "symbol": _SYMBOL,
        "timeframe": timeframe.value,
        "open_time": open_time,
        "close_time": open_time + _TF_MS,
        "open": str(close),
        "high": str(close),
        "low": str(close),
        "close": str(close),
        "volume": "1",
        "maturity_state": MaturityState.CLOSED.value,
    }
    return BusMessage(
        event_id=str(uuid4()),
        event_type=MarketCandleEventType.CANDLE_CLOSED.value,
        stream_key=_stream_key(timeframe),
        idempotency_key=f"candle:{uuid4().hex}",
        envelope=json.dumps({"payload": payload}).encode(),
    )


def main() -> None:  # noqa: PLR0912, PLR0915
    failures: list[str] = []
    tenants: list[UUID] = []
    users: list[UUID] = []
    rule_ids: list[UUID] = []

    app_db = PsycopgDatabase(_dsn(DSN_ENV_VAR))
    rules_db = PsycopgDatabase(_dsn(RULES_DSN_ENV_VAR))
    mig_db = PsycopgDatabase(_dsn(MIGRATIONS_DSN_ENV_VAR))
    system_db = SystemScopedDatabase(rules_db)
    scoped_app = TenantScopedDatabase(app_db)
    handler = build_handler(system_db, build_catalog())

    def check(label: str, ok: bool) -> None:
        print(f"  [{'OK' if ok else 'FAIL'}] {label}")
        if not ok:
            failures.append(label)

    def cuenta(sql: str, param: str) -> int:
        with mig_db.transaction() as mig_s:
            row = mig_s.fetchone(sql, (param,))
        if row is None:
            return 0
        valor = row[0]
        assert isinstance(valor, int)
        return valor

    def intents_de(rule_id: UUID) -> int:
        return cuenta(_COUNT_INTENTS_SQL, str(rule_id))

    def reglas_con(rule_id: UUID) -> int:
        return cuenta(_COUNT_RULE_SQL, str(rule_id))

    def procesa(open_time: int, close: Decimal, tf: Timeframe = Timeframe.H1) -> None:
        with rules_db.transaction() as rules_s:
            handler(rules_s, _candle_message(open_time, close, tf))

    def estado(tenant: UUID, rule_id: UUID) -> RuleLifecycleState | None:
        with system_db.transaction(tenant) as sys_s:
            return read_state(sys_s.session, rule_id)

    base = _NOW_MS // _TF_MS * _TF_MS
    try:
        user = register_user(app_db, _fake_email(), _PASSWORD_HASH)
        tenant = provision_tenant_for_user(app_db, user)
        users.append(user)
        tenants.append(tenant)
        print(f"[prep] tenant={tenant} user={user}")

        # =================== PARTE A: acumulacion entre ticks ===================
        print("== A1: STALE tras M velas NOT_EVALUABLE consecutivas ==")
        # Regla sobre 1h SIN velas sembradas -> la serie viene VACIA -> el termino es
        # NOT_EVALUABLE (historia insuficiente), no FALSE. Cada tick suma uno.
        stale_rule = _mkrule(tenant)
        rule_ids.append(stale_rule.rule_id)
        with scoped_app.transaction(user) as app_s:
            create_rule_with_intents(
                app_s, stale_rule, canonical_rule_hash(stale_rule), _NOW_MS
            )

        contadores: list[int] = []
        for tick in range(_STALE_THRESHOLD):
            # open_time sin vela sembrada detras: la ventana sale vacia.
            procesa(base + tick * _TF_MS, Decimal("1"))
            st = estado(tenant, stale_rule.rule_id)
            contadores.append(st.operational.not_evaluable_count if st else -1)
        st_stale = estado(tenant, stale_rule.rule_id)
        check(
            f"not_evaluable_count ACUMULA entre ticks: {contadores}",
            contadores == list(range(1, _STALE_THRESHOLD + 1)),
        )
        check(
            "is_stale=true al alcanzar el umbral",
            st_stale is not None and st_stale.operational.is_stale,
        )
        check(
            "stale_reason persistido (rule_not_evaluable)",
            st_stale is not None
            and st_stale.operational.stale_reason == "rule_not_evaluable",
        )

        print("== A2: una evaluacion BUENA entre medias resetea el contador ==")
        # Se siembra una vela para ese open_time: ahora la serie SI tiene dato.
        buena_ot = base + _STALE_THRESHOLD * _TF_MS
        with mig_db.transaction() as mig_s:
            mig_s.execute(
                _INSERT_CANDLE,
                (
                    f"fake-candle-{stale_rule.rule_id}",
                    _stream_key(Timeframe.H1),
                    _EXCHANGE,
                    MarketType.SPOT.value,
                    _SYMBOL,
                    Timeframe.H1.value,
                    buena_ot,
                    buena_ot + _TF_MS,
                    Decimal("40000"),
                    Decimal("40000"),
                    Decimal("40000"),
                    Decimal("40000"),
                    Decimal("1"),
                ),
            )
        procesa(buena_ot, Decimal("40000"))
        st_reset = estado(tenant, stale_rule.rule_id)
        check(
            "not_evaluable_count vuelve a 0 tras una evaluacion decidible",
            st_reset is not None and st_reset.operational.not_evaluable_count == 0,
        )
        check(
            "is_stale se AUTO-LIMPIA (stale es transitorio, D3)",
            st_reset is not None and not st_reset.operational.is_stale,
        )
        check(
            "y la regla dispara: estado firing",
            st_reset is not None and st_reset.state == "firing",
        )

        print("== A3: CUARENTENA tras N excepciones de evaluacion consecutivas ==")
        # Una constante STRING comparada contra close COMPILA (el compilador solo
        # resuelve fuentes) pero REVIENTA al evaluar (_scalar_to_decimal). Es
        # una excepcion de EVAL genuina, que es justo el eje que cuenta hacia cuarentena
        # -- distinto de un fallo de compilacion, que cuarentena de golpe.
        exc_rule = _mkrule(tenant, condition=_gt_texto())
        rule_ids.append(exc_rule.rule_id)
        with scoped_app.transaction(user) as app_s:
            create_rule_with_intents(
                app_s, exc_rule, canonical_rule_hash(exc_rule), _NOW_MS
            )
        excepciones: list[int] = []
        for _ in range(_QUARANTINE_THRESHOLD):
            procesa(buena_ot, Decimal("40000"))
            st = estado(tenant, exc_rule.rule_id)
            excepciones.append(st.operational.consecutive_exceptions if st else -1)
        st_exc = estado(tenant, exc_rule.rule_id)
        check(
            f"consecutive_exceptions ACUMULA entre ticks: {excepciones}",
            excepciones == list(range(1, _QUARANTINE_THRESHOLD + 1)),
        )
        check(
            "is_quarantined=true al alcanzar el umbral",
            st_exc is not None and st_exc.operational.is_quarantined,
        )
        check(
            "quarantine_reason=repeated_exceptions",
            st_exc is not None
            and st_exc.operational.quarantine_reason == "repeated_exceptions",
        )
        check(
            "last_technical_error persistido (diagnostico, no secreto)",
            st_exc is not None and bool(st_exc.operational.last_technical_error),
        )

        # =================== PARTE B: intents (tests 11-21) ===================
        print("== 11: crear regla activa + intents en la MISMA transaccion ==")
        r11 = _mkrule(tenant)
        rule_ids.append(r11.rule_id)
        with scoped_app.transaction(user) as app_s:
            creados = create_rule_with_intents(
                app_s, r11, canonical_rule_hash(r11), _NOW_MS
            )
        check("regla presente tras commit", reglas_con(r11.rule_id) == 1)
        check(
            f"1 intent presente tras commit (creados={creados})",
            intents_de(r11.rule_id) == 1,
        )

        print("== 12: falla el INTENT -> rollback, no queda regla activa ==")
        r12 = _mkrule(tenant)
        lanzo12 = False
        try:
            with scoped_app.transaction(user) as app_s:
                create_rule_with_intents(app_s, r12, canonical_rule_hash(r12), _NOW_MS)
                # Un SEGUNDO intent identico viola el UNIQUE de origen: el fallo del
                # intent debe arrastrar a la regla.
                store = PostgresIntentStore(app_s)
                for intent in _duplicar_intents(app_s, r12):
                    store.insert(intent)
        except Exception:  # noqa: BLE001
            lanzo12 = True
        check("la insercion del intent fallo", lanzo12)
        check("NO queda regla (rollback total)", reglas_con(r12.rule_id) == 0)
        check("NO queda intent (rollback total)", intents_de(r12.rule_id) == 0)

        print("== 13: falla la REGLA -> no queda intent zombie ==")
        r13 = _mkrule(tenant)
        rule_ids.append(r13.rule_id)
        with scoped_app.transaction(user) as app_s:
            create_rule_with_intents(app_s, r13, canonical_rule_hash(r13), _NOW_MS)
        lanzo13 = False
        try:
            # Reinsertar la MISMA regla viola la PK: la regla falla y el intent que la
            # acompanaba en esa transaccion no debe sobrevivir.
            with scoped_app.transaction(user) as app_s:
                create_rule_with_intents(app_s, r13, canonical_rule_hash(r13), _NOW_MS)
        except Exception:  # noqa: BLE001
            lanzo13 = True
        check("la insercion de la regla fallo (PK duplicada)", lanzo13)
        check(
            "sigue habiendo exactamente 1 intent (ninguno zombie del intento fallido)",
            intents_de(r13.rule_id) == 1,
        )

        print("== 14: regla con DOS evaluation_contexts -> dos intents distintos ==")
        r14 = _mkrule(tenant, timeframes=(Timeframe.H1, Timeframe.H4))
        rule_ids.append(r14.rule_id)
        with scoped_app.transaction(user) as app_s:
            n14 = create_rule_with_intents(
                app_s, r14, canonical_rule_hash(r14), _NOW_MS
            )
        with scoped_app.transaction(user) as app_s:
            listado = PostgresIntentStore(app_s).list_for_subject(tenant, user)
        claves = {
            i.market_stream_key() for i in listado if i.source_ref == str(r14.rule_id)
        }
        check(f"dos intents creados (n={n14})", n14 == 2)
        check(
            "un MarketStreamKey por timeframe (1h y 4h)",
            claves == {_stream_key(Timeframe.H1), _stream_key(Timeframe.H4)},
        )

        print("== 15: los intents NO son ilimitados (acotados por el presupuesto) ==")
        # Dos grupos en el MISMO timeframe son UN interes, no dos.
        r15 = _mkrule(tenant, timeframes=(Timeframe.H1, Timeframe.H1))
        check(
            "contextos repetidos NO duplican intents", len(rule_stream_keys(r15)) == 1
        )
        seis = (
            Timeframe.M1,
            Timeframe.M5,
            Timeframe.M15,
            Timeframe.H1,
            Timeframe.H4,
            Timeframe.D1,
        )
        lanzo15 = False
        try:
            rule_stream_keys(_mkrule(tenant, timeframes=seis))
        except ValueError:
            lanzo15 = True
        check(
            "mas contextos que el tope -> fail-loud (no se abren sin limite)", lanzo15
        )

        print("== 16: desactivar (enabled->false) -> intents retirados ==")
        with scoped_app.transaction(user) as app_s:
            set_rule_enabled(app_s, r11, enabled=False, now_ms=_NOW_MS)
        check("intents retirados al desactivar", intents_de(r11.rule_id) == 0)
        check(
            "la regla SIGUE existiendo (desactivada, no borrada)",
            reglas_con(r11.rule_id) == 1,
        )

        print("== 17: borrar la regla -> intents retirados ==")
        with scoped_app.transaction(user) as app_s:
            delete_rule_with_intents(app_s, r14)
        check("regla borrada", reglas_con(r14.rule_id) == 0)
        check("sus DOS intents retirados", intents_de(r14.rule_id) == 0)

        print("== 18: QUARANTINED (operacional) -> los intents SE MANTIENEN ==")
        antes18 = intents_de(r13.rule_id)
        with system_db.transaction(tenant) as sys_s:
            sys_s.session.execute(_QUARANTINE_SQL, (str(r13.rule_id), str(tenant)))
        st18 = estado(tenant, r13.rule_id)
        check(
            "la regla esta en cuarentena",
            st18 is not None and st18.operational.is_quarantined,
        )
        check(
            f"y sus intents SIGUEN ahi ({antes18}): el stream compartido no se apaga",
            intents_de(r13.rule_id) == antes18 == 1,
        )

        print("== 19: rearmar -> no hace falta recrear el intent ==")
        with system_db.transaction(tenant) as sys_s:
            sys_s.session.execute(
                "UPDATE rule_lifecycle_state SET is_quarantined = false, "
                "quarantine_reason = NULL WHERE rule_id = %s",
                (str(r13.rule_id),),
            )
        st19 = estado(tenant, r13.rule_id)
        check(
            "rearmada (is_quarantined=false)",
            st19 is not None and not st19.operational.is_quarantined,
        )
        check(
            "el intent nunca se fue: rearme INMEDIATO sin rehidratar suscripcion",
            intents_de(r13.rule_id) == 1,
        )

        print("== 20: el ref-count de P07 cuenta los intents de regla SIN CAMBIOS ==")
        with mig_db.transaction() as mig_s:
            demanda = PostgresPublicDemand(mig_s).snapshot()
        clave_h1 = _stream_key(Timeframe.H1)
        check(
            f"la ventanilla agregada ve el flujo 1h ({demanda.get(clave_h1)})",
            demanda.get(clave_h1, 0) >= 1,
        )

        print("== 21: dos tenants sobre el MISMO stream -> ref-count compartido ==")
        user_b = register_user(app_db, _fake_email(), _PASSWORD_HASH)
        tenant_b = provision_tenant_for_user(app_db, user_b)
        users.append(user_b)
        tenants.append(tenant_b)
        antes21 = demanda.get(clave_h1, 0)
        r21 = _mkrule(tenant_b, product=RuleProduct.TRADING_SIGNAL)
        rule_ids.append(r21.rule_id)
        with scoped_app.transaction(user_b) as app_s:
            create_rule_with_intents(app_s, r21, canonical_rule_hash(r21), _NOW_MS)
        with mig_db.transaction() as mig_s:
            demanda2 = PostgresPublicDemand(mig_s).snapshot()
        check(
            f"el mismo stream suma un interes mas ({antes21} -> "
            f"{demanda2.get(clave_h1)}) y NO se duplica el stream",
            demanda2.get(clave_h1, 0) == antes21 + 1,
        )
        with scoped_app.transaction(user_b) as app_s:
            store_b = PostgresIntentStore(app_s)
            propios = store_b.list_for_subject(tenant_b, user_b)
        check(
            "cada tenant solo ve SU intent (RLS user-scoped)",
            len(propios) == 1 and propios[0].source_ref == str(r21.rule_id),
        )
    finally:
        with mig_db.transaction() as limpieza:
            for rid in rule_ids:
                limpieza.execute(
                    "DELETE FROM outbox WHERE stream_key = %s", (f"rule:{rid}",)
                )
                limpieza.execute(
                    "DELETE FROM market_candle WHERE idempotency_key = %s",
                    (f"fake-candle-{rid}",),
                )
            for t in tenants:
                limpieza.execute("DELETE FROM tenant WHERE tenant_id = %s", (str(t),))
            for u in users:
                limpieza.execute("DELETE FROM app_user WHERE user_id = %s", (str(u),))
        mig_db.close()
        app_db.close()
        rules_db.close()

    if failures:
        print(f"RESUMEN: FALLO - {len(failures)} comprobaciones:")
        for reason in failures:
            print(f"  - {reason}")
        raise SystemExit(1)
    print(
        "RESUMEN: OK - el estado operacional ACUMULA entre ticks (stale + reset) y los "
        "SubscriptionIntent de regla son atomicos, van por enabled y no por salud, y "
        "alimentan el ref-count de P07 sin cambios (11-21)."
    )


def _duplicar_intents(
    scoped: TenantScopedSession, rule: AnyRule
) -> list[SubscriptionIntent]:
    """Los MISMOS intents que la regla ya declaro: reinsertarlos viola el UNIQUE.

    Es la forma HONESTA de forzar el fallo del intent dentro de la transaccion: no se
    parchea el repo ni se inyecta un doble, se provoca la violacion que el esquema ya
    define (market_intent_origen_unico).
    """
    from ce_v5.infra.db.rules import _intents_for_rule  # noqa: PLC0415

    ctx = scoped.context
    return list(_intents_for_rule(rule, ctx.tenant_id, ctx.user_id, _NOW_MS))


if __name__ == "__main__":
    main()
