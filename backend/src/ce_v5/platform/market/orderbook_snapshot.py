"""Motor de snapshot del libro L2 (P07c Tanda III; ADR-013/007, cond.1/3). SIN IO.

Produce los dos snapshots del top-K a partir del libro VIVO que mantiene el
OrderbookIngestionEngine:

- MUESTRA (kind='sample'): una foto intra-ventana a cadencia. is_complete = el del libro
  en ese instante. Se PERSISTE sin publicar (persist_sample), como los trades.

- FRONTERA (kind='frontier'): la foto AS-OF el cierre de una barra. is_complete = el del
  libro Y que NINGUNA discontinuidad solape [open_time, close_time) -- espejo EXACTO del
  is_complete del footprint (un hueco en la ventana => barra incompleta, cond.3,
  uniforme y reproducible). Se PUBLICA por outbox (persist_and_enqueue), como el
  footprint.

TOP-K: los K mejores niveles por lado (bids los de precio mas ALTO, asks los mas BAJO).
K, cadencia, ventana (as_of), formula_version y clock_source entran en la
idempotency/cache_key (cond.1 + refino de procedencia de Central, ya en el contrato); un
cambio semantico sube formula_version. El snapshot es CANON VIVO -- no hay replay --: la
clave registra COMO se capturo, no una receta. El libro completo vive en memoria
(OrderbookBook); aqui solo se recorta el top-K.

NO importa infra: depende de dos PUERTOS (writer y reader), que infra satisface por
FORMA. El recorte es PURO y se prueba en frio sin red ni base. El event_time lo fija el
ORIGEN del hecho (ADR-007): el cierre de la barra en el frontier, el instante de la
muestra en el sample; nuestro reloj solo sella ingestion/processing_time del sobre.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ce_v5.core.clock import Clock
from ce_v5.platform.market.orderbook_book import OrderbookBook
from ce_v5.platform.market.orderbook_writer import (
    OrderbookReaderPort,
    OrderbookWriterPort,
)
from source.envelope import Envelope
from source.envelope.enums import Scope
from source.families.market import MarketType, Timeframe
from source.families.orderbook import (
    MarketOrderbookEventType,
    MarketOrderbookSnapshotKind,
    OrderbookLevel,
    OrderbookSnapshotPayload,
)
from source.families.registry import expected_event_schema_version

# K por defecto (dictamen de Central: 25-50). El extremo bajo del rango: mas ligero de
# persistir y coherente con la profundidad de los topics (orderbook.200 de Bybit da de
# sobra para un top-25). Parametrizable si la medicion del paso 8 (cond.6) lo pide.
DEFAULT_DEPTH_K = 25
# Cadencia de muestreo por defecto (ms). Entra en la idempotency_key (cond.1): cambiarla
# produce OTRA serie de muestras, no pisa la anterior.
DEFAULT_CADENCE_MS = 1000
# formula_version del recorte top-K. Sube ante CUALQUIER cambio semantico (que un mismo
# libro produzca otro snapshot): reproducibilidad (cond.1).
DEFAULT_FORMULA_VERSION = 1
# Fuente del reloj que fecha la captura: 'system' en produccion (SystemClock),
# 'simulated' en backtest/tests (SimulatedClock). Entra en la idempotency_key
# (procedencia, refino de Central): dos capturas del mismo as_of por relojes distintos
# no colisionan.
DEFAULT_CLOCK_SOURCE = "system"


@dataclass(frozen=True, slots=True)
class OrderbookSnapshotConfig:
    """La CONFIG que hace el snapshot reproducible POR PROCEDENCIA (cond.1): entra en la
    cache_key. No hay replay -- el snapshot de orderbook es canon vivo tal como se
    guarda; la clave registra COMO se capturo, no una receta para re-derivarlo.
    """

    depth_k: int = DEFAULT_DEPTH_K
    cadence_ms: int = DEFAULT_CADENCE_MS
    formula_version: int = DEFAULT_FORMULA_VERSION
    clock_source: str = DEFAULT_CLOCK_SOURCE


DEFAULT_ORDERBOOK_SNAPSHOT_CONFIG = OrderbookSnapshotConfig()


@dataclass(slots=True)
class OrderbookSnapshotMetrics:
    """Observabilidad. Sin esto, un frontier que se publica siempre incompleto o una
    muestra que nunca entra son INVISIBLES.
    """

    samples_persisted: int = 0
    frontiers_published: int = 0
    incomplete_frontiers: int = 0
    duplicates_skipped: int = 0
    # TELEMETRIA (opcion B): fronteras EMITIDAS sobre un libro SIN semilla (o vaciado),
    # con is_complete=False y niveles vacios. NO es la senal -- la senal es el
    # is_complete=False publicado en el canon --; es solo para ver cuantas barras se
    # capturaron sin libro (un arranque largo con muchas asi delata una siembra que no
    # llega). Estas fronteras cuentan tambien en frontiers_published e incomplete.
    frontiers_unseeded: int = 0


def _top_k(
    levels: dict[Decimal, Decimal], depth_k: int, *, descending: bool
) -> tuple[OrderbookLevel, ...]:
    """Los K mejores niveles de un lado: bids por precio DESCENDENTE, asks ASCENDENTE.

    El orden lo exige el contrato (bids ↓, asks ↑) y es lo que hace el snapshot
    reproducible bit a bit: los mismos niveles producen SIEMPRE el mismo top-K.
    """
    mejores = sorted(levels, reverse=descending)[:depth_k]
    return tuple(OrderbookLevel(price=price, size=levels[price]) for price in mejores)


class OrderbookSnapshotEngine:
    """Recorta el top-K del libro vivo y lo persiste (sample) o publica (frontier)."""

    def __init__(
        self,
        writer: OrderbookWriterPort,
        reader: OrderbookReaderPort,
        clock: Clock,
        *,
        component_source: str,
        config: OrderbookSnapshotConfig = DEFAULT_ORDERBOOK_SNAPSHOT_CONFIG,
    ) -> None:
        self._writer = writer
        self._reader = reader
        self._clock = clock
        self._component_source = component_source
        self._config = config
        self.metrics = OrderbookSnapshotMetrics()

    def take_sample(
        self,
        book: OrderbookBook,
        *,
        timeframe: Timeframe,
        open_time: int,
        close_time: int,
        sample_time: int,
    ) -> bool:
        """Una MUESTRA intra-ventana del libro. is_complete = el del libro en ese
        instante.

        Se PERSISTE sin publicar (persist_sample). El event_time es el instante de la
        muestra (ADR-007). Devuelve False si ya estaba (dedup por su idempotency_key,
        que incluye sample_time). cond.3: una muestra tomada mientras el libro esta
        incompleto se persiste COMO incompleta.
        """
        payload = self._build(
            book,
            kind=MarketOrderbookSnapshotKind.SAMPLE,
            timeframe=timeframe,
            open_time=open_time,
            close_time=close_time,
            sample_time=sample_time,
            is_complete=book.is_complete,
        )
        escrita = self._writer.persist_sample(payload, event_time=sample_time)
        if escrita:
            self.metrics.samples_persisted += 1
        else:
            self.metrics.duplicates_skipped += 1
        return escrita

    def take_frontier(
        self,
        book: OrderbookBook,
        *,
        timeframe: Timeframe,
        open_time: int,
        close_time: int,
    ) -> bool:
        """La FRONTERA de la barra por RELOJ DE BARRA (opcion 3). is_complete FAIL-SAFE
        UNIFORME (cond.3).

        FIRE-ANYWAY HONESTO (cond.5, opcion B): el trigger llama a esto en CADA barra de
        un (symbol, tf) activo, aunque la vela sea plana. Un libro SIN semilla -- o
        vaciado -- NO se salta: EMITE su frontera con bids/asks VACIOS e is_complete=
        False. La incompletitud va EN EL CANON, no en una metrica: quien lea el frontier
        ve que esa barra no tenia libro (5.21 lo admite solo si is_complete=False).
        El conteo de estos casos queda como TELEMETRIA (frontiers_unseeded), no como la
        senal -- la senal es is_complete=False, publicada.

        is_complete = el libro esta completo AHORA **Y** ninguna discontinuidad (resync)
        solapa [open_time, close_time). Es el MISMO criterio que el footprint: un hueco
        dentro de la ventana marca la barra incompleta aunque el libro ya se recuperase.
        Un libro sin semilla nunca esta completo, asi que su frontera sale is_complete=
        False por el mismo camino, sin caso especial. Se PUBLICA por outbox
        (persist_and_enqueue). event_time = open_time (as_of de la barra, ADR-007; la
        frontera es la foto de ESA barra, cond.2), misma ancla que su idempotency_key.
        """
        exchange, market_type, symbol = _identidad(book)
        vacio = not book.bids() and not book.asks()
        solapes = self._reader.overlapping_discontinuities(
            exchange, market_type, symbol, open_time, close_time
        )
        is_complete = book.is_complete and not solapes
        payload = self._build(
            book,
            kind=MarketOrderbookSnapshotKind.FRONTIER,
            timeframe=timeframe,
            open_time=open_time,
            close_time=close_time,
            sample_time=None,
            is_complete=is_complete,
        )
        event = MarketOrderbookEventType.ORDERBOOK_FRONTIER
        envelope = self._envelope(payload, event, open_time)
        escrita = self._writer.persist_and_enqueue(
            envelope_json=envelope,
            payload=payload,
            event_type=event.value,
            stream_key=payload.stream_key(),
            idempotency_key=payload.idempotency_key(payload.kind),
            event_time=open_time,
        )
        if not escrita:
            self.metrics.duplicates_skipped += 1
            return False
        self.metrics.frontiers_published += 1
        if not is_complete:
            self.metrics.incomplete_frontiers += 1
        if not book.seeded or vacio:
            self.metrics.frontiers_unseeded += 1  # TELEMETRIA, no la senal.
        return True

    def _build(
        self,
        book: OrderbookBook,
        *,
        kind: MarketOrderbookSnapshotKind,
        timeframe: Timeframe,
        open_time: int,
        close_time: int,
        sample_time: int | None,
        is_complete: bool,
    ) -> OrderbookSnapshotPayload:
        exchange, market_type, symbol = _identidad(book)
        return OrderbookSnapshotPayload(
            exchange=exchange,
            market_type=MarketType(market_type),
            symbol=symbol,
            depth_k=self._config.depth_k,
            bids=_top_k(book.bids(), self._config.depth_k, descending=True),
            asks=_top_k(book.asks(), self._config.depth_k, descending=False),
            sequence=book.sequence,
            kind=kind,
            timeframe=timeframe,
            open_time=open_time,
            close_time=close_time,
            sample_time=sample_time,
            is_complete=is_complete,
            cadence_ms=self._config.cadence_ms,
            formula_version=self._config.formula_version,
            clock_source=self._config.clock_source,
        )

    def _envelope(
        self,
        payload: OrderbookSnapshotPayload,
        event: MarketOrderbookEventType,
        event_time: int,
    ) -> bytes:
        """El sobre canonico del frontier (ADR-003/007/011), espejo del footprint."""
        ahora = self._clock.now_ms()
        stream_key = payload.stream_key()
        envelope = Envelope[OrderbookSnapshotPayload](
            event_type=event.value,
            event_schema_version=expected_event_schema_version(event.value),
            source=self._component_source,
            idempotency_key=payload.idempotency_key(payload.kind),
            stream_key=stream_key,
            source_sequence=payload.sequence,
            scope=Scope.PUBLIC_MARKET,  # publico: sin tenant (ADR-011).
            event_time=event_time,  # el cierre de la barra (ADR-007), no nuestro reloj.
            ingestion_time=ahora,
            processing_time=ahora,
            correlation_id=stream_key,
            payload=payload,
        )
        return envelope.model_dump_json().encode()


def _identidad(book: OrderbookBook) -> tuple[str, str, str]:
    """La identidad del flujo del libro, o error si aun no se arranco.

    Un snapshot de un libro SIN foto no tiene sentido (no hay estado que fotografiar):
    es un fallo de cableado, no un dato. FAIL-LOUD.
    """
    exchange, market_type, symbol = book.exchange, book.market_type, book.symbol
    if exchange is None or market_type is None or symbol is None:
        msg = "no se puede fotografiar un libro sin foto de partida (sin sembrar)."
        raise ValueError(msg)
    return exchange, market_type, symbol
