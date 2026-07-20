"""Persistencia del motor de reglas: autoria, descubrimiento y estado (P08).

Tres responsabilidades, cada una con su rol y su disciplina de scope:

- AUTORIA (insert_rule_definition, create_rule_with_intents, set_rule_enabled,
  delete_rule_with_intents): la escribe ce_v5_app bajo la sesion USER-DRIVEN
  (TenantScopedDatabase, que fija tenant Y user). Toda columna de scope se deriva del
  SERVIDOR (el tenant del CONTEXTO, exchange/symbol de market_scope, los
  evaluation_context de los grupos), NUNCA del JSON: el JSON de la regla se guarda tal
  cual en definition pero no decide identidad ni scope. La RLS WITH CHECK exige ademas
  que la columna tenant_id coincida con app_current_tenant_id().
  Una regla ACTIVA declara ademas su demanda de mercado como SubscriptionIntent
  (CA-P08-07 D2, ADR-014), en la MISMA transaccion que la regla: el invariante es
  "regla activa <=> sus intents existen".

- DESCUBRIMIENTO (discover_rules): lee por la ventanilla cross-tenant rules_for_market
  (SECURITY DEFINER), el UNICO acceso de ce_v5_rules a la autoria.

- ESTADO (record_transition): LA PRIMITIVA ATOMICA UNICA (CA-P08-02 p.2). En UNA sola
  transaccion, scopeada al tenant AUTORITATIVO por el camino SYSTEM-DRIVEN, hace UPSERT
  del RuntimeState completo (FSM + operacional) Y encola en la MISMA outbox los eventos
  de la transicion (cero o varios). Si algo falla, rollback de todo. Mismo patron que la
  escritura atomica de velas (market_candles.py, ADR-013).

Este modulo ejecuta SQL bajo la sesion recibida (como los demas repos de infra) y no
conoce el driver.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

from ce_v5.infra.db.market_store import PostgresIntentStore
from ce_v5.infra.db.outbox import OutboxEvent, enqueue_event
from ce_v5.infra.db.ports import Session
from ce_v5.infra.db.tenancy import SystemScopedDatabase, TenantScopedSession
from source.envelope import Envelope, EventPayload
from source.envelope.enums import Scope
from source.families.alert import AlertEventType, AlertRaisedPayload
from source.families.market import (
    IntentSourceType,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    StreamScope,
    SubscriptionIntent,
    Timeframe,
)
from source.families.registry import expected_event_schema_version
from source.families.rule import (
    EvaluationLifecycleState,
    EvaluationResult,
    ResolvedReason,
    RuleEvaluationCompletedPayload,
    RuleEventType,
    RuleFiringPayload,
    RuleQuarantinedPayload,
    RuleResolvedPayload,
)
from source.families.signal import SignalEventType, SignalRaisedPayload
from source.rules.market_rules import RULE_ADAPTER, AnyRule, RuleProduct

# Limite de last_technical_error: coincide con el CHECK de la 0014. Infra ACOTA al borde
# de su columna para que un diagnostico largo nunca rompa el commit atomico del estado.
_MAX_TECH_ERROR_LEN = 500

# Origen (envelope.source) de los eventos del motor de reglas (ADR-003).
_ENGINE_SOURCE = "ce_v5_rules_engine"

# Tope de intents (= contextos de evaluacion distintos) por regla. ESPEJO de
# MAX_GROUPS_PER_RULE de platform.rules.validator, que es la FUENTE DE VERDAD del
# presupuesto de admision: una regla no puede tener mas contextos distintos que grupos.
# Se duplica aqui porque infra NO importa platform (fronteras de capa, check 7.1) y
# porque este numero se traduce en CONEXIONES REALES a un exchange: el borde que las
# abre tiene que poder decir que no por si mismo, sin depender de que alguien ya haya
# validado antes. Si sube el presupuesto, sube tambien aqui.
MAX_INTENTS_PER_RULE = 5

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

# Activar/desactivar y borrar la AUTORIA. Filtran por tenant_id ADEMAS de la RLS
# (defensa en profundidad, ADR-011): si un dia fallara una policy, el filtro sigue en
# pie, y viceversa. Ninguna de las dos capas se apoya en la otra.
_SET_RULE_ENABLED_SQL = """
UPDATE rule_definition SET enabled = %s WHERE rule_id = %s AND tenant_id = %s
"""

_DELETE_RULE_SQL = """
DELETE FROM rule_definition WHERE rule_id = %s AND tenant_id = %s
"""

_DISCOVER_SQL = """
SELECT rule_id, tenant_id, product, canonical_rule_hash, schema_version, definition
FROM rules_for_market(%s, %s, %s)
"""

# Se lee la fila ENTERA, no solo la FSM: el motor reconstruye su RuntimeState COMPLETO
# entre ticks. Leer solo el estado de la FSM reiniciaria not_evaluable_count y
# consecutive_exceptions en cada vela, y entonces STALE (D3) y la cuarentena por
# excepciones repetidas NO DISPARARIAN NUNCA: los umbrales cuentan velas CONSECUTIVAS y
# el contador jamas llegaria a 2. Las columnas operacionales las anadio la 0014.
_READ_STATE_SQL = """
SELECT rule_id, tenant_id, state, last_evaluated_open_time,
       not_evaluable_count, consecutive_exceptions, is_stale, stale_reason,
       is_quarantined, quarantine_reason, last_technical_error
FROM rule_lifecycle_state
WHERE rule_id = %s
"""

# UPSERT del estado: la PK es rule_id, asi que un segundo tick actualiza la misma fila.
# tenant_id se estampa en el INSERT y no se toca en el UPDATE (la fila no cambia de
# tenant). La RLS WITH CHECK exige que ese tenant_id coincida con el tenant fijado. Se
# escribe el RuntimeState COMPLETO (FSM + estado operacional de CA-P08-05); las columnas
# operacionales las anadio la 0014.
_UPSERT_STATE_SQL = """
INSERT INTO rule_lifecycle_state (
    rule_id, tenant_id, state, last_evaluated_open_time,
    not_evaluable_count, consecutive_exceptions, is_stale, stale_reason,
    is_quarantined, quarantine_reason, last_technical_error, updated_at
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now()
)
ON CONFLICT (rule_id) DO UPDATE SET
    state = EXCLUDED.state,
    last_evaluated_open_time = EXCLUDED.last_evaluated_open_time,
    not_evaluable_count = EXCLUDED.not_evaluable_count,
    consecutive_exceptions = EXCLUDED.consecutive_exceptions,
    is_stale = EXCLUDED.is_stale,
    stale_reason = EXCLUDED.stale_reason,
    is_quarantined = EXCLUDED.is_quarantined,
    quarantine_reason = EXCLUDED.quarantine_reason,
    last_technical_error = EXCLUDED.last_technical_error,
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
class LifecycleOperational:
    """Estado OPERACIONAL de una regla para persistir (espejo de las columnas 0014).

    Escalares planos A PROPOSITO: infra NO importa platform (fronteras de capa, check
    7.1); el llamador (que conoce el RuntimeState) mapea aqui, convirtiendo los enums de
    motivo a su .value (o None). last_technical_error se acota al borde de la columna en
    record_transition (nunca un secreto: no debe llegar).
    """

    not_evaluable_count: int
    consecutive_exceptions: int
    is_stale: bool
    stale_reason: str | None
    is_quarantined: bool
    quarantine_reason: str | None
    last_technical_error: str | None


@dataclass(frozen=True, slots=True)
class RuleLifecycleState:
    """Estado del ciclo de una regla (fila COMPLETA de rule_lifecycle_state).

    Lleva la FSM (state / last_evaluated_open_time) Y el estado OPERACIONAL, en el MISMO
    carrier de escalares que consume record_transition: lo que se lee y lo que se
    escribe tienen la misma forma, asi que el motor reconstruye entre ticks exactamente
    lo que persistio. Los enums los reconstruye el llamador (entrypoints), que si conoce
    platform; infra se queda en escalares (fronteras de capa, check 7.1).
    """

    rule_id: UUID
    tenant_id: UUID
    state: str
    last_evaluated_open_time: int | None
    operational: LifecycleOperational


def _clamp_tech_error(value: str | None) -> str | None:
    """Acota el diagnostico tecnico al limite de la columna (0014, CHECK <= 500).

    Truncar el TEXTO nunca debe romper el commit atomico del estado: mejor un
    diagnostico recortado que perder el estado por longitud. Jamas debe llegar un
    secreto aqui; si llegara, tampoco se persiste completo.
    """
    if value is None or len(value) <= _MAX_TECH_ERROR_LEN:
        return value
    return value[:_MAX_TECH_ERROR_LEN]


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


def _as_text(value: object) -> str | None:
    """Columna de texto NULLABLE (los motivos y el diagnostico): str o None."""
    return None if value is None else str(value)


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


# --- AUTORIA + SubscriptionIntent (CA-P08-07 D2) -----------------------------
# ADR-014 dice que la demanda de suscripcion es DECLARATIVA: una regla activa DECLARA
# que necesita su flujo, y el ref-count del subscription manager se RECONSTRUYE desde
# esos intereses persistidos. Por eso la regla y sus intents se escriben en la MISMA
# transaccion: el invariante es "regla activa <=> sus intents existen", y una
# transaccion es la unica forma de que no pueda romperse. Si fueran dos escrituras
# separadas, un fallo entre medias dejaria o una regla activa que nadie alimenta (nunca
# dispararia, en silencio) o un intent zombie manteniendo viva una conexion al exchange
# para una regla que ya no existe.
#
# EL CICLO VA POR enabled, NO POR SALUD (D2). Una regla en CUARENTENA sigue siendo una
# regla ACTIVA: sus intents SE MANTIENEN. Apagar el stream por la cuarentena de UNA
# regla seria doblemente erroneo -- el stream es COMPARTIDO (otras reglas y otros
# tenants pueden depender de el, ADR-014) y el rearme del usuario debe ser INMEDIATO,
# no esperar a que se rehidrate una suscripcion. Solo enabled=false y el borrado retiran
# intents.


def _intent_source_type(product: RuleProduct) -> IntentSourceType:
    """El origen del interes, segun el producto de la regla (taxonomia ADR-014)."""
    if product is RuleProduct.ALERT:
        return IntentSourceType.ALERT_RULE
    return IntentSourceType.TRADING_SIGNAL_RULE


def rule_stream_keys(rule: AnyRule) -> list[MarketStreamKey]:
    """Un MarketStreamKey por evaluation_context DISTINTO de la regla (ADR-014).

    La regla vive en UN mercado (market_scope) pero puede evaluar en VARIOS timeframes
    (un grupo por contexto), y cada timeframe es un flujo DISTINTO: una regla con grupos
    en 1h y 4h necesita las dos suscripciones. Se derivan de los grupos, ordenadas y sin
    repetir: dos grupos en el mismo timeframe son UN interes, no dos (el UNIQUE de la
    tabla lo exigiria de todas formas).

    ACOTADO, no ilimitado: los contextos distintos no pueden superar el numero de grupos
    y el presupuesto de admision ya lo limita (MAX_GROUPS_PER_RULE en
    platform.rules.validator). Se reafirma aqui como defensa en profundidad porque infra
    NO importa platform (check 7.1) y porque el numero de intents es lo que se traduce
    en conexiones reales al exchange: es un limite de recurso, no un detalle.
    """
    contexts = sorted({group.evaluation_context for group in rule.groups})
    if len(contexts) > MAX_INTENTS_PER_RULE:
        msg = (
            f"la regla {rule.rule_id} declara {len(contexts)} contextos de evaluacion "
            f"distintos y el maximo es {MAX_INTENTS_PER_RULE}: cada contexto es una "
            "suscripcion real a un exchange, no se abren sin limite (ADR-014)."
        )
        raise ValueError(msg)
    return [
        MarketStreamKey(
            exchange=rule.market_scope.exchange,
            market_type=MarketType.SPOT,
            symbol=rule.market_scope.symbol,
            data_kind=MarketDataKind.CANDLES,
            timeframe=Timeframe(context),
        )
        for context in contexts
    ]


def _intents_for_rule(
    rule: AnyRule, tenant_id: UUID, user_id: UUID, now_ms: int
) -> list[SubscriptionIntent]:
    """Los SubscriptionIntent que declara una regla activa (uno por contexto).

    expires_at=None a proposito: el interes de una regla es PERSISTENTE. Un widget
    caduca cuando el usuario cierra el navegador; una alerta tiene que seguir viva
    aunque nadie este mirando -- justamente para eso existe.
    """
    source_type = _intent_source_type(rule.product)
    return [
        SubscriptionIntent(
            intent_id=uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            stream_scope=StreamScope.PUBLIC_MARKET,
            stream_key=stream_key,
            source_type=source_type,
            source_ref=str(rule.rule_id),
            expires_at=None,
            created_at=now_ms,
            updated_at=now_ms,
        )
        for stream_key in rule_stream_keys(rule)
    ]


def create_rule_with_intents(
    scoped: TenantScopedSession,
    rule: AnyRule,
    canonical_hash: str,
    now_ms: int,
) -> int:
    """Autora una regla Y declara sus intents, ATOMICAMENTE. Devuelve cuantos intents.

    ATOMICIDAD POR CONSTRUCCION: ambas escrituras ocurren en la transaccion que ABRE EL
    LLAMADOR (TenantScopedDatabase.transaction, que fija tenant Y user -- lo que la RLS
    user-scoped de market_subscription_intent exige). No se abre una transaccion aqui
    dentro: si este repo abriera la suya, la regla y sus intents podrian confirmarse por
    separado y el invariante "regla activa <=> sus intents existen" dejaria de estar
    garantizado por el motor.

    Una regla creada con enabled=false NO declara intents: no hay nada que alimentar.
    """
    insert_rule_definition(scoped, rule, canonical_hash)
    if not rule.enabled:
        return 0
    context = scoped.context
    store = PostgresIntentStore(scoped)
    intents = _intents_for_rule(rule, context.tenant_id, context.user_id, now_ms)
    for intent in intents:
        store.insert(intent)
    return len(intents)


def set_rule_enabled(
    scoped: TenantScopedSession, rule: AnyRule, *, enabled: bool, now_ms: int
) -> int:
    """Activa o desactiva una regla y sincroniza sus intents en la MISMA transaccion.

    enabled=False retira los intents (el flujo deja de tener quien lo pida por esta
    regla); enabled=True los vuelve a declarar. Devuelve cuantos intents quedan.

    NO se toca por SALUD: la cuarentena es estado OPERACIONAL del motor y no pasa por
    aqui. Una regla en cuarentena sigue enabled y conserva sus intents.
    """
    tenant_id = scoped.context.tenant_id
    scoped.session.execute(
        _SET_RULE_ENABLED_SQL, (enabled, str(rule.rule_id), str(tenant_id))
    )
    if enabled:
        store = PostgresIntentStore(scoped)
        intents = _intents_for_rule(rule, tenant_id, scoped.context.user_id, now_ms)
        for intent in intents:
            store.insert(intent)
        return len(intents)
    return -remove_rule_intents(scoped, rule)


def remove_rule_intents(scoped: TenantScopedSession, rule: AnyRule) -> int:
    """Retira TODOS los intents de una regla. Devuelve cuantos se borraron.

    Se borra por (source_type, source_ref, market_stream_key) -- la misma identidad con
    la que se insertaron --, un DELETE por flujo declarado. Bajo RLS, un borrado
    dirigido a otro sujeto no ve la fila y devuelve 0: no falla, simplemente no la toca.
    """
    store = PostgresIntentStore(scoped)
    context = scoped.context
    source_type = _intent_source_type(rule.product)
    borrados = 0
    for stream_key in rule_stream_keys(rule):
        borrados += store.delete(
            context.tenant_id,
            context.user_id,
            source_type,
            str(rule.rule_id),
            stream_key.as_stream_key(),
        )
    return borrados


def delete_rule_with_intents(scoped: TenantScopedSession, rule: AnyRule) -> int:
    """Borra la regla Y retira sus intents, ATOMICAMENTE. Devuelve cuantos se retiraron.

    Mismo razonamiento que la creacion: si el borrado de la regla se confirmara sin el
    de sus intents, quedaria un intent ZOMBIE manteniendo viva una suscripcion al
    exchange para una regla que ya no existe -- y el ref-count reconstruido desde la
    tabla lo daria por bueno para siempre.
    """
    retirados = remove_rule_intents(scoped, rule)
    scoped.session.execute(
        _DELETE_RULE_SQL, (str(rule.rule_id), str(scoped.context.tenant_id))
    )
    return retirados


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
        operational=LifecycleOperational(
            not_evaluable_count=_as_int(row[4]),
            consecutive_exceptions=_as_int(row[5]),
            is_stale=bool(row[6]),
            stale_reason=_as_text(row[7]),
            is_quarantined=bool(row[8]),
            quarantine_reason=_as_text(row[9]),
            last_technical_error=_as_text(row[10]),
        ),
    )


def record_transition(
    scoped_db: SystemScopedDatabase,
    *,
    tenant_id: UUID,
    rule_id: UUID,
    new_state: str,
    last_evaluated_open_time: int | None,
    operational: LifecycleOperational,
    events: Sequence[OutboxEvent],
) -> None:
    """LA PRIMITIVA ATOMICA UNICA del estado del motor (CA-P08-02 p.2).

    En UNA sola transaccion, scopeada al tenant AUTORITATIVO recibido: hace UPSERT del
    RuntimeState COMPLETO (estado de la FSM + estado operacional de CA-P08-05) Y encola
    en la MISMA outbox los eventos de esta transicion (rule.*/signal.*/alert.*). Es el
    UNICO camino de escritura del estado. Si algo falla -- la RLS WITH CHECK sobre un
    tenant ajeno, una familia de evento prohibida por la policy de outbox, un motivo o
    un last_technical_error que viole su CHECK... --, la transaccion hace rollback y no
    queda ni estado ni evento (ADR-013).

    events es una SECUENCIA (no un evento suelto): una transicion a FIRING emite dos
    (evaluation_completed + firing) mas su proyeccion; una actualizacion operacional (ir
    a stale/quarantine, o un tick dedup) NO emite ninguno pero SI persiste el contador.
    Por eso puede venir vacia: el estado se escribe igual, atomicamente. La proyeccion
    signal.*/alert.* la construye la 6.4; aqui solo se persiste lo que llegue.

    tenant_id es autoritativo (fila de servidor). Como scopea y estampa la fila con el
    MISMO tenant, una sola llamada solo puede afectar a UN tenant: una transaccion nunca
    cruza tenants.
    """
    with scoped_db.transaction(tenant_id) as scoped:
        scoped.session.execute(
            _UPSERT_STATE_SQL,
            (
                str(rule_id),
                str(tenant_id),
                new_state,
                last_evaluated_open_time,
                operational.not_evaluable_count,
                operational.consecutive_exceptions,
                operational.is_stale,
                operational.stale_reason,
                operational.is_quarantined,
                operational.quarantine_reason,
                _clamp_tech_error(operational.last_technical_error),
            ),
        )
        for event in events:
            enqueue_event(scoped.session, event)


def build_quarantined_event(
    payload: RuleQuarantinedPayload,
    *,
    source: str,
    correlation_id: str,
    authoritative_tenant_id: UUID,
) -> OutboxEvent:
    """Construye el evento rule.quarantined desde su payload (CA-P08-06).

    SERVER-AUTHORITATIVE (p.2, precedente CA-P08-03): el tenant del ENVELOPE es el
    autoritativo (del servidor); se EXIGE que payload.tenant_id coincida, porque un
    tenant de payload divergente es duplicidad peligrosa (misma clase que el tenant del
    JSON de regla que cerro CA-P08-03). El envelope acota scope=tenant con tenant_id
    obligatorio. El evento es OPERACIONAL: no proyecta, no pasa por flanco; quien decide
    emitirlo (solo en is_quarantined false->true) es el runtime (platform.rules).

    No hace I/O: devuelve el OutboxEvent; record_transition lo encola en la misma
    transaccion que el estado (atomicidad, CA-P08-02).
    """
    if payload.tenant_id != authoritative_tenant_id:
        msg = (
            "rule.quarantined: payload.tenant_id no coincide con el tenant "
            f"autoritativo del envelope ({payload.tenant_id} != "
            f"{authoritative_tenant_id}); manda el tenant del servidor (CA-P08-06 p.2)."
        )
        raise ValueError(msg)
    event_type = RuleEventType.QUARANTINED.value
    envelope = Envelope[RuleQuarantinedPayload](
        event_type=event_type,
        event_schema_version=expected_event_schema_version(event_type),
        source=source,
        idempotency_key=f"{event_type}:{payload.rule_id}:{correlation_id}",
        stream_key=f"rule:{payload.rule_id}",
        scope=Scope.TENANT,
        tenant_id=str(authoritative_tenant_id),
        correlation_id=correlation_id,
        payload=payload,
    )
    return OutboxEvent(
        event_id=envelope.event_id,
        idempotency_key=envelope.idempotency_key,
        stream_key=envelope.stream_key,
        event_type=envelope.event_type,
        envelope=envelope.model_dump(mode="json"),
    )


# --- Constructores del ciclo de una regla (CA-P08-01, CA-P08-04 D6) ----------
# SERVER-AUTHORITATIVE: el tenant lo pasa el llamador (la columna, no el JSON de la
# regla; CA-P08-03) y de ese MISMO tenant se derivan el scope del envelope y el tenant
# del payload -- por construccion coinciden, no puede colarse un tenant divergente.
# IDEMPOTENCY CUALIFICADO por tenant/regla/vela/tipo: reprocesar una vela es idempotente
# (la outbox tiene idempotency_key UNIQUE) y no colisiona entre familias ni tenants.


@dataclass(frozen=True, slots=True)
class CorrectionMark:
    """Marca de que una emision viene de una CORRECCION de vela (CA-P08-08).

    causation_event_id es el event_id del market.candle_corrected que la origino: la
    cadena causal queda completa (candle_corrected -> rule.firing -> alert.raised) y
    auditable sin adivinar.

    correction_revision cualifica la idempotency_key. Es lo que impide que la emision
    por correccion COLISIONE con la del candle_closed original de esa MISMA vela: sin
    ella ambas compartirian clave, la outbox (idempotency_key UNIQUE) se tragaria la
    segunda en silencio y la correccion no llegaria nunca a publicarse. Dos revisiones
    distintas de la misma vela son dos hechos distintos y necesitan claves distintas.
    """

    causation_event_id: str
    correction_revision: int


def _build_rule_event[PayloadT: EventPayload](
    envelope_cls: type[Envelope[PayloadT]],
    *,
    event_type: str,
    payload: PayloadT,
    tenant_id: UUID,
    rule_id: UUID,
    open_time: int,
    causation_id: str | None = None,
    correction: CorrectionMark | None = None,
) -> OutboxEvent:
    """Envuelve un payload de ciclo en su envelope tenant-scoped y su OutboxEvent.

    envelope_cls es el generico CONCRETO (Envelope[RuleFiringPayload], ...), nunca el
    base Envelope[EventPayload]: pydantic serializa por el tipo DECLARADO, asi que un
    envelope parametrizado con la base volcaria payload={} y el evento viajaria VACIO a
    la outbox. Mismo patron que identity.py / ingestor.py / operator_admin.py.

    Con `correction`, la clave de idempotencia se cualifica con la revision y el
    causation por defecto es el evento de correccion. Un causation_id EXPLICITO tiene
    prioridad: la proyeccion debe anclarse SIEMPRE a su rule.firing (ADR-015), tambien
    cuando el firing lo disparo una correccion.
    """
    idempotency_key = f"{event_type}:{tenant_id}:{rule_id}:{open_time}"
    if correction is not None:
        idempotency_key = (
            f"{idempotency_key}:correction:{correction.correction_revision}"
        )
        if causation_id is None:
            causation_id = correction.causation_event_id
    envelope = envelope_cls(
        event_type=event_type,
        event_schema_version=expected_event_schema_version(event_type),
        source=_ENGINE_SOURCE,
        idempotency_key=idempotency_key,
        stream_key=f"rule:{rule_id}",
        scope=Scope.TENANT,
        tenant_id=str(tenant_id),
        event_time=open_time,
        correlation_id=f"rule:{rule_id}:{open_time}",
        causation_id=causation_id,
        payload=payload,
    )
    return OutboxEvent(
        event_id=envelope.event_id,
        idempotency_key=envelope.idempotency_key,
        stream_key=envelope.stream_key,
        event_type=envelope.event_type,
        envelope=envelope.model_dump(mode="json"),
    )


def build_evaluation_completed_event(
    *,
    rule_id: UUID,
    tenant_id: UUID,
    canonical_rule_hash: str,
    previous_state: EvaluationLifecycleState,
    new_state: EvaluationLifecycleState,
    result: EvaluationResult,
    reason_code: str,
    open_time: int,
    correction: CorrectionMark | None = None,
) -> OutboxEvent:
    """rule.evaluation_completed: el EvaluationResult granular de una transicion."""
    payload = RuleEvaluationCompletedPayload(
        rule_id=rule_id,
        tenant_id=tenant_id,
        canonical_rule_hash=canonical_rule_hash,
        previous_state=previous_state,
        new_state=new_state,
        result=result,
        reason_code=reason_code,
    )
    return _build_rule_event(
        Envelope[RuleEvaluationCompletedPayload],
        event_type=RuleEventType.EVALUATION_COMPLETED.value,
        payload=payload,
        tenant_id=tenant_id,
        rule_id=rule_id,
        open_time=open_time,
        correction=correction,
    )


def build_firing_event(
    *,
    rule_id: UUID,
    tenant_id: UUID,
    canonical_rule_hash: str,
    previous_state: EvaluationLifecycleState,
    open_time: int,
    correction: CorrectionMark | None = None,
) -> OutboxEvent:
    """rule.firing: flanco de subida. Su envelope.event_id es el ANCLA CAUSAL de la
    proyeccion (raised.causation_id = event_id(rule.firing), CA-P08-01 p.5)."""
    payload = RuleFiringPayload(
        rule_id=rule_id,
        tenant_id=tenant_id,
        canonical_rule_hash=canonical_rule_hash,
        previous_state=previous_state,
    )
    return _build_rule_event(
        Envelope[RuleFiringPayload],
        event_type=RuleEventType.FIRING.value,
        payload=payload,
        tenant_id=tenant_id,
        rule_id=rule_id,
        open_time=open_time,
        correction=correction,
    )


def build_resolved_event(
    *,
    rule_id: UUID,
    tenant_id: UUID,
    canonical_rule_hash: str,
    previous_state: EvaluationLifecycleState,
    resolved_reason: ResolvedReason,
    open_time: int,
    correction: CorrectionMark | None = None,
) -> OutboxEvent:
    """rule.resolved: flanco de bajada, con su motivo. NO proyecta (CA-P08-01 p.8)."""
    payload = RuleResolvedPayload(
        rule_id=rule_id,
        tenant_id=tenant_id,
        canonical_rule_hash=canonical_rule_hash,
        previous_state=previous_state,
        resolved_reason=resolved_reason,
    )
    return _build_rule_event(
        Envelope[RuleResolvedPayload],
        event_type=RuleEventType.RESOLVED.value,
        payload=payload,
        tenant_id=tenant_id,
        rule_id=rule_id,
        open_time=open_time,
        correction=correction,
    )


def build_projection_event(
    rule: AnyRule,
    *,
    tenant_id: UUID,
    canonical_rule_hash: str,
    firing_event_id: UUID,
    open_time: int,
    correction: CorrectionMark | None = None,
) -> OutboxEvent:
    """Proyeccion por PRODUCTO (CA-P08-04 D6): alert.raised (AlertRule) / signal.raised
    (TradingSignalRule). causation_id = event_id(rule.firing): NUNCA se emite saltandose
    rule.firing (ADR-015); el orquestador construye la proyeccion SOLO tras el firing y
    con su event_id como ancla. tenant_id es autoritativo (columna), no el JSON de la
    regla; exchange/symbol salen de market_scope y notification_policy_ref de la regla.
    """
    causation = str(firing_event_id)
    if rule.product is RuleProduct.ALERT:
        return _build_rule_event(
            Envelope[AlertRaisedPayload],
            event_type=AlertEventType.RAISED.value,
            payload=AlertRaisedPayload(
                alert_id=uuid4(),
                rule_id=rule.rule_id,
                tenant_id=tenant_id,
                canonical_rule_hash=canonical_rule_hash,
                exchange=rule.market_scope.exchange,
                symbol=rule.market_scope.symbol,
                notification_policy_ref=rule.notification_policy_ref,
            ),
            tenant_id=tenant_id,
            rule_id=rule.rule_id,
            open_time=open_time,
            causation_id=causation,
            correction=correction,
        )
    return _build_rule_event(
        Envelope[SignalRaisedPayload],
        event_type=SignalEventType.RAISED.value,
        payload=SignalRaisedPayload(
            signal_id=uuid4(),
            rule_id=rule.rule_id,
            tenant_id=tenant_id,
            canonical_rule_hash=canonical_rule_hash,
            exchange=rule.market_scope.exchange,
            symbol=rule.market_scope.symbol,
        ),
        tenant_id=tenant_id,
        rule_id=rule.rule_id,
        open_time=open_time,
        causation_id=causation,
        correction=correction,
    )
