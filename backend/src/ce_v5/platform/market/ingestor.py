"""Motor de ingesta: el feed de un exchange se convierte en hechos (ADR-007/013/014).

REGLA DE ORO DE LA PIEZA (dictamen P07-A):

- candle_closed y candle_corrected SE PERSISTEN y van por OUTBOX, en la MISMA
  transaccion: es IMPOSIBLE que exista una vela guardada que nunca se publico, o
  publicada que no se guardo. Por eso el puerto expone UN SOLO metodo
  (persist_and_enqueue) y no dos: si fuesen dos, alguien acabaria llamando a uno sin
  el otro, y esa divergencia es justo lo que el patron outbox existe para impedir.

- candle_updated NO se persiste (no es historia, es una vista viva) y va DIRECTO al
  bus, FAIL-LOUD. No hay divergencia posible porque no hay pareja persistida con la
  que discrepar. Si el bus falla, se PROPAGA: una vista viva que se pierde en silencio
  es un grafico que miente.

NO importa infra ni components. Sin hilos y sin sleep: el tiempo entra por el Clock y
el ritmo lo marca quien llama a drain_once().
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol
from uuid import uuid4

from ce_v5.core.bus import BusMessage, EventBus
from ce_v5.core.clock import Clock
from ce_v5.platform.market.datasource import MarketDataSourcePort
from ce_v5.platform.market.normalize import (
    RawCandleRejected,
    candle_payload_from_raw,
)
from source.envelope import Envelope
from source.envelope.enums import Scope
from source.families.market import (
    CandleCorrectedPayload,
    CandlePayload,
    MarketCandleEventType,
    MarketStreamKey,
    RawCandle,
    StoredCandle,
)
from source.families.registry import expected_event_schema_version
from source.time import MaturityState


class CandleWriterPort(Protocol):
    """Puerto de escritura del historico. Lo cumple infra/db por FORMA (estructural)."""

    def existing(self, stream_key: str, open_time_ms: int) -> StoredCandle | None:
        """La vela ORIGINAL ya guardada para esa ventana, si la hay."""
        ...

    def persist_and_enqueue(
        self,
        envelope_json: bytes,
        payload: CandlePayload,
        event_type: str,
        stream_key: str,
        idempotency_key: str,
    ) -> bool:
        """Guarda la vela en el historico Y la encola en la outbox, en la MISMA
        transaccion (ADR-013). Devuelve False si ya existia (dedup por
        idempotency_key). NUNCA puede guardar sin encolar ni encolar sin guardar: de
        ahi que sea UN solo metodo y no dos.
        """
        ...


@dataclass(frozen=True, slots=True)
class IngestionConfig:
    """BACKPRESSURE: quien manda es el ingestor, no el exchange.

    max_batch acota lo que se procesa por ciclo. Sin tope, una avalancha del exchange
    se convierte en una cola infinita en memoria y tumba el proceso. Lo que no cabe no
    se pierde: espera en el feed al siguiente ciclo.
    """

    max_batch: int = 500
    poll_timeout_ms: int = 200


DEFAULT_INGESTION_CONFIG = IngestionConfig()


@dataclass(slots=True)
class IngestionMetrics:
    """Observabilidad. Sin esto, un stream zombi o un feed que solo manda basura son
    INVISIBLES: el proceso parece sano porque no falla.
    """

    provisional_published: int = 0
    closed_persisted: int = 0
    corrections_emitted: int = 0
    duplicates_skipped: int = 0
    out_of_order_dropped: int = 0
    unsubscribed_dropped: int = 0
    rejected: dict[str, int] = field(default_factory=dict)  # por reason code
    degraded_streams: set[str] = field(default_factory=set)


class IngestionEngine:
    """Convierte el feed de un exchange en hechos del sistema (ADR-007/013/014)."""

    def __init__(
        self,
        source: MarketDataSourcePort,
        writer: CandleWriterPort,
        bus: EventBus,
        clock: Clock,
        *,
        component_source: str,
        config: IngestionConfig = DEFAULT_INGESTION_CONFIG,
    ) -> None:
        self._source = source
        self._writer = writer
        self._bus = bus
        self._clock = clock
        self._component_source = component_source
        self._config = config
        self.metrics = IngestionMetrics()
        # Ultima ventana CERRADA por stream: una provisional anterior a esto llega
        # tarde y no aporta nada (la ventana ya tiene su verdad definitiva).
        self._watermarks: dict[str, int] = {}

    def drain_once(self) -> IngestionMetrics:
        """Un ciclo: procesa hasta max_batch mensajes y los convierte en hechos.

        BACKPRESSURE SIN PERDIDA: el motor deja de PEDIR cuando alcanza su tope, en vez
        de pedirlo todo y tirar lo que no le cabe. Lo que no se pide se queda en el
        feed, esperando al siguiente ciclo: nada se pierde y la memoria no crece sin
        limite. Tirar el sobrante seria "backpressure" solo de nombre; en realidad
        seria perder velas en silencio.
        """
        suscritas = {
            clave: MarketStreamKey.parse(clave) for clave in self._source.active()
        }

        procesados = 0
        while procesados < self._config.max_batch:
            crudas = self._source.poll(self._config.poll_timeout_ms)
            if not crudas:
                break
            for raw in crudas:
                self._procesar(raw, suscritas)
                procesados += 1
        return self.metrics

    def _procesar(
        self, raw: RawCandle, suscritas: Mapping[str, MarketStreamKey]
    ) -> None:
        clave_texto = self._clave_declarada(raw)
        esperada = suscritas.get(clave_texto)
        if esperada is None:
            # Nadie pidio este flujo: no se procesa. Un dato que nadie quiere no entra
            # en el historico solo porque el exchange lo mande.
            self.metrics.unsubscribed_dropped += 1
            return

        try:
            event_type, payload = candle_payload_from_raw(raw, esperada)
        except RawCandleRejected as rechazo:
            # AISLAMIENTO POR STREAM: una vela corrupta de BTC no puede impedir que se
            # procese la vela buena de ETH que viene detras en el mismo lote.
            motivo = rechazo.reason.value
            self.metrics.rejected[motivo] = self.metrics.rejected.get(motivo, 0) + 1
            self.metrics.degraded_streams.add(clave_texto)
            return

        if payload.maturity_state is MaturityState.PROVISIONAL:
            self._procesar_provisional(raw, payload, event_type, clave_texto)
        else:
            self._procesar_cerrada(raw, payload, event_type, clave_texto)

    def _procesar_provisional(
        self,
        raw: RawCandle,
        payload: CandlePayload,
        event_type: MarketCandleEventType,
        clave_texto: str,
    ) -> None:
        watermark = self._watermarks.get(clave_texto)
        if watermark is not None and payload.open_time <= watermark:
            # La ventana ya cerro: su verdad definitiva ya esta publicada. Una
            # provisional de esa ventana no aporta nada y CONTRADIRIA a la cerrada.
            self.metrics.out_of_order_dropped += 1
            return

        # FAIL-LOUD: si el bus falla, PROPAGA. Una vista viva perdida en silencio es un
        # grafico que miente. No se persiste: no es historia.
        self._publicar(raw, payload, event_type)
        self.metrics.provisional_published += 1

    def _procesar_cerrada(
        self,
        raw: RawCandle,
        payload: CandlePayload,
        event_type: MarketCandleEventType,
        clave_texto: str,
    ) -> None:
        existente = self._writer.existing(clave_texto, payload.open_time)

        if existente is None:
            escrita = self._persistir(raw, payload, event_type)
            if escrita:
                self.metrics.closed_persisted += 1
            else:
                # Alguien se adelanto (otra replica, un reintento): dedup por clave.
                self.metrics.duplicates_skipped += 1
        elif existente.same_values_as(payload):
            # EL CASO NORMAL, no un error: tras una reconexion, el bootstrap REST
            # vuelve a traer velas que ya teniamos. Identicas: no hay nada que hacer.
            self.metrics.duplicates_skipped += 1
        else:
            self._emitir_correccion(raw, payload, existente)

        self._watermarks[clave_texto] = max(
            payload.open_time, self._watermarks.get(clave_texto, payload.open_time)
        )

    def _emitir_correccion(
        self,
        raw: RawCandle,
        payload: CandlePayload,
        existente: StoredCandle,
    ) -> None:
        """El exchange corrigio la vela. El original NO SE TOCA: append-only (ADR-007).

        La correccion es un hecho NUEVO que REFERENCIA al corregido. Y numera su
        revision: dos correcciones de la misma vela son DOS hechos distintos, y sin la
        revision compartirian idempotency_key y la outbox se tragaria la segunda EN
        SILENCIO.
        """
        revision = existente.max_correction_revision + 1
        correccion = CandleCorrectedPayload(
            maturity_state=MaturityState.CORRECTION,
            corrects_idempotency_key=existente.idempotency_key,
            correction_revision=revision,
            exchange=payload.exchange,
            market_type=payload.market_type,
            symbol=payload.symbol,
            timeframe=payload.timeframe,
            open_time=payload.open_time,
            close_time=payload.close_time,
            open=payload.open,
            high=payload.high,
            low=payload.low,
            close=payload.close,
            volume=payload.volume,
        )
        escrita = self._persistir(
            raw, correccion, MarketCandleEventType.CANDLE_CORRECTED
        )
        if escrita:
            self.metrics.corrections_emitted += 1
        else:
            self.metrics.duplicates_skipped += 1

    def _persistir(
        self,
        raw: RawCandle,
        payload: CandlePayload,
        event_type: MarketCandleEventType,
    ) -> bool:
        """Historico + outbox, ATOMICO. Una sola llamada: no hay forma de hacer una
        cosa sin la otra.
        """
        envelope = self._envelope(raw, payload, event_type)
        return self._writer.persist_and_enqueue(
            envelope_json=envelope,
            payload=payload,
            event_type=event_type.value,
            stream_key=payload.stream_key(),
            idempotency_key=payload.idempotency_key(event_type),
        )

    def _publicar(
        self,
        raw: RawCandle,
        payload: CandlePayload,
        event_type: MarketCandleEventType,
    ) -> None:
        """Directo al bus, sin outbox: la provisional no nace de una transaccion."""
        envelope = self._envelope(raw, payload, event_type)
        idempotency_key = payload.idempotency_key(event_type)
        self._bus.publish(
            _topic_for(event_type.value),
            BusMessage(
                event_id=str(uuid4()),
                event_type=event_type.value,
                stream_key=payload.stream_key(),
                idempotency_key=idempotency_key,
                envelope=envelope,
            ),
        )

    def _envelope(
        self,
        raw: RawCandle,
        payload: CandlePayload,
        event_type: MarketCandleEventType,
    ) -> bytes:
        """El sobre canonico (ADR-003/007/011)."""
        ahora = self._clock.now_ms()
        stream_key = payload.stream_key()
        envelope = Envelope[CandlePayload](
            event_type=event_type.value,
            event_schema_version=expected_event_schema_version(event_type.value),
            source=self._component_source,
            idempotency_key=payload.idempotency_key(event_type),
            stream_key=stream_key,
            source_sequence=raw.source_sequence,
            # Los publicos NO llevan tenant (ADR-011): un solo hecho para todos.
            scope=Scope.PUBLIC_MARKET,
            # event_time LO FIJA EL ORIGEN (ADR-007): es el instante del EXCHANGE.
            # Jamas el nuestro; fecharlo con nuestro reloj seria inventar cuando paso.
            event_time=raw.event_time_ms,
            ingestion_time=ahora,
            processing_time=ahora,
            correlation_id=stream_key,
            payload=payload,
        )
        return envelope.model_dump_json().encode()

    def watermarks(self) -> Mapping[str, int]:
        """Ultima ventana cerrada por stream (observable)."""
        return dict(self._watermarks)

    def _clave_declarada(self, raw: RawCandle) -> str:
        """La clave del flujo al que la vela DICE pertenecer.

        Solo sirve para encontrar la suscripcion; que la vela pertenezca de verdad a
        ese flujo lo decide la frontera de confianza (anti-suplantacion), no esto.
        """
        partes = [
            "market",
            "candles",
            raw.exchange,
            raw.market_type,
            raw.symbol,
            raw.timeframe,
        ]
        return ":".join(partes)


def _topic_for(event_type: str) -> str:
    """El topic es la FAMILIA del evento (ADR-004)."""
    return event_type.split(".", 1)[0]
