"""Composition root del worker de footprint (ADR-002, P07b 3b-1, CE-14).

PROCESO PROPIO, como el worker de reglas: consume las velas cerradas y corregidas del
bus y agrega el footprint de la barra. Aqui, y solo aqui, se cablean los adapters
concretos (PostgreSQL con el rol de INGESTA, Redis) con el motor de platform.

CE-14. Es un CONSUMIDOR del bus; NO toca el nucleo de ingesta (el IngestionEngine ni el
worker de ingesta). Corre bajo ce_v5_ingestion -- que ya tiene SELECT market_trade +
market_trade_gap e INSERT market_footprint + outbox (0017) -- porque el footprint es
market data que escribe el mismo poder que las velas (regla 5.20). NO usa el inbox:
ce_v5_ingestion no lo tiene, y no le hace falta -- la agregacion es IDEMPOTENTE por la
footprint_idempotency_key (PK de market_footprint + UNIQUE de la outbox), asi que un
poll+ack at-least-once con efecto idempotente basta (ADR-013).

UN TICK. Consume del topic "market" (poll+ack; filtra candle_closed/candle_corrected e
ignora candle_updated) y, tras consumir, DRENA su propia outbox para publicar los
market.footprint_* al bus. Las dos mitades en el mismo tick, como el ingestor y el motor
de reglas.

GUARDIA 5.20. IngestionDbConfig.from_env ABORTA si el entorno trae el DSN de la app, el
del operador o el de reglas. Este proceso solo porta la credencial de ingesta.

NADA DE BUCLES EN LA CONSTRUCCION: el bucle se arranca en __main__, no aqui.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from ce_v5.core.bus import BusMessage, DlqReason, EventBus
from ce_v5.core.clock import Clock, SystemClock
from ce_v5.infra.bus_redis import RedisBusConfig, RedisEventBus, create_client
from ce_v5.infra.db.config import DbConfig, IngestionDbConfig
from ce_v5.infra.db.market_footprint import PostgresFootprintWriter
from ce_v5.infra.db.market_trades import read_overlapping_gaps, read_trades_in_window
from ce_v5.infra.db.outbox_publisher import OutboxPublisher
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.platform.market.footprint_aggregate import TradeGap
from ce_v5.platform.market.footprint_ingestor import FootprintEngine
from source.families.footprint import MarketTrade
from source.families.market import (
    CandleClosedPayload,
    CandleCorrectedPayload,
    MarketCandleEventType,
)

# El topic del bus es la FAMILIA del evento (ADR-004): se consume "market" y se filtra
# por event_type. Grupo de consumo PROPIO: comparte el flujo de velas con el de reglas
# (otro grupo), pero cada grupo recibe TODAS las candle_closed de forma independiente.
MARKET_TOPIC = "market"
CONSUMER_GROUP = "ce_v5_footprint_aggregator"
COMPONENT_SOURCE = "worker_footprint"


@dataclass(frozen=True, slots=True)
class ConsumeResult:
    """Recuento de una pasada de consumo (observable)."""

    processed: int
    skipped: int
    dead_lettered: int


@dataclass(frozen=True, slots=True)
class _TradeReaderOnDb:
    """Adaptador de lectura: abre una transaccion por consulta y delega en los lectores
    de infra. Mantiene al motor lejos del ciclo de vida de la sesion (_CatalogOnDb).
    """

    database: PsycopgDatabase

    def trades_in_window(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        window_start: int,
        window_end: int,
    ) -> tuple[MarketTrade, ...]:
        with self.database.transaction() as session:
            return read_trades_in_window(
                session, exchange, market_type, symbol, window_start, window_end
            )

    def overlapping_gaps(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        window_start: int,
        window_end: int,
    ) -> tuple[TradeGap, ...]:
        with self.database.transaction() as session:
            return read_overlapping_gaps(
                session, exchange, market_type, symbol, window_start, window_end
            )


def _decode_closed(message: BusMessage) -> tuple[CandleClosedPayload, int] | None:
    """El payload de la vela CERRADA y su event_time, o None si no es de este grupo.

    Devolver None -- en vez de lanzar -- para un candle_updated es deliberado: no es un
    error, es un mensaje que a este consumidor no le toca; lanzar lo dejaria sin ACK
    hasta la DLQ, convirtiendo el flujo normal de provisionales en un incidente.
    """
    if message.event_type != MarketCandleEventType.CANDLE_CLOSED.value:
        return None
    envelope = json.loads(message.envelope)
    payload = CandleClosedPayload.model_validate(envelope["payload"])
    return payload, int(envelope["event_time"])


def _decode_corrected(
    message: BusMessage,
) -> tuple[CandleCorrectedPayload, int] | None:
    """El payload de una CORRECCION de vela y su event_time, o None si no es de aqui."""
    if message.event_type != MarketCandleEventType.CANDLE_CORRECTED.value:
        return None
    envelope = json.loads(message.envelope)
    payload = CandleCorrectedPayload.model_validate(envelope["payload"])
    return payload, int(envelope["event_time"])


def _handle(engine: FootprintEngine, message: BusMessage) -> bool:
    """Procesa un mensaje del topic market. True si era una vela de este consumidor."""
    closed = _decode_closed(message)
    if closed is not None:
        cerrada, event_time = closed
        engine.on_candle_closed(cerrada, event_time)
        return True
    corrected = _decode_corrected(message)
    if corrected is not None:
        correccion, event_time = corrected
        engine.on_candle_corrected(correccion, event_time)
        return True
    # candle_updated y demas: no es de este handler (y no es un error): se ACKea igual.
    return False


@dataclass(frozen=True, slots=True)
class FootprintContext:
    """Todo lo que el bucle del proceso necesita, y lo necesario para apagarlo."""

    engine: FootprintEngine
    publisher: OutboxPublisher
    database: PsycopgDatabase
    bus: EventBus
    consumer_name: str

    def close(self) -> None:
        """Cierra las conexiones. Idempotente: se puede llamar en el apagado limpio."""
        self.database.close()

    def consume_once(
        self,
        *,
        block_ms: int = 1000,
        batch_size: int = 100,
        min_idle_ms: int = 30_000,
        max_attempts: int = 5,
    ) -> ConsumeResult:
        """Una pasada de consumo del topic de mercado (poll+ack, sin inbox).

        RECLAMA los pendientes de un consumidor caido (claim_stale), consume nuevos
        (poll), y por cada uno: si paso max_attempts va a la DLQ; si el efecto falla NO
        se ACKea (queda pendiente, se reintenta); si va bien se ACKea. El efecto es
        IDEMPOTENTE (persist_and_enqueue dedupa por la idempotency_key), asi que una
        reentrega tras un fallo parcial no duplica el footprint.
        """
        self.bus.ensure_group(MARKET_TOPIC, CONSUMER_GROUP)
        stale = self.bus.claim_stale(
            MARKET_TOPIC,
            CONSUMER_GROUP,
            self.consumer_name,
            min_idle_ms=min_idle_ms,
            max_messages=batch_size,
        )
        fresh = self.bus.poll(
            MARKET_TOPIC,
            CONSUMER_GROUP,
            self.consumer_name,
            max_messages=batch_size,
            block_ms=block_ms,
        )
        processed = 0
        skipped = 0
        dead_lettered = 0
        for received in (*stale, *fresh):
            if received.delivery.delivery_count > max_attempts:
                self.bus.dead_letter(
                    received,
                    DlqReason(
                        reason_code="max_attempts_exceeded",
                        attempts=received.delivery.delivery_count,
                        detail="footprint_aggregator",
                    ),
                )
                dead_lettered += 1
                continue
            try:
                era_vela = _handle(self.engine, received.message)
            except Exception:  # noqa: BLE001 - un fallo del efecto no tumba el lote.
                # NO se ACKea: queda pendiente y se reintenta; pasado el tope -> DLQ.
                continue
            self.bus.ack(received.delivery)
            if era_vela:
                processed += 1
            else:
                skipped += 1
        return ConsumeResult(
            processed=processed, skipped=skipped, dead_lettered=dead_lettered
        )

    def drain_once(self) -> int:
        """Publica al bus lo que el motor dejo en la outbox (market.footprint_*)."""
        return self.publisher.drain_once()

    def tick(self, *, block_ms: int = 1000) -> tuple[ConsumeResult, int]:
        """Un ciclo completo: consumir velas y drenar los footprint producidos."""
        consumed = self.consume_once(block_ms=block_ms)
        published = self.drain_once()
        return consumed, published


def build_context(
    *,
    environ: Mapping[str, str] | None = None,
    consumer_name: str = "footprint-1",
    clock: Clock | None = None,
) -> FootprintContext:
    """Cablea el worker de footprint y devuelve el contexto. NO arranca ningun bucle.

    ``environ`` se inyecta para dar el entorno LIMPIO que tendria el worker real (SOLO
    su DSN de ingesta); el proceso real lo deja en None -> os.environ, con la misma
    guardia 5.20 que el worker de ingesta.
    """
    # GUARDIA 5.20: aborta si el entorno trae el DSN de app, operador o reglas.
    ingestion_dsn = IngestionDbConfig.from_env(environ).dsn
    database = PsycopgDatabase(DbConfig(dsn=ingestion_dsn))
    bus_config = RedisBusConfig.from_env(environ)
    bus = RedisEventBus(create_client(bus_config), bus_config)

    engine = FootprintEngine(
        reader=_TradeReaderOnDb(database),
        writer=PostgresFootprintWriter(database),
        clock=clock or SystemClock(),
        component_source=COMPONENT_SOURCE,
    )
    publisher = OutboxPublisher(db=database, bus=bus)

    return FootprintContext(
        engine=engine,
        publisher=publisher,
        database=database,
        bus=bus,
        consumer_name=consumer_name,
    )


__all__ = [
    "COMPONENT_SOURCE",
    "CONSUMER_GROUP",
    "MARKET_TOPIC",
    "ConsumeResult",
    "FootprintContext",
    "build_context",
]
