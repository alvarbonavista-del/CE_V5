"""Validacion en caliente del worker de reglas: test 22 + mordida D1 (P08 7.1).

Demuestra, con role-switching REAL (migraciones siembra y limpia; app autora;
ce_v5_rules evalua bajo su propio DSN), que el motor usa el HISTORICO de
market_candle:

  T22-a: read_close_window devuelve la ventana de cierres CERRADOS del flujo, en orden
         oldest->newest y acotada a `bars` -- leida por ce_v5_rules con el GRANT SELECT
         de la 0016 (sin el, esta lectura seria un permission denied).
  T22-b: el handler del worker (on_candle_closed) descubre la regla por la ventanilla,
         evalua sobre esa ventana y el ESTADO AVANZA a firing.
  T22-c: una vela PROVISIONAL (market.candle_updated) NO la procesa el handler y NO
         mueve el estado: el invariante firmado (P07-A) es real, no un comentario.
  T22-d: ce_v5_rules NO puede ESCRIBIR market_candle (append-only para el motor).

Y comprueba que el check estatico MUERDE (26/27/28) ejercitando su LOGICA PURA con mapas
de privilegios mutados: un check que solo se ve pasar no demuestra nada.

Sandbox/local, NUNCA datos reales: tenant, regla y velas FALSOS, borrados al final. El
end-to-end completo (firing publicado al bus y servible) es la 7.3.

Uso:
    CE_V5_RULES_DATABASE_URL=... python tools/validate_rules_worker.py
Exige CE_V5_DATABASE_URL (app), CE_V5_RULES_DATABASE_URL (reglas) y
CE_V5_MIGRATIONS_DATABASE_URL (migraciones).
"""

from __future__ import annotations

import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from ce_v5.core.bus import BusMessage  # noqa: E402
from ce_v5.entrypoints.worker_rules.composition import (  # noqa: E402
    build_catalog,
    build_handler,
)
from ce_v5.infra.db.config import (  # noqa: E402
    DSN_ENV_VAR,
    MIGRATIONS_DSN_ENV_VAR,
    RULES_DSN_ENV_VAR,
    DbConfig,
    DbConfigError,
)
from ce_v5.infra.db.identity import register_user  # noqa: E402
from ce_v5.infra.db.market_candles import read_close_window  # noqa: E402
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase  # noqa: E402
from ce_v5.infra.db.rules import insert_rule_definition, read_state  # noqa: E402
from ce_v5.infra.db.tenancy import (  # noqa: E402
    SystemScopedDatabase,
    TenantScopedDatabase,
    provision_tenant_for_user,
)
from ce_v5.platform.rules.canonical import canonical_rule_hash  # noqa: E402
from ce_v5.platform.rules.rawclose import MARKET_CLOSE_SOURCE_ID  # noqa: E402
from check_rules_access import (  # noqa: E402
    MARKET_CANDLE_TABLE,
    RULE_DEFINITION_TABLE,
    check_rules,
)
from source.families.market import (  # noqa: E402
    MarketCandleEventType,
    MarketType,
    Timeframe,
)
from source.rules.condition import Condition  # noqa: E402
from source.rules.feature import Feature  # noqa: E402
from source.rules.group import Group  # noqa: E402
from source.rules.market_rules import (  # noqa: E402
    AlertRule,
    AnyRule,
    MarketScope,
    RuleProduct,
)
from source.rules.reference import DataSourceRef  # noqa: E402
from source.rules.rule import BindingKind, TargetBinding  # noqa: E402
from source.rules.scalar import ScalarType, ScalarValue  # noqa: E402
from source.rules.term import SourceTerm, Term, TermKind  # noqa: E402
from source.rules.vocab import (  # noqa: E402
    CombineMode,
    ComparisonOperator,
    RuleCombineMode,
    TriggerPolicy,
)
from source.time import MaturityState  # noqa: E402

_PASSWORD_HASH = "hash-de-prueba-no-es-argon2"
_EXCHANGE = "binance"
_SYMBOL = "BTC-USDT"
_TIMEFRAME = Timeframe.H1
_TF_MS = 3_600_000  # 1h; open_time debe caer en frontera exacta (contrato market).
_THRESHOLD = "30000"

# Serie sembrada, oldest->newest. La ultima (40000) cruza el umbral: la regla dispara
# SOBRE EL HISTORICO, no sobre un dato inventado por el arnes.
_CLOSES = (Decimal("20000"), Decimal("25000"), Decimal("28000"), Decimal("40000"))

_INSERT_CANDLE = """
INSERT INTO market_candle (
    idempotency_key, stream_key, exchange, market_type, symbol, timeframe,
    open_time, close_time, open, high, low, close, volume, maturity_state
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'closed')
"""


def _dsn(var: str) -> DbConfig:
    value = os.environ.get(var, "").strip()
    if not value:
        raise DbConfigError(f"Falta {var} para la validacion del worker de reglas.")
    return DbConfig(dsn=value)


def _stream_key() -> str:
    return (
        f"market:candles:{_EXCHANGE}:{MarketType.SPOT.value}:"
        f"{_SYMBOL}:{_TIMEFRAME.value}"
    )


def _mkrule(tenant_id: UUID) -> AnyRule:
    """AlertRule minima: close > 30000 sobre 1h (acceso directo, 1 barra)."""
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
    group = Group(
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
    )
    return AlertRule(
        product=RuleProduct.ALERT,
        rule_id=uuid4(),
        tenant_id=tenant_id,
        name="regla-del-worker",
        target_binding=TargetBinding(binding_kind=BindingKind.MARKET),
        trigger_policy=TriggerPolicy.CANDLE_CLOSE,
        groups=(group,),
        rule_combine_mode=RuleCombineMode.ALL,
        enabled=True,
        market_scope=MarketScope(exchange=_EXCHANGE, symbol=_SYMBOL),
    )


def _candle_payload(
    open_time: int, close: Decimal, maturity: MaturityState
) -> dict[str, object]:
    """Payload de vela como dict JSON (el handler valida contra el contrato)."""
    return {
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


def _message(event_type: str, payload: dict[str, object]) -> BusMessage:
    """BusMessage con el envelope serializado, como lo entrega el bus.

    El handler solo lee envelope['payload'] (el resto del sobre lo valida el publisher
    antes de publicar, ADR-006), asi que el arnes envuelve el payload y no re-fabrica un
    envelope completo que nadie mirara aqui.
    """
    return BusMessage(
        event_id=str(uuid4()),
        event_type=event_type,
        stream_key=_stream_key(),
        idempotency_key=f"{event_type}:{uuid4().hex}",
        envelope=json.dumps({"payload": payload}).encode(),
    )


def _mordida_del_check() -> list[tuple[str, bool]]:
    """Tests 26/27/28: el check ROMPE si se concede de mas. Logica pura, sin DB.

    Se parte de un mapa de privilegios SANO (el que la 0016 deja) y se muta UNA cosa
    cada vez. Si el check siguiera verde con la mutacion, no estaria protegiendo nada.
    """
    from check_identity_access import FunctionFacts

    ventanilla = FunctionFacts(
        name="rules_for_market",
        is_security_definer=True,
        config=("search_path=public",),
        arguments="p_exchange text, p_symbol text, p_timeframe text",
        result=(
            "TABLE(rule_id uuid, tenant_id uuid, product text, "
            "canonical_rule_hash text, schema_version integer, definition jsonb)"
        ),
        body="SELECT 1",
        execute_for_public=False,
        execute_for_app=False,
        execute_for_ingestion=False,
        execute_for_operator=False,
    )
    outbox_ok = {
        "outbox_rules_insert": "event_type like 'rule.%' or 'signal.' or 'alert.'",
        "outbox_rules_read": "event_type like 'rule.%' or 'signal.' or 'alert.'",
        "outbox_rules_update": "event_type like 'rule.%' or 'signal.' or 'alert.'",
    }
    sano: dict[tuple[str, str, str], bool] = {
        ("ce_v5_rules", MARKET_CANDLE_TABLE, "SELECT"): True,
    }

    def corre(privs: dict[tuple[str, str, str], bool], demand: bool = False) -> bool:
        """True si el check FALLA (que es lo que se espera de cada mutacion)."""
        return bool(check_rules(ventanilla, True, privs, outbox_ok, demand))

    casos: list[tuple[str, bool]] = []
    # Base: el mapa sano pasa. Si esto fallara, el resto no probaria nada.
    casos.append(("base sana -> el check PASA", not corre(dict(sano))))
    # (26) Permiso ANCHO de mas sobre una tabla de mercado prohibida.
    ancho = dict(sano)
    ancho[("ce_v5_rules", "market_instrument", "SELECT")] = True
    casos.append(("26: SELECT de market_instrument -> el check FALLA", corre(ancho)))
    # (27) ESCRITURA de mercado: el historico es append-only tambien para el motor.
    escritura = dict(sano)
    escritura[("ce_v5_rules", MARKET_CANDLE_TABLE, "INSERT")] = True
    casos.append(("27: INSERT en market_candle -> el check FALLA", corre(escritura)))
    # (28) SELECT DIRECTO de rule_definition (saltandose la ventanilla).
    directo = dict(sano)
    directo[("ce_v5_rules", RULE_DEFINITION_TABLE, "SELECT")] = True
    casos.append(
        (
            f"28: SELECT directo de {RULE_DEFINITION_TABLE} -> el check FALLA",
            corre(directo),
        )
    )
    # (6) EXECUTE de la ventanilla de demanda del ingestor.
    casos.append(
        (
            "6: EXECUTE de market_public_demand -> el check FALLA",
            corre(dict(sano), True),
        )
    )
    # (1) POSITIVO: si DESAPARECE el SELECT el motor no evalua -> el check FALLA.
    casos.append(("1: sin SELECT de market_candle -> el check FALLA", corre({})))
    return casos


def main() -> None:
    failures: list[str] = []
    tenant: UUID | None = None
    user: UUID | None = None
    rule_id: UUID | None = None

    app_db = PsycopgDatabase(_dsn(DSN_ENV_VAR))
    rules_db = PsycopgDatabase(_dsn(RULES_DSN_ENV_VAR))
    mig_db = PsycopgDatabase(_dsn(MIGRATIONS_DSN_ENV_VAR))
    system_db = SystemScopedDatabase(rules_db)
    scoped_app = TenantScopedDatabase(app_db)
    catalog = build_catalog()
    handler = build_handler(system_db, catalog)

    def check(label: str, ok: bool) -> None:
        print(f"  [{'OK' if ok else 'FAIL'}] {label}")
        if not ok:
            failures.append(label)

    base_open = 1_700_000_000_000 // _TF_MS * _TF_MS  # frontera exacta de 1h
    try:
        user = register_user(app_db, _fake_email(), _PASSWORD_HASH)
        tenant = provision_tenant_for_user(app_db, user)
        rule = _mkrule(tenant)
        rule_id = rule.rule_id
        print(f"[prep] tenant={tenant} rule={rule_id}")

        with scoped_app.transaction(user) as app_s:
            insert_rule_definition(app_s, rule, canonical_rule_hash(rule))

        # SIEMBRA del historico con el rol de MIGRACIONES (ce_v5_rules no puede: T22-d).
        with mig_db.transaction() as mig_s:
            for index, close in enumerate(_CLOSES):
                open_time = base_open + index * _TF_MS
                mig_s.execute(
                    _INSERT_CANDLE,
                    (
                        f"fake-candle-{rule_id}-{index}",
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
                    ),
                )
        last_open = base_open + (len(_CLOSES) - 1) * _TF_MS
        print(f"[prep] {len(_CLOSES)} velas cerradas hasta open_time={last_open}")

        # ---- T22-a: ce_v5_rules LEE la ventana del historico (GRANT de la 0016) ----
        print("== T22-a: ce_v5_rules lee la ventana de cierres de market_candle ==")
        with rules_db.transaction() as rules_s:
            ventana = read_close_window(
                rules_s, _EXCHANGE, _SYMBOL, _TIMEFRAME.value, last_open, 10
            )
            recorte = read_close_window(
                rules_s, _EXCHANGE, _SYMBOL, _TIMEFRAME.value, last_open, 2
            )
            previa = read_close_window(
                rules_s, _EXCHANGE, _SYMBOL, _TIMEFRAME.value, base_open, 10
            )
        check(f"ventana completa oldest->newest == {_CLOSES}", ventana == _CLOSES)
        check("bars recorta por el extremo ANTIGUO", recorte == _CLOSES[-2:])
        check("up_to_open_time acota el futuro", previa == _CLOSES[:1])

        # ---- T22-b: el handler evalua sobre el historico y el estado AVANZA ----
        print(
            "== T22-b: on_candle_closed evalua sobre el historico -> estado avanza =="
        )
        with system_db.transaction(tenant) as sys_s:
            antes = read_state(sys_s.session, rule_id)
        with rules_db.transaction() as rules_s:
            handler(
                rules_s,
                _message(
                    MarketCandleEventType.CANDLE_CLOSED.value,
                    _candle_payload(last_open, _CLOSES[-1], MaturityState.CLOSED),
                ),
            )
        with system_db.transaction(tenant) as sys_s:
            despues = read_state(sys_s.session, rule_id)
        check("estado inexistente antes de la vela", antes is None)
        check(
            f"estado -> firing tras candle_closed (es {_estado(despues)})",
            despues is not None and despues.state == "firing",
        )
        check(
            "last_evaluated_open_time == open_time de la vela",
            despues is not None and despues.last_evaluated_open_time == last_open,
        )

        # ---- T22-c: una vela PROVISIONAL no la procesa el handler ----
        print(
            "== T22-c: candle_updated (provisional) NO se evalua (invariante P07-A) =="
        )
        siguiente = last_open + _TF_MS
        with rules_db.transaction() as rules_s:
            handler(
                rules_s,
                _message(
                    MarketCandleEventType.CANDLE_UPDATED.value,
                    _candle_payload(
                        siguiente, Decimal("99000"), MaturityState.PROVISIONAL
                    ),
                ),
            )
        with system_db.transaction(tenant) as sys_s:
            tras_provisional = read_state(sys_s.session, rule_id)
        check(
            "el estado NO se movio con la provisional",
            tras_provisional is not None
            and tras_provisional.last_evaluated_open_time == last_open,
        )

        # ---- T22-d: el motor NO puede escribir el historico ----
        print("== T22-d: ce_v5_rules NO escribe market_candle (append-only) ==")
        lanzo, msg = False, ""
        try:
            with rules_db.transaction() as rules_s:
                rules_s.execute(
                    _INSERT_CANDLE,
                    (
                        f"fake-forbidden-{uuid4().hex}",
                        _stream_key(),
                        _EXCHANGE,
                        MarketType.SPOT.value,
                        _SYMBOL,
                        _TIMEFRAME.value,
                        base_open,
                        base_open + _TF_MS,
                        Decimal("1"),
                        Decimal("1"),
                        Decimal("1"),
                        Decimal("1"),
                        Decimal("1"),
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            lanzo, msg = True, str(exc)
        check(
            f"INSERT rechazado por el MOTOR ({_primera_linea(msg)})",
            lanzo and "permission denied" in msg.lower(),
        )

        # ---- Mordida del check estatico (26/27/28 + 1 + 6) ----
        print("== Mordida del check D1: un check que no rompe no protege nada ==")
        for label, ok in _mordida_del_check():
            check(label, ok)
    finally:
        with mig_db.transaction() as m:
            if rule_id is not None:
                m.execute(
                    "DELETE FROM outbox WHERE stream_key = %s", (f"rule:{rule_id}",)
                )
                m.execute(
                    "DELETE FROM market_candle WHERE idempotency_key LIKE %s",
                    (f"fake-candle-{rule_id}-%",),
                )
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
        "RESUMEN: OK - test 22 demostrado: el motor evalua sobre el HISTORICO de "
        "market_candle (no sobre provisionales) y el estado avanza; el historico le es "
        "de solo lectura; y el check D1 muerde en los dos sentidos."
    )


def _fake_email() -> str:
    return f"fake-{uuid4().hex}@ejemplo.test"


def _estado(row: object) -> str:
    """Etiqueta del estado leido, o 'None' si la regla aun no tiene fila."""
    return "None" if row is None else str(getattr(row, "state", row))


def _primera_linea(mensaje: str) -> str:
    """La primera linea del error del motor (el resto es ruido del driver)."""
    lineas = mensaje.strip().splitlines()
    return lineas[0] if lineas else ""


if __name__ == "__main__":
    main()
