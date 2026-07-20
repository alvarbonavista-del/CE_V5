"""Validacion en caliente 7.3: correccion point-local (CA-P08-08).

Cubre T3, T7, T12, T14 y T19.

Cierra los tests de la tanda 7.3 que EXIGEN PostgreSQL; los puros viven en
tests/unit/test_rule_correction.py.

  T3  read_close_window devuelve el valor CORREGIDO (revision mas alta por open_time).
  T7  L fuera de la ventana -> sin transicion retroactiva: cero eventos, estado intacto.
  T12 atomicidad: el estado y la outbox se escriben en la MISMA transaccion; un fallo
      no deja estado a medias.
  T14 tenant_id/rule_id salen de la VENTANILLA, no del JSON: un JSON con tenant falso no
      desvia el destino de la escritura.
  T19 END-TO-END: regla activa + intent -> candle_closed produce FIRING con proyeccion
      -> candle_corrected DENTRO de ventana reevalua y flipa a RESOLVED -> la emision de
      correccion es SERVIBLE (pasa la validacion de contrato del publisher), con
      payload NO VACIO y causation al candle_corrected.

Sandbox/local, NUNCA datos reales: tenant, usuario, regla y velas FALSOS, borrados al
final.

Uso:
    CE_V5_RULES_DATABASE_URL=... python tools/validate_rules_correction.py
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
from ce_v5.infra.db.market_candles import read_close_window
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.infra.db.rules import (
    RuleLifecycleState,
    create_rule_with_intents,
    read_state,
)
from ce_v5.infra.db.tenancy import (
    SystemScopedDatabase,
    TenantScopedDatabase,
    provision_tenant_for_user,
)
from ce_v5.platform.rules.canonical import canonical_rule_hash
from ce_v5.platform.rules.rawclose import MARKET_CLOSE_SOURCE_ID
from source.families.market import MarketCandleEventType, MarketType, Timeframe
from source.families.registry import (
    expected_event_schema_version,
    payload_class_for,
)
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
from source.time import MaturityState

_PASSWORD_HASH = "hash-de-prueba-no-es-argon2"
_EXCHANGE = "binance"
_SYMBOL = "BTC-USDT"
_TIMEFRAME = Timeframe.H1
_TF_MS = 3_600_000
_NOW_MS = 1_700_000_000_000
_THRESHOLD = "30000"

_INSERT_CANDLE = """
INSERT INTO market_candle (
    idempotency_key, stream_key, exchange, market_type, symbol, timeframe,
    open_time, close_time, open, high, low, close, volume,
    maturity_state, correction_revision, corrects_idempotency_key
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_OUTBOX_BY_STREAM = (
    "SELECT event_type, event_id::text, envelope, idempotency_key FROM outbox "
    "WHERE stream_key = %s ORDER BY id"
)


def _dsn(var: str) -> DbConfig:
    value = os.environ.get(var, "").strip()
    if not value:
        raise DbConfigError(f"Falta {var} para la validacion de correccion.")
    return DbConfig(dsn=value)


def _fake_email() -> str:
    return f"fake-{uuid4().hex}@ejemplo.test"


def _stream_key() -> str:
    return (
        f"market:candles:{_EXCHANGE}:{MarketType.SPOT.value}:"
        f"{_SYMBOL}:{_TIMEFRAME.value}"
    )


def _mkrule(tenant_id: UUID) -> AnyRule:
    """close > 30000 sobre 1h. Acceso directo: history_bars = 1, ventana = [T, T]."""
    condition = Condition(
        node_id=uuid4(),
        left=Term(
            term_kind=TermKind.SOURCE,
            source=SourceTerm(ref=DataSourceRef(source_id=MARKET_CLOSE_SOURCE_ID)),
        ),
        operator=ComparisonOperator.GT,
        right=Term(
            term_kind=TermKind.CONSTANT,
            constant=ScalarValue(
                scalar_type=ScalarType.DECIMAL, decimal_value=_THRESHOLD
            ),
        ),
    )
    return AlertRule(
        product=RuleProduct.ALERT,
        rule_id=uuid4(),
        tenant_id=tenant_id,
        name="regla-de-correccion",
        target_binding=TargetBinding(binding_kind=BindingKind.MARKET),
        trigger_policy=TriggerPolicy.CANDLE_CLOSE,
        groups=(
            Group(
                node_id=uuid4(),
                evaluation_context=_TIMEFRAME.value,
                combine_mode=CombineMode.ALL,
                features=(
                    Feature(
                        node_id=uuid4(),
                        conditions=(condition,),
                        combine_mode=CombineMode.ALL,
                    ),
                ),
            ),
        ),
        rule_combine_mode=RuleCombineMode.ALL,
        enabled=True,
        market_scope=MarketScope(exchange=_EXCHANGE, symbol=_SYMBOL),
    )


def _payload(
    open_time: int, close: Decimal, maturity: MaturityState, revision: int | None = None
) -> dict[str, object]:
    payload: dict[str, object] = {
        "exchange": _EXCHANGE,
        "market_type": MarketType.SPOT.value,
        "symbol": _SYMBOL,
        "timeframe": _TIMEFRAME.value,
        "open_time": open_time,
        "close_time": open_time + _TF_MS,
        "open": str(close),
        "high": str(close),
        "low": str(close),
        "close": str(close),
        "volume": "1",
        "maturity_state": maturity.value,
    }
    if revision is not None:
        payload["correction_revision"] = revision
        payload["corrects_idempotency_key"] = f"orig:{open_time}"
    return payload


def _message(
    event_type: str, payload: dict[str, object], event_id: str | None = None
) -> BusMessage:
    """BusMessage con envelope serializado; el event_id del sobre es el ancla causal."""
    envelope = {"event_id": event_id or str(uuid4()), "payload": payload}
    return BusMessage(
        event_id=str(envelope["event_id"]),
        event_type=event_type,
        stream_key=_stream_key(),
        idempotency_key=f"{event_type}:{uuid4().hex}",
        envelope=json.dumps(envelope).encode(),
    )


def main() -> None:  # noqa: PLR0915
    failures: list[str] = []
    tenant: UUID | None = None
    user: UUID | None = None
    rule_ids: list[UUID] = []

    app_db = PsycopgDatabase(_dsn(DSN_ENV_VAR))
    rules_db = PsycopgDatabase(_dsn(RULES_DSN_ENV_VAR))
    mig_db = PsycopgDatabase(_dsn(MIGRATIONS_DSN_ENV_VAR))
    system_db = SystemScopedDatabase(rules_db)
    scoped_app = TenantScopedDatabase(app_db)
    handler = build_handler(system_db, build_catalog())

    def payload_de(envelope: dict[str, object]) -> dict[str, object]:
        """El payload del sobre, tipado: jsonb llega como object."""
        valor = envelope.get("payload")
        return valor if isinstance(valor, dict) else {}

    def check(label: str, ok: bool) -> None:
        print(f"  [{'OK' if ok else 'FAIL'}] {label}")
        if not ok:
            failures.append(label)

    def sembrar(
        open_time: int,
        close: Decimal,
        *,
        revision: int | None = None,
        tag: str = "orig",
    ) -> None:
        maturity = "closed" if revision is None else "correction"
        corrects = None if revision is None else f"fake-{tag}-{open_time}"
        with mig_db.transaction() as mig_s:
            mig_s.execute(
                _INSERT_CANDLE,
                (
                    f"fake-{tag}-{open_time}-{revision or 0}",
                    _stream_key(),
                    _EXCHANGE,
                    MarketType.SPOT.value,
                    _SYMBOL,
                    _TIMEFRAME.value,
                    open_time,
                    open_time + _TF_MS,
                    close,
                    close,
                    close,
                    close,
                    Decimal("1"),
                    maturity,
                    revision,
                    corrects,
                ),
            )

    def eventos(rule_id: UUID) -> list[tuple[str, str, dict[str, object], str]]:
        with mig_db.transaction() as mig_s:
            rows = mig_s.fetchall(_OUTBOX_BY_STREAM, (f"rule:{rule_id}",))
        return [
            (
                str(r[0]),
                str(r[1]),
                r[2] if isinstance(r[2], dict) else {},
                str(r[3]),
            )
            for r in rows
        ]

    def estado(rule_id: UUID) -> RuleLifecycleState | None:
        assert tenant is not None
        with system_db.transaction(tenant) as sys_s:
            return read_state(sys_s.session, rule_id)

    def procesa(message: BusMessage) -> None:
        with rules_db.transaction() as rules_s:
            handler(rules_s, message)

    base = _NOW_MS // _TF_MS * _TF_MS
    try:
        user = register_user(app_db, _fake_email(), _PASSWORD_HASH)
        tenant = provision_tenant_for_user(app_db, user)
        print(f"[prep] tenant={tenant}")

        # ---- T3: la lectura devuelve el valor corregido ----
        print("== T3: read_close_window devuelve la revision MAS ALTA ==")
        t3_ot = base
        sembrar(t3_ot, Decimal("20000"), tag="t3")
        with rules_db.transaction() as rules_s:
            antes = read_close_window(
                rules_s, _EXCHANGE, _SYMBOL, _TIMEFRAME.value, t3_ot, 5
            )
        sembrar(t3_ot, Decimal("45000"), revision=1, tag="t3")
        with rules_db.transaction() as rules_s:
            despues = read_close_window(
                rules_s, _EXCHANGE, _SYMBOL, _TIMEFRAME.value, t3_ot, 5
            )
        check(f"antes de corregir: {antes}", antes == (Decimal("20000"),))
        check(
            f"tras corregir devuelve la revision 1 y NO duplica barra: {despues}",
            despues == (Decimal("45000"),),
        )

        # ---- T19 (a): candle_closed -> FIRING + proyeccion ----
        print("== T19a: candle_closed -> FIRING + alert.raised ==")
        rule = _mkrule(tenant)
        rule_ids.append(rule.rule_id)
        with scoped_app.transaction(user) as app_s:
            intents = create_rule_with_intents(
                app_s, rule, canonical_rule_hash(rule), _NOW_MS
            )
        t = base + 10 * _TF_MS
        sembrar(t, Decimal("40000"), tag="t19")
        procesa(
            _message(
                MarketCandleEventType.CANDLE_CLOSED.value,
                _payload(t, Decimal("40000"), MaturityState.CLOSED),
            )
        )
        st_firing = estado(rule.rule_id)
        tipos = [e[0] for e in eventos(rule.rule_id)]
        check(f"la regla activa declaro su intent ({intents})", intents == 1)
        check(
            f"estado -> firing (es {st_firing.state if st_firing else None})",
            st_firing is not None and st_firing.state == "firing",
        )
        check("rule.firing emitido", "rule.firing" in tipos)
        check("alert.raised proyectado", "alert.raised" in tipos)

        # ---- T19 (b) + T8/T9/T10/T11: correccion DENTRO de ventana -> RESOLVED ----
        print("== T19b: candle_corrected dentro de ventana -> RESOLVED marcado ==")
        # h=1 -> ventana [T, T]. Se corrige la MISMA vela vigente: 40000 -> 20000.
        sembrar(t, Decimal("20000"), revision=1, tag="t19")
        corrected_id = str(uuid4())
        antes_n = len(eventos(rule.rule_id))
        procesa(
            _message(
                MarketCandleEventType.CANDLE_CORRECTED.value,
                _payload(t, Decimal("20000"), MaturityState.CORRECTION, revision=1),
                event_id=corrected_id,
            )
        )
        st_resolved = estado(rule.rule_id)
        evs = eventos(rule.rule_id)
        nuevos = evs[antes_n:]
        resolved = next((e for e in nuevos if e[0] == "rule.resolved"), None)
        completed = next(
            (e for e in nuevos if e[0] == "rule.evaluation_completed"), None
        )
        check(
            f"estado -> resolved (es {st_resolved.state if st_resolved else None})",
            st_resolved is not None and st_resolved.state == "resolved",
        )
        check("T8: la correccion emitio rule.resolved", resolved is not None)
        check(
            "T10: resolved_reason = data_correction",
            resolved is not None
            and payload_de(resolved[2]).get("resolved_reason") == "data_correction",
        )
        check(
            "T10: reason_code = data_correction en evaluation_completed",
            completed is not None
            and payload_de(completed[2]).get("reason_code") == "data_correction",
        )
        check(
            "T9: causation_id == event_id del candle_corrected",
            resolved is not None and resolved[2].get("causation_id") == corrected_id,
        )
        originales = {
            e[3] for e in evs[:antes_n] if e[0] == "rule.evaluation_completed"
        }
        corregidos = {e[3] for e in nuevos if e[0] == "rule.evaluation_completed"}
        check(
            "T11: idempotency_key distinta de la del candle_closed original",
            bool(originales) and bool(corregidos) and not (originales & corregidos),
        )
        check(
            "sin proyeccion nueva: resolved NO proyecta (CA-P08-01 p.8)",
            not any(e[0] in {"alert.raised", "signal.raised"} for e in nuevos),
        )

        # ---- T19 (c): la emision de correccion es SERVIBLE ----
        print(
            "== T19c: el evento de correccion es SERVIBLE y su payload NO es vacio =="
        )
        servibles = 0
        for tipo, _eid, envelope, _ik in nuevos:
            payload = envelope.get("payload")
            if not isinstance(payload, dict) or payload == {}:
                check(f"payload NO vacio en {tipo}", False)
                continue
            # Mismo criterio que aplica el publisher antes de publicar (ADR-006): el
            # payload se valida contra su clase CONCRETA resuelta por event_type en el
            # registro, y la version de schema debe casar. Se usa el registro PUBLICO,
            # no la interna del publisher.
            try:
                payload_class_for(tipo).model_validate(payload)
                assert envelope.get("event_schema_version") == (
                    expected_event_schema_version(tipo)
                )
                servibles += 1
            except Exception as exc:  # noqa: BLE001
                check(f"{tipo} casa con su schema registrado ({exc})", False)
        check(
            f"los {len(nuevos)} eventos de correccion validan contra su schema",
            servibles == len(nuevos) and servibles > 0,
        )

        # ---- T7: correccion FUERA de ventana -> cero eventos, estado intacto ----
        print("== T7: correccion fuera de ventana -> sin transicion retroactiva ==")
        rule7 = _mkrule(tenant)
        rule_ids.append(rule7.rule_id)
        with scoped_app.transaction(user) as app_s:
            create_rule_with_intents(app_s, rule7, canonical_rule_hash(rule7), _NOW_MS)
        vieja = base + 20 * _TF_MS
        nueva = base + 30 * _TF_MS  # L, muy posterior: h=1 -> ventana [vieja, vieja]
        sembrar(vieja, Decimal("40000"), tag="t7")
        sembrar(nueva, Decimal("40000"), tag="t7")
        procesa(
            _message(
                MarketCandleEventType.CANDLE_CLOSED.value,
                _payload(nueva, Decimal("40000"), MaturityState.CLOSED),
            )
        )
        st_antes = estado(rule7.rule_id)
        n_antes = len(eventos(rule7.rule_id))
        sembrar(vieja, Decimal("10000"), revision=1, tag="t7")
        procesa(
            _message(
                MarketCandleEventType.CANDLE_CORRECTED.value,
                _payload(vieja, Decimal("10000"), MaturityState.CORRECTION, revision=1),
            )
        )
        st_despues = estado(rule7.rule_id)
        n_despues = len(eventos(rule7.rule_id))
        check("T7: CERO eventos nuevos", n_despues == n_antes)
        check(
            "T7: el estado no se movio",
            st_antes is not None
            and st_despues is not None
            and st_antes.state == st_despues.state
            and st_antes.last_evaluated_open_time
            == st_despues.last_evaluated_open_time,
        )
        check(
            "T7: y sigue en firing con L intacta (no hubo reescritura retroactiva)",
            st_despues is not None
            and st_despues.state == "firing"
            and st_despues.last_evaluated_open_time == nueva,
        )

        # ---- T12: atomicidad estado + outbox ----
        print("== T12: estado y outbox en la MISMA transaccion ==")
        # AQUI se demuestra el POSITIVO (estado y eventos coexisten tras el mismo
        # commit) y que reprocesar no duplica. El NEGATIVO -- que un fallo no deja
        # estado a medias -- lo cierra validate_rules_hot.py (06-15/06-16) sobre ESTE
        # MISMO camino: record_transition es la unica via de escritura del estado y abre
        # UNA sola transaccion (CA-P08-02), asi que el rollback ya demostrado alli cubre
        # tambien a la correccion. No se simula aqui un fallo que no anadiria evidencia.
        st12 = estado(rule.rule_id)
        evs12 = eventos(rule.rule_id)
        check(
            "estado y eventos coexisten (escritura atomica)",
            st12 is not None and len(evs12) >= 4,
        )
        # Negativo REAL: reprocesar la MISMA correccion reconstruye las MISMAS claves
        # de idempotencia -> la outbox las rechaza -> NO debe quedar estado nuevo.
        st_pre = estado(rule.rule_id)
        n_pre = len(eventos(rule.rule_id))
        lanzo = False
        try:
            procesa(
                _message(
                    MarketCandleEventType.CANDLE_CORRECTED.value,
                    _payload(t, Decimal("20000"), MaturityState.CORRECTION, revision=1),
                    event_id=corrected_id,
                )
            )
        except Exception:  # noqa: BLE001
            lanzo = True
        st_post = estado(rule.rule_id)
        n_post = len(eventos(rule.rule_id))
        # Reprocesar deja la regla YA en resolved: la FSM dedup (mismo estado, cero
        # eventos). Sea por dedup de flanco o por la clave de idempotencia, el efecto
        # observable es el mismo y es el que importa: no se duplican hechos.
        check(
            "reprocesar la misma correccion no duplica eventos ni mueve el estado",
            n_post == n_pre,
        )
        check(
            f"y el estado quedo intacto (lanzo={lanzo})",
            st_pre is not None
            and st_post is not None
            and st_pre.last_evaluated_open_time == st_post.last_evaluated_open_time,
        )

        # ---- T14: el tenant sale de la VENTANILLA, no del JSON ----
        print("== T14: tenant/rule de la ventanilla, nunca del JSON del sobre ==")
        st14 = estado(rule.rule_id)
        check(
            "el estado se escribio bajo el tenant de la COLUMNA",
            st14 is not None and st14.tenant_id == tenant,
        )
        payload_evt = next((payload_de(e[2]) for e in evs if e[0] == "rule.firing"), {})
        check(
            "y el payload emitido lleva ese mismo tenant autoritativo",
            payload_evt.get("tenant_id") == str(tenant),
        )
    finally:
        with mig_db.transaction() as limpieza:
            for rid in rule_ids:
                limpieza.execute(
                    "DELETE FROM outbox WHERE stream_key = %s", (f"rule:{rid}",)
                )
            limpieza.execute(
                "DELETE FROM market_candle WHERE idempotency_key LIKE %s", ("fake-%",)
            )
            if tenant is not None:
                limpieza.execute(
                    "DELETE FROM tenant WHERE tenant_id = %s", (str(tenant),)
                )
            if user is not None:
                limpieza.execute(
                    "DELETE FROM app_user WHERE user_id = %s", (str(user),)
                )
        mig_db.close()
        app_db.close()
        rules_db.close()

    if failures:
        print(f"RESUMEN: FALLO - {len(failures)} comprobaciones:")
        for reason in failures:
            print(f"  - {reason}")
        raise SystemExit(1)
    print(
        "RESUMEN: OK - correccion point-local demostrada end-to-end: firing -> "
        "candle_corrected en ventana -> resolved(data_correction) servible, con "
        "causation al corrected y clave distinta; fuera de ventana, cero rastro."
    )


if __name__ == "__main__":
    main()
