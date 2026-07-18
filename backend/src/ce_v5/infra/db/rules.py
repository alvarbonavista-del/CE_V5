"""Persistencia del motor de reglas: autoria, descubrimiento y estado (P08).

Tres responsabilidades, cada una con su rol y su disciplina de scope:

- AUTORIA (insert_rule_definition): la escribe ce_v5_app bajo la sesion USER-DRIVEN
  (TenantScopedDatabase). Toda columna de scope se deriva del SERVIDOR (el tenant del
  CONTEXTO, exchange/symbol de market_scope, los evaluation_context de los grupos),
  NUNCA del JSON: el JSON de la regla se guarda tal cual en definition pero no decide
  identidad ni scope. La RLS WITH CHECK exige ademas que la columna tenant_id coincida
  con app_current_tenant_id().

- DESCUBRIMIENTO (discover_rules): lee por la ventanilla cross-tenant rules_for_market
  (SECURITY DEFINER), el UNICO acceso de ce_v5_rules a la autoria.

- ESTADO (record_transition): LA PRIMITIVA ATOMICA UNICA (CA-P08-02 p.2). En UNA sola
  transaccion, scopeada al tenant AUTORITATIVO por el camino SYSTEM-DRIVEN, hace UPSERT
  del estado del ciclo Y encola el evento en la MISMA outbox. No hay camino que escriba
  el estado sin el evento ni el evento sin el estado; si algo falla, rollback de ambos.
  Mismo patron que la escritura atomica de velas (market_candles.py, ADR-013).

Este modulo ejecuta SQL bajo la sesion recibida (como los demas repos de infra) y no
conoce el driver.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from ce_v5.infra.db.outbox import OutboxEvent, enqueue_event
from ce_v5.infra.db.ports import Session
from ce_v5.infra.db.tenancy import SystemScopedDatabase, TenantScopedSession
from source.rules.market_rules import RULE_ADAPTER, AnyRule

# La autoria: toda columna de scope la fija el SERVIDOR; definition guarda el JSON
# canonico pero no decide identidad ni scope (eso es la columna).
_INSERT_RULE_SQL = """
INSERT INTO rule_definition (
    rule_id, tenant_id, exchange, symbol, evaluation_contexts, product, name,
    canonical_rule_hash, schema_version, enabled, definition
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
)
"""

_DISCOVER_SQL = """
SELECT rule_id, tenant_id, product, canonical_rule_hash, schema_version, definition
FROM rules_for_market(%s, %s, %s)
"""

_READ_STATE_SQL = """
SELECT rule_id, tenant_id, state, last_evaluated_open_time
FROM rule_lifecycle_state
WHERE rule_id = %s
"""

# UPSERT del estado: la PK es rule_id, asi que un segundo tick actualiza la misma fila.
# tenant_id se estampa en el INSERT y no se toca en el UPDATE (la fila no cambia de
# tenant). La RLS WITH CHECK exige que ese tenant_id coincida con el tenant fijado.
_UPSERT_STATE_SQL = """
INSERT INTO rule_lifecycle_state (
    rule_id, tenant_id, state, last_evaluated_open_time, updated_at
) VALUES (
    %s, %s, %s, %s, now()
)
ON CONFLICT (rule_id) DO UPDATE SET
    state = EXCLUDED.state,
    last_evaluated_open_time = EXCLUDED.last_evaluated_open_time,
    updated_at = now()
"""


@dataclass(frozen=True, slots=True)
class DiscoveredRule:
    """Una regla habilitada que la ventanilla devuelve para un mercado + timeframe."""

    rule_id: UUID
    tenant_id: UUID
    product: str
    canonical_rule_hash: str
    schema_version: int
    definition: dict[str, object]


@dataclass(frozen=True, slots=True)
class RuleLifecycleState:
    """Estado del ciclo de una regla (fila de rule_lifecycle_state)."""

    rule_id: UUID
    tenant_id: UUID
    state: str
    last_evaluated_open_time: int | None


def _as_uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        return UUID(value)
    msg = f"Se esperaba un uuid de la base y llego {type(value)!r}."
    raise TypeError(msg)


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"Se esperaba un entero de la base y llego {type(value)!r}."
        raise TypeError(msg)
    return value


def _as_json(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    msg = f"Se esperaba un objeto jsonb de la base y llego {type(value)!r}."
    raise TypeError(msg)


def insert_rule_definition(
    scoped: TenantScopedSession, rule: AnyRule, canonical_hash: str
) -> None:
    """Inserta la AUTORIA de una regla ya admitida bajo la sesion user-driven.

    Deriva TODA columna de scope del SERVIDOR, nunca del JSON: tenant_id sale del
    CONTEXTO de la sesion (la RLS WITH CHECK exige ademas que coincida con
    app_current_tenant_id()); exchange/symbol de market_scope; los evaluation_contexts
    de los grupos DISTINTOS. definition guarda el JSON canonico via RULE_ADAPTER, pero
    ese JSON NO decide identidad ni scope.

    canonical_hash es el hash de evaluacion (ADR-017) y se RECIBE ya calculado: el
    calculo vive en ce_v5.platform.rules (Bloque 1) y este repo de infra no importa
    platform (fronteras de capa, check 7.1). El llamador lo computa y lo pasa.
    """
    tenant_id = scoped.context.tenant_id
    contexts = sorted({group.evaluation_context for group in rule.groups})
    definition = RULE_ADAPTER.dump_json(rule).decode()
    scoped.session.execute(
        _INSERT_RULE_SQL,
        (
            str(rule.rule_id),
            str(tenant_id),
            rule.market_scope.exchange,
            rule.market_scope.symbol,
            contexts,
            rule.product.value,
            rule.name,
            canonical_hash,
            rule.schema_version,
            rule.enabled,
            definition,
        ),
    )


def discover_rules(
    session: Session, exchange: str, symbol: str, timeframe: str
) -> list[DiscoveredRule]:
    """Las reglas HABILITADAS de TODOS los tenants para un mercado + timeframe.

    Lee por la ventanilla cross-tenant rules_for_market (SECURITY DEFINER): el unico
    acceso de ce_v5_rules a la autoria. Nunca devuelve dato de sujeto.
    """
    rows = session.fetchall(_DISCOVER_SQL, (exchange, symbol, timeframe))
    return [
        DiscoveredRule(
            rule_id=_as_uuid(row[0]),
            tenant_id=_as_uuid(row[1]),
            product=str(row[2]),
            canonical_rule_hash=str(row[3]),
            schema_version=_as_int(row[4]),
            definition=_as_json(row[5]),
        )
        for row in rows
    ]


def read_state(session: Session, rule_id: UUID) -> RuleLifecycleState | None:
    """Lee la fila de rule_lifecycle_state de una regla, o None si no existe.

    Bajo RLS: solo ve el estado del tenant fijado en la sesion (fuera, cero filas).
    """
    row = session.fetchone(_READ_STATE_SQL, (str(rule_id),))
    if row is None:
        return None
    last = row[3]
    return RuleLifecycleState(
        rule_id=_as_uuid(row[0]),
        tenant_id=_as_uuid(row[1]),
        state=str(row[2]),
        last_evaluated_open_time=None if last is None else _as_int(last),
    )


def record_transition(
    scoped_db: SystemScopedDatabase,
    *,
    tenant_id: UUID,
    rule_id: UUID,
    new_state: str,
    last_evaluated_open_time: int | None,
    event: OutboxEvent,
) -> None:
    """LA PRIMITIVA ATOMICA UNICA del estado del motor (CA-P08-02 p.2).

    En UNA sola transaccion, scopeada al tenant AUTORITATIVO recibido: hace UPSERT del
    estado del ciclo Y encola el evento (rule.*/signal.*/alert.) en la MISMA outbox. Es
    el UNICO camino de escritura del estado: no expone forma de escribir el estado sin
    el evento ni el evento sin el estado. Si algo falla -- la RLS WITH CHECK sobre un
    tenant ajeno, una familia de evento prohibida por la policy de outbox, ... --, la
    transaccion hace rollback y no queda ni el estado ni el evento (ADR-013).

    tenant_id es autoritativo (fila de servidor). Como scopea y estampa la fila con el
    MISMO tenant, una sola llamada solo puede afectar a UN tenant: una transaccion nunca
    cruza tenants.
    """
    with scoped_db.transaction(tenant_id) as scoped:
        scoped.session.execute(
            _UPSERT_STATE_SQL,
            (str(rule_id), str(tenant_id), new_state, last_evaluated_open_time),
        )
        enqueue_event(scoped.session, event)
