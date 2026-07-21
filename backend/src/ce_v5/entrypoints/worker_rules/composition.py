"""Composition root del worker de reglas (ADR-002, DOC_ESTRUCTURA sec.6, P08 Bloque 7).

El worker de reglas es un PROCESO PROPIO: aqui, y solo aqui, se cablean los adapters
concretos (PostgreSQL con el rol de REGLAS, Redis) y se cose el ciclo que ya existe
(platform: evaluador + FSM pura; infra: ventanilla, ventana de cierres, primitiva
atomica). El resto del codigo depende de puertos.

GUARDIA DE ARRANQUE (regla 5.20): RulesDbConfig.from_env ABORTA si en el entorno aparece
el DSN de la aplicacion, el del operador o el de ingesta. El motor no porta credenciales
que su funcion no necesita, y quien lo hace cumplir es el CODIGO.

QUE HACE UN TICK. Consume del topic "market" por el EventBus (nunca la API nativa del
broker; REST-15) con InboxConsumer -- idempotencia real de consumidor via el inbox de
P02b -- y, tras consumir, drena su PROPIA outbox para publicar rule.*/signal.*/alert.*
al bus. Las dos mitades en el mismo tick, como el ingestor hace reconcile + drain.

SOLO VELAS CERRADAS. El handler procesa market.candle_closed y IGNORA
market.candle_updated: evaluar sobre una vela en formacion violaria el invariante
firmado en el dictamen P07-A (una provisional no es historia, puede cambiar y hasta
desaparecer; una senal emitida sobre ella seria un hecho inventado).

UNA TRANSACCION POR TENANT, JAMAS MEZCLADAS (CA-P08-03). La ventanilla devuelve reglas
de VARIOS tenants para el mismo mercado; cada una se procesa en su propia transaccion
system-driven scopeada a SU tenant. Ver la nota sobre idempotencia en _process_rules.

NADA DE BUCLES EN LA CONSTRUCCION: como la API y el ingestor, los bucles se arrancan en
__main__, no en build_context. Un hilo de fondo escondido en la construccion es un hilo
que los tests no controlan.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from ce_v5.core.bus import BusMessage, EventBus
from ce_v5.core.observability import log_event
from ce_v5.entrypoints.worker_rules.cycle import process_rule_cycle
from ce_v5.infra.bus_redis import RedisBusConfig, RedisEventBus, create_client
from ce_v5.infra.db.config import DbConfig, RulesDbConfig
from ce_v5.infra.db.inbox_consumer import ConsumeResult, Handler, InboxConsumer
from ce_v5.infra.db.market_candles import (
    read_close_window,
    read_last_closed_open_time,
)
from ce_v5.infra.db.outbox_publisher import OutboxPublisher
from ce_v5.infra.db.ports import Session
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.infra.db.rules import (
    CorrectionMark,
    DiscoveredRule,
    discover_rules,
    read_state,
)
from ce_v5.infra.db.tenancy import SystemScopedDatabase
from ce_v5.platform.rules.catalog import DataSourceCatalog
from ce_v5.platform.rules.compiler import CompilationError, ExecutionPlan, compile
from ce_v5.platform.rules.correction import (
    affected_window,
    correction_scope,
    is_within_window,
)
from ce_v5.platform.rules.evaluator import Series
from ce_v5.platform.rules.rawclose import market_close_declaration
from ce_v5.platform.rules.runtime import EvalOutcome, RuntimeState, StaleReason
from source.families.market import (
    CandleClosedPayload,
    CandleCorrectedPayload,
    MarketCandleEventType,
)
from source.families.rule import EvaluationLifecycleState, QuarantineReason
from source.rules.market_rules import RULE_ADAPTER, AnyRule

# El topic del bus se deriva de la FAMILIA del evento (ADR-004, topic_for): el motor se
# suscribe a "market" y filtra por event_type dentro del handler.
MARKET_TOPIC = "market"

# Grupo de consumo del motor. El estado de ACK lo lleva el bus por GRUPO, no por
# proceso: varias replicas del worker comparten grupo y se reparten la carga.
CONSUMER_GROUP = "ce_v5_rules_engine"
HANDLER_NAME = "on_market_candle"


@dataclass(frozen=True, slots=True)
class RulesContext:
    """Todo lo que el bucle del proceso necesita, y lo necesario para apagarlo."""

    consumer: InboxConsumer
    publisher: OutboxPublisher
    scoped_db: SystemScopedDatabase
    catalog: DataSourceCatalog
    database: PsycopgDatabase
    bus: EventBus
    consumer_name: str

    def close(self) -> None:
        """Cierra las conexiones. Idempotente: se llama en el apagado limpio."""
        self.database.close()

    def consume_once(self, *, block_ms: int = 1000) -> ConsumeResult:
        """Una pasada de consumo del topic de mercado."""
        return self.consumer.run_once(
            MARKET_TOPIC, self.consumer_name, block_ms=block_ms
        )

    def drain_once(self) -> int:
        """Publica al bus lo que el ciclo dejo en la outbox (rule./signal./alert.)."""
        return self.publisher.drain_once()

    def tick(self, *, block_ms: int = 1000) -> tuple[ConsumeResult, int]:
        """Un ciclo completo del worker: consumir mercado y drenar lo producido."""
        consumed = self.consume_once(block_ms=block_ms)
        published = self.drain_once()
        return consumed, published


def build_catalog() -> DataSourceCatalog:
    """El catalogo de DataSources del motor: en v5.0, market.close (ADR-008).

    Los cuatro indicadores y el catalogo de paridad-v4 NO son P08 (los disena I-02):
    market.close es la demostracion del marco. validate() comprueba que el grafo de
    derivacion esta completo y es aciclico ANTES de compilar nada.
    """
    catalog = DataSourceCatalog()
    catalog.register(market_close_declaration())
    catalog.validate()
    return catalog


def _decode_closed(message: BusMessage) -> CandleClosedPayload | None:
    """El payload de una vela CERRADA, o None si el mensaje no es de este handler.

    Devolver None (en vez de lanzar) es deliberado: un market.candle_updated en el topic
    NO es un error, es un mensaje que a este consumidor no le toca. Lanzar lo dejaria
    sin ACK y lo reintentaria hasta la DLQ, convirtiendo el flujo NORMAL de velas
    provisionales en un incidente operativo.
    """
    if message.event_type != MarketCandleEventType.CANDLE_CLOSED.value:
        return None
    envelope = json.loads(message.envelope)
    return CandleClosedPayload.model_validate(envelope["payload"])


def _decode_corrected(
    message: BusMessage,
) -> tuple[CandleCorrectedPayload, str] | None:
    """El payload de una CORRECCION y el event_id del sobre, o None si no es de aqui.

    El event_id sale del ENVELOPE (no del BusMessage.event_id) porque es el que viaja en
    la cadena causal del bus y el que quedara como causation_id de lo que emitamos. Si
    el sobre no lo trae, se cae a la identidad de transporte antes que perder el ancla.
    """
    if message.event_type != MarketCandleEventType.CANDLE_CORRECTED.value:
        return None
    envelope = json.loads(message.envelope)
    payload = CandleCorrectedPayload.model_validate(envelope["payload"])
    event_id = str(envelope.get("event_id") or message.event_id)
    return payload, event_id


def _series_for(
    session: Session, plan: ExecutionPlan, timeframe: str, open_time: int
) -> Series:
    """Materializa la serie de cada fuente del plan desde el historico de velas.

    El plan dimensiona la historia POR FUENTE (ResolvedSource.history_bars: el maximo de
    barras que pide entre todos sus usos en la regla). En v5.0 la unica fuente servible
    es market.close, y su serie es la ventana de cierres del flujo de la regla.
    """
    return {
        source.source_id: read_close_window(
            session,
            plan.exchange,
            plan.symbol,
            timeframe,
            open_time,
            source.history_bars,
        )
        for source in plan.resolved_sources
    }


def _previous_state(session: Session, rule_id: UUID) -> RuntimeState:
    """El RuntimeState COMPLETO previo de la regla; INACTIVE si aun no tiene fila.

    Reconstruye la fila ENTERA de rule_lifecycle_state, no solo la FSM: los contadores
    (not_evaluable_count / consecutive_exceptions) y las banderas operacionales tienen
    que SOBREVIVIR entre ticks. Si aqui se devolviera solo el eval_state, cada vela
    empezaria con los contadores a cero y los umbrales de CA-P08-05 -- que cuentan velas
    CONSECUTIVAS -- nunca se alcanzarian: STALE (D3) y la cuarentena por excepciones
    repetidas serian codigo inalcanzable en el worker real, por muy correcta que fuese
    la funcion pura.

    Aqui es donde los escalares de infra vuelven a ser ENUMS: entrypoints si conoce
    platform, asi que la reconstruccion del tipo vive en esta capa y no en el repo.
    """
    row = read_state(session, rule_id)
    if row is None:
        return RuntimeState(EvaluationLifecycleState.INACTIVE)
    op = row.operational
    return RuntimeState(
        eval_state=EvaluationLifecycleState(row.state),
        not_evaluable_count=op.not_evaluable_count,
        consecutive_exceptions=op.consecutive_exceptions,
        is_stale=op.is_stale,
        stale_reason=None if op.stale_reason is None else StaleReason(op.stale_reason),
        is_quarantined=op.is_quarantined,
        quarantine_reason=(
            None
            if op.quarantine_reason is None
            else QuarantineReason(op.quarantine_reason)
        ),
        last_technical_error=op.last_technical_error,
    )


def _process_one(
    scoped_db: SystemScopedDatabase,
    catalog: DataSourceCatalog,
    discovered: DiscoveredRule,
    timeframe: str,
    open_time: int,
) -> None:
    """Procesa UNA regla de UN tenant contra UNA vela, en su propia transaccion.

    tenant_id y rule_id son AUTORITATIVOS: salen de la COLUMNA que devuelve la
    ventanilla, nunca del JSON de la definicion (CA-P08-03 p.9).

    Si el plan NO COMPILA se procesa igualmente, con EvalOutcome.compilation_error: eso
    es lo que lleva la regla a CUARENTENA y cierra la obligacion "plan no recomputable
    -> QUARANTINED" (ADR-017 / Bloque 6). Tragarse el fallo y saltar la regla dejaria
    una regla rota evaluando en silencio para siempre.
    """
    rule: AnyRule = RULE_ADAPTER.validate_python(discovered.definition)
    plan: ExecutionPlan | None = None
    outcome_override: EvalOutcome | None = None
    try:
        plan = compile(rule, catalog)
    except CompilationError as exc:
        outcome_override = EvalOutcome.compilation_error(str(exc))

    with scoped_db.transaction(discovered.tenant_id) as scoped:
        prev = _previous_state(scoped.session, discovered.rule_id)
        if plan is None:
            data: Series = {}
        else:
            data = _series_for(scoped.session, plan, timeframe, open_time)
    process_rule_cycle(
        scoped_db,
        rule,
        plan,
        data,
        prev,
        open_time,
        tenant_id=discovered.tenant_id,
        rule_id=discovered.rule_id,
        outcome_override=outcome_override,
    )


def _correct_one(
    scoped_db: SystemScopedDatabase,
    catalog: DataSourceCatalog,
    discovered: DiscoveredRule,
    payload: CandleCorrectedPayload,
    corrected_event_id: str,
) -> None:
    """Propaga una CORRECCION de vela a UNA regla, si su plan lo admite (CA-P08-08).

    Cuatro decisiones, en este orden:

    1. GUARDIA DURA. Si alguna fuente del plan no es POINT_LOCAL, NO se propaga: se
       registra el motivo (fuente + memory_model) y se sigue. La regla NO se cuarentena
       -- no esta rota, es el motor el que aun no sabe corregir esa clase de fuente -- y
       sigue evaluando con normalidad cada candle_closed. Abstenerse es la respuesta
       correcta: recalcular una ventana sobre una EMA daria un numero equivocado con
       aspecto de correcto.

    2. VENTANA. h = max history_bars de las fuentes; la correccion de T invalida las
       evaluaciones de [T, T+(h-1) barras].

    3. ?AFECTA AL ESTADO VIGENTE? Solo si L (la ultima vela madura) cae dentro de esa
       ventana. Si L ya quedo fuera, el estado actual NO se calculo con el dato
       corregido: no hay transicion retroactiva, cero eventos, estado intacto. Reevaluar
       una vela ANTIGUA reescribiria historia que ya nadie mira.

    4. REEVALUAR EN L (no en T): el estado de la regla es el de la ultima vela, y
       read_close_window ya sirve el valor corregido (revision mas alta por open_time).

    Un plan que no compila NO se corrige: la correccion no es el camino por el que una
    regla entra en cuarentena (eso es candle_closed, que ya lo hace).
    """
    rule: AnyRule = RULE_ADAPTER.validate_python(discovered.definition)
    timeframe = payload.timeframe.value
    try:
        plan = compile(rule, catalog)
    except CompilationError:
        return

    scope = correction_scope(plan)
    if not scope.conformant:
        model = scope.blocking_memory_model
        log_event(
            "rules.correction_skipped_non_point_local",
            rule_id=str(discovered.rule_id),
            tenant_id=str(discovered.tenant_id),
            source_id=str(scope.blocking_source_id),
            memory_model=model.value if model is not None else "unknown",
            reason="v5.0 solo propaga correcciones a fuentes point-local (CA-P08-08)",
        )
        return

    window = affected_window(
        payload.open_time, scope.history_bars, payload.timeframe.duration_ms
    )
    with scoped_db.transaction(discovered.tenant_id) as scoped:
        last_open = read_last_closed_open_time(
            scoped.session, plan.exchange, plan.symbol, timeframe
        )
        if last_open is None or not is_within_window(last_open, window):
            return  # La correccion no alcanza al estado vigente: nada que rehacer.
        prev = _previous_state(scoped.session, discovered.rule_id)
        data = _series_for(scoped.session, plan, timeframe, last_open)

    # correction_revision es int por CONTRATO (CandleCorrectedPayload, CA-P08-09): el
    # payload no se pudo construir sin ella, asi que aqui alimenta la idempotency_key
    # directamente. Ya no hay guarda de None: el tipo la hace innecesaria (CE-8).
    process_rule_cycle(
        scoped_db,
        rule,
        plan,
        data,
        prev,
        last_open,
        tenant_id=discovered.tenant_id,
        rule_id=discovered.rule_id,
        correction=CorrectionMark(
            causation_event_id=corrected_event_id,
            correction_revision=payload.correction_revision,
        ),
    )


def _correct_rules(
    scoped_db: SystemScopedDatabase,
    catalog: DataSourceCatalog,
    session: Session,
    payload: CandleCorrectedPayload,
    corrected_event_id: str,
) -> None:
    """Descubre por la ventanilla y propaga la correccion a cada regla de SU tenant."""
    discovered = discover_rules(
        session, payload.exchange, payload.symbol, payload.timeframe.value
    )
    for rule in discovered:
        _correct_one(scoped_db, catalog, rule, payload, corrected_event_id)


def _process_rules(
    scoped_db: SystemScopedDatabase,
    catalog: DataSourceCatalog,
    session: Session,
    payload: CandleClosedPayload,
) -> None:
    """Descubre por la ventanilla y procesa cada regla en la transaccion de SU tenant.

    IDEMPOTENCIA ANTE REDELIVERY. El ciclo NO se ejecuta dentro de la transaccion del
    inbox: cada regla abre la suya (una por tenant, jamas mezcladas, CA-P08-03). Si el
    mensaje se reentrega tras un fallo parcial, lo que impide duplicar hechos son las
    CLAVES DE IDEMPOTENCIA CUALIFICADAS por tenant/regla/vela/tipo mas el UPSERT del
    estado (6.5), no una unica transaccion abarcadora: reprocesar la misma vela
    reconstruye las MISMAS claves y la outbox (idempotency_key UNIQUE) las absorbe.
    """
    timeframe = payload.timeframe.value
    discovered = discover_rules(session, payload.exchange, payload.symbol, timeframe)
    for rule in discovered:
        _process_one(scoped_db, catalog, rule, timeframe, payload.open_time)


def build_handler(
    scoped_db: SystemScopedDatabase, catalog: DataSourceCatalog
) -> Handler:
    """Construye el handler del InboxConsumer (on_candle_closed).

    La firma es la del puerto Handler: (Session, BusMessage) -> None. La Session que
    llega es la del INBOX (la que registro la idempotencia del consumidor); se usa para
    la lectura cross-tenant de la ventanilla, que no necesita contexto de tenant.
    """

    def on_market_candle(session: Session, message: BusMessage) -> None:
        closed = _decode_closed(message)
        if closed is not None:
            _process_rules(scoped_db, catalog, session, closed)
            return
        corrected = _decode_corrected(message)
        if corrected is not None:
            payload, event_id = corrected
            _correct_rules(scoped_db, catalog, session, payload, event_id)
            return
        # market.candle_updated y demas: no es de este handler (y no es un error).

    return on_market_candle


def build_context(
    *,
    environ: Mapping[str, str] | None = None,
    consumer_name: str = "rules-1",
) -> RulesContext:
    """Cablea el worker de reglas y devuelve el contexto. NO arranca ningun bucle.

    ``environ`` se inyecta para que un test pueda dar el entorno LIMPIO que tendria el
    worker real (SOLO su DSN de reglas). El proceso real lo deja en None -> os.environ,
    y sigue protegido por la misma guardia 5.20.
    """
    # GUARDIA 5.20 BIDIRECCIONAL: aborta (ForeignDsnInRulesError) si el entorno trae el
    # DSN de la aplicacion, el del operador o el de ingesta.
    rules_dsn = RulesDbConfig.from_env(environ).dsn
    database = PsycopgDatabase(DbConfig(dsn=rules_dsn))
    bus_config = RedisBusConfig.from_env(environ)
    bus = RedisEventBus(create_client(bus_config), bus_config)

    scoped_db = SystemScopedDatabase(database)
    catalog = build_catalog()
    handler = build_handler(scoped_db, catalog)

    consumer = InboxConsumer(
        db=database,
        bus=bus,
        handler=handler,
        consumer_group=CONSUMER_GROUP,
        handler_name=HANDLER_NAME,
    )
    publisher = OutboxPublisher(db=database, bus=bus)

    return RulesContext(
        consumer=consumer,
        publisher=publisher,
        scoped_db=scoped_db,
        catalog=catalog,
        database=database,
        bus=bus,
        consumer_name=consumer_name,
    )


__all__ = [
    "CONSUMER_GROUP",
    "HANDLER_NAME",
    "MARKET_TOPIC",
    "RulesContext",
    "build_catalog",
    "build_context",
    "build_handler",
]
