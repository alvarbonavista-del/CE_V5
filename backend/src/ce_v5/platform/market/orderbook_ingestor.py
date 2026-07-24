"""Motor de ingesta del LIBRO L2 con estado (P07c Tanda III; ADR-014/013/007). SIN IO.

Gemelo de trade_ingestor.py, pero CON ESTADO y ORDER-DEPENDIENTE (esa es la diferencia
de fondo): mantiene un OrderbookBook vivo por stream, drena deltas del puerto con
backpressure y los aplica EN ORDEN. Lo que lo separa del de trades:

- ARRANCA DE UNA FOTO. Un stream suscrito sin libro se SIEMBRA (source.seed ->
  book.seed) antes de aplicarle deltas: sin la foto un delta no significa nada.

- RECONEXION = RE-SEMBRAR. Cuando el conector senala que un stream reconecto
  (drain_reconnected), el socket estuvo caido y se perdieron deltas: se pide una foto
  nueva y se RE-SIEMBRA. Como el hueco NO se ve por un delta (se re-siembra en vez de
  encadenar), se APUNTA la discontinuidad (record_discontinuity) para que el frontier de
  las barras solapadas salga incompleto (fail-safe, cond.3), sin publicar un evento.

- HUECO DETECTADO POR EL MOTOR = RESYNC PUBLICADO. Cuando el OrderbookBook detecta que
  la cadena de secuencias se rompio (resync_required), es un hecho del mercado: se
  PUBLICA market.orderbook_resynced por persist_and_enqueue (persiste la discontinuidad
  Y la encola atomico, ADR-013). Es su PROPIO hecho, no una candle_corrected. El
  is_complete del libro es FAIL-SAFE y lo lleva el Motor; aqui solo se orquesta.

FAULT ISOLATION POR STREAM: una foto corrupta, un delta podrido o un fallo de siembra se
cuentan y se saltan; jamas tumban el ciclo ni a los demas streams. NO importa infra:
depende del puerto de datos y del puerto de escritura, que infra satisface por FORMA.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ce_v5.core.clock import Clock
from ce_v5.platform.market.orderbook_book import (
    OrderbookBook,
    RawOrderbookRejected,
)
from ce_v5.platform.market.orderbook_source import OrderbookDataSourcePort
from ce_v5.platform.market.orderbook_writer import OrderbookWriterPort
from source.envelope import Envelope
from source.envelope.enums import Scope
from source.families.market import (
    MarketStreamKey,
    MarketType,
    RawOrderbookDelta,
)
from source.families.orderbook import (
    MarketOrderbookEventType,
    OrderbookResyncedPayload,
)
from source.families.registry import expected_event_schema_version

# Motivo de las discontinuidades que apunta el motor. Un resync detectado por el Motor
# (hueco en la cadena de secuencias) y una reconexion son discontinuidades distintas.
_REASON_GAP = "gap"
_REASON_RECONNECT = "reconnect"


def _stream_id(exchange: str, market_type: str, symbol: str) -> str:
    """La clave textual del flujo del libro (ADR-014), igual que MarketStreamKey."""
    return ":".join(["market", "orderbook", exchange, market_type, symbol])


@dataclass(frozen=True, slots=True)
class OrderbookIngestionConfig:
    """BACKPRESSURE: quien manda es el motor, no el exchange.

    max_batch acota los deltas por ciclo. Un libro liquido publica un torrente de
    actualizaciones; sin tope, un pico de volatilidad se vuelve una cola infinita en
    memoria. Lo que no cabe ESPERA en el feed al siguiente ciclo, no se pierde.
    """

    max_batch: int = 500
    poll_timeout_ms: int = 200


DEFAULT_ORDERBOOK_INGESTION_CONFIG = OrderbookIngestionConfig()


@dataclass(slots=True)
class OrderbookIngestionMetrics:
    """Observabilidad (insumo del paso 8): sin esto, un stream que solo re-sincroniza o
    un feed que solo manda basura son INVISIBLES.
    """

    deltas_applied: int = 0
    resyncs: int = 0
    reseeds: int = 0
    discontinuities_recorded: int = 0
    seed_errors: int = 0
    unsubscribed_dropped: int = 0
    unseeded_dropped: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    degraded_streams: set[str] = field(default_factory=set)


class OrderbookIngestionEngine:
    """Convierte el feed de deltas de un exchange en un libro vivo por stream."""

    def __init__(
        self,
        source: OrderbookDataSourcePort,
        writer: OrderbookWriterPort,
        clock: Clock,
        *,
        component_source: str,
        config: OrderbookIngestionConfig = DEFAULT_ORDERBOOK_INGESTION_CONFIG,
    ) -> None:
        self._source = source
        self._writer = writer
        self._clock = clock
        self._component_source = component_source
        self._config = config
        self._books: dict[str, OrderbookBook] = {}
        self.metrics = OrderbookIngestionMetrics()

    def book_for(self, stream_id: str) -> OrderbookBook | None:
        """El libro vivo de un stream (READ-ONLY para el motor de snapshot), o None.

        El motor de snapshot lo fotografia; NO lo muta (bids()/asks() del libro
        devuelven copias). La clave es la canonica del stream
        (market:orderbook:exchange:mkt:symbol).
        """
        return self._books.get(stream_id)

    def books(self) -> Mapping[str, OrderbookBook]:
        """Los libros vivos por stream (READ-ONLY): el cableado los muestrea a cadencia.

        Copia superficial: el diccionario no se muta fuera, y cada libro solo se lee
        (bids()/asks() devuelven copias). Solo aparecen los ya sembrados.
        """
        return dict(self._books)

    def drain_once(self) -> OrderbookIngestionMetrics:
        """Un ciclo: re-siembra reconectados, siembra nuevos, aplica deltas con
        backpressure.

        EL ORDEN IMPORTA: primero se re-siembran los reconectados y se siembran los
        nuevos (asegurar un libro de partida de fiar), y SOLO despues se drenan y
        aplican los deltas EN ORDEN. Polling antes de sembrar aplicaria deltas a un
        libro viejo que se va a descartar.
        """
        suscritas = {
            clave: MarketStreamKey.parse(clave) for clave in self._source.active()
        }
        self._reseed_reconectados(suscritas)
        self._sembrar_nuevos(suscritas)

        procesados = 0
        while procesados < self._config.max_batch:
            crudos = self._source.poll_deltas(self._config.poll_timeout_ms)
            if not crudos:
                break
            for raw in crudos:
                self._procesar(raw, suscritas)
                procesados += 1
        return self.metrics

    def _sembrar_nuevos(self, suscritas: Mapping[str, MarketStreamKey]) -> None:
        """Cada stream suscrito sin libro se arranca con su foto. Fault isolation."""
        for clave, key in suscritas.items():
            if clave not in self._books:
                self._sembrar(key, clave)

    def _reseed_reconectados(self, suscritas: Mapping[str, MarketStreamKey]) -> None:
        """Cada stream reconectado se RE-SIEMBRA y su discontinuidad se APUNTA (no
        publica).

        Un reconectado ya tenia libro: el socket cayo y volvio, y pudo perder deltas. Se
        guarda la ultima secuencia buena, se pide una foto nueva y se re-siembra; si
        habia libro previo, se apunta la discontinuidad (record_discontinuity) para que
        el frontier de las barras solapadas salga incompleto. FAULT ISOLATION: una clave
        corrupta o un fallo de siembra se cuentan y se saltan.
        """
        for clave in self._source.drain_reconnected():
            try:
                key = suscritas.get(clave) or MarketStreamKey.parse(clave)
            except ValueError:
                self.metrics.seed_errors += 1
                continue
            previo = self._books.get(clave)
            from_seq = previo.sequence if previo is not None and previo.seeded else None
            if not self._sembrar(key, clave):
                continue
            self.metrics.reseeds += 1
            if from_seq is not None:
                to_seq = self._books[clave].sequence
                self._registrar_discontinuidad(key, clave, from_seq, to_seq)

    def _sembrar(self, key: MarketStreamKey, clave: str) -> bool:
        """Pide la foto al puerto y (re)arranca el libro. False si fallo (aislado)."""
        try:
            raw = self._source.seed(key)
        except Exception:  # noqa: BLE001 - un seed fallido no tumba el ciclo.
            self.metrics.seed_errors += 1
            self.metrics.degraded_streams.add(clave)
            return False
        book = self._books.get(clave) or OrderbookBook()
        try:
            book.seed(raw)
        except RawOrderbookRejected as rechazo:
            motivo = rechazo.reason.value
            self.metrics.rejected[motivo] = self.metrics.rejected.get(motivo, 0) + 1
            self.metrics.degraded_streams.add(clave)
            return False
        self._books[clave] = book
        return True

    def _procesar(
        self, raw: RawOrderbookDelta, suscritas: Mapping[str, MarketStreamKey]
    ) -> None:
        clave = _stream_id(raw.exchange, raw.market_type, raw.symbol)
        if clave not in suscritas:
            # Nadie pidio este flujo: un dato que nadie quiere no entra en el libro.
            self.metrics.unsubscribed_dropped += 1
            return
        book = self._books.get(clave)
        if book is None:
            # Suscrito pero sin foto (la siembra fallo): un delta sin libro no significa
            # nada. Se cuenta y se salta; el proximo ciclo reintenta la siembra.
            self.metrics.unseeded_dropped += 1
            return

        estaba_completo = not book.resync_required
        try:
            book.apply(raw)
        except RawOrderbookRejected as rechazo:
            # AISLAMIENTO POR STREAM: un delta podrido de BTC no impide procesar el de
            # ETH.
            motivo = rechazo.reason.value
            self.metrics.rejected[motivo] = self.metrics.rejected.get(motivo, 0) + 1
            self.metrics.degraded_streams.add(clave)
            return

        self.metrics.deltas_applied += 1
        # El hueco acaba de aparecer (transicion completo -> resync): se publica UNA
        # vez. Si ya estaba en resync, el Motor ignora el delta y no se re-publica.
        if book.resync_required and estaba_completo:
            self._publicar_resync(book, clave)

    def _registrar_discontinuidad(
        self, key: MarketStreamKey, clave: str, from_seq: int, to_seq: int
    ) -> None:
        """Apunta una discontinuidad de RECONEXION sin publicarla (fail-safe del
        frontier).

        A diferencia del hueco detectado por el Motor -- que es un hecho publicado --,
        una reconexion se resuelve re-sembrando; se registra para que el frontier de las
        barras solapadas salga incompleto, pero no se emite un evento por cada
        reconexion.
        """
        try:
            nueva = self._writer.record_discontinuity(
                key.exchange,
                key.market_type.value,
                key.symbol,
                from_seq,
                to_seq,
                self._clock.now_ms(),
                _REASON_RECONNECT,
            )
        except Exception:  # noqa: BLE001 - no poder apuntarlo no tumba el ciclo.
            self.metrics.seed_errors += 1
            self.metrics.degraded_streams.add(clave)
            return
        if nueva:
            self.metrics.discontinuities_recorded += 1

    def _publicar_resync(self, book: OrderbookBook, clave: str) -> None:
        """Publica market.orderbook_resynced del hueco detectado por el Motor (ADR-013).

        persist_and_enqueue persiste la discontinuidad Y la encola en LA MISMA
        transaccion. from_sequence es la ultima secuencia buena; to_sequence None: el
        extremo de reanudacion es DESCONOCIDO hasta que una reconexion re-siembre
        (fail-safe).
        """
        exchange, market_type, symbol = book.exchange, book.market_type, book.symbol
        if exchange is None or market_type is None or symbol is None:
            return
        now = self._clock.now_ms()
        payload = OrderbookResyncedPayload(
            exchange=exchange,
            market_type=MarketType(market_type),
            symbol=symbol,
            from_sequence=book.sequence,
            to_sequence=None,
            reason=_REASON_GAP,
            event_time=now,
        )
        event = MarketOrderbookEventType.ORDERBOOK_RESYNCED
        try:
            escrita = self._writer.persist_and_enqueue(
                envelope_json=self._envelope(payload, event, now),
                payload=payload,
                event_type=event.value,
                stream_key=payload.stream_key(),
                idempotency_key=payload.idempotency_key(),
                event_time=now,
            )
        except Exception:  # noqa: BLE001 - un publish fallido no tumba el ciclo.
            self.metrics.seed_errors += 1
            self.metrics.degraded_streams.add(clave)
            return
        if escrita:
            self.metrics.resyncs += 1

    def _envelope(
        self,
        payload: OrderbookResyncedPayload,
        event: MarketOrderbookEventType,
        event_time: int,
    ) -> bytes:
        """El sobre canonico del resync (ADR-003/007/011). event_time = ahora: el resync
        es NUESTRA observacion (el libro perdio continuidad), no un hecho fechado por
        el exchange; por eso su origen es nuestro reloj, a diferencia de un trade o
        una vela.
        """
        ahora = self._clock.now_ms()
        stream_key = payload.stream_key()
        envelope = Envelope[OrderbookResyncedPayload](
            event_type=event.value,
            event_schema_version=expected_event_schema_version(event.value),
            source=self._component_source,
            idempotency_key=payload.idempotency_key(),
            stream_key=stream_key,
            source_sequence=payload.from_sequence,
            scope=Scope.PUBLIC_MARKET,
            event_time=event_time,
            ingestion_time=ahora,
            processing_time=ahora,
            correlation_id=stream_key,
            payload=payload,
        )
        return envelope.model_dump_json().encode()
