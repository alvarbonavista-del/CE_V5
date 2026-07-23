"""Motor de agregacion del footprint (P07b 3b-1; ADR-007/013/014, CE-14).

CONSUMIDOR del bus, NO productor de trades: por cada market.candle_closed que llega,
agrega el footprint de esa barra desde los trades ya persistidos y lo emite como
market.footprint_closed (persist + outbox atomico); por cada market.candle_corrected,
emite market.footprint_corrected con la MISMA revision de la vela (lockstep, append).
NO toca el nucleo de ingesta: solo lee market_trade/market_trade_gap y escribe el
footprint. La cobertura de reconexion y la ingesta de trades son de la fase 3a.

NO importa infra: depende de dos PUERTOS (lectura de la ventana de trades/huecos y
escritura atomica del footprint), que infra satisface por FORMA. La agregacion es PURA
(footprint_aggregate) y se prueba en frio sin red ni base.

El event_time del footprint es el de la VELA que lo dispara (su envelope, ADR-007): el
mismo instante de origen de la barra. No se inventa con nuestro reloj.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from ce_v5.core.clock import Clock
from ce_v5.platform.market.footprint_aggregate import (
    FootprintStreamIdentity,
    TradeGap,
    aggregate_footprint,
)
from source.envelope import Envelope
from source.envelope.enums import Scope
from source.families.footprint import (
    FootprintPayload,
    MarketFootprintEventType,
    MarketTrade,
)
from source.families.market import CandleClosedPayload, CandleCorrectedPayload
from source.families.registry import expected_event_schema_version
from source.time import MaturityState


class FootprintTradeReaderPort(Protocol):
    """Lectura de la ventana de trades y de los huecos de una barra. Lo cumple infra."""

    def trades_in_window(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        window_start: int,
        window_end: int,
    ) -> Sequence[MarketTrade]:
        """Los trades cuyo event_time cae en [window_start, window_end)."""
        ...

    def overlapping_gaps(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        window_start: int,
        window_end: int,
    ) -> Sequence[TradeGap]:
        """Los huecos de market_trade_gap que solapan [window_start, window_end)."""
        ...


class FootprintWriterPort(Protocol):
    """Escritura atomica del footprint (historico + outbox). Lo cumple infra."""

    def persist_and_enqueue(
        self,
        envelope_json: bytes,
        payload: FootprintPayload,
        event_type: str,
        stream_key: str,
        idempotency_key: str,
    ) -> bool:
        """Guarda el footprint Y lo encola, atomico. False si ya existia."""
        ...


@dataclass(slots=True)
class FootprintMetrics:
    """Observabilidad. CELDAS-POR-BARRA es la metrica que Central pidio: sin cap,
    lo que se vigila es cuantas produce cada barra (un salto delata un flujo anomalo).
    """

    footprints_closed: int = 0
    footprints_corrected: int = 0
    duplicates_skipped: int = 0
    incomplete_bars: int = 0
    cells_last_bar: int = 0
    max_cells_in_bar: int = 0
    total_cells: int = 0


class FootprintEngine:
    """Agrega el footprint de una barra cuando su vela cierra (o se corrige)."""

    def __init__(
        self,
        reader: FootprintTradeReaderPort,
        writer: FootprintWriterPort,
        clock: Clock,
        *,
        component_source: str,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._clock = clock
        self._component_source = component_source
        self.metrics = FootprintMetrics()

    def on_candle_closed(self, closed: CandleClosedPayload, event_time: int) -> None:
        """Una vela cerro -> footprint_closed de esa barra."""
        payload = self._aggregate(closed, MaturityState.CLOSED, None)
        tipo = MarketFootprintEventType.FOOTPRINT_CLOSED
        escrita = self._emit(payload, tipo, event_time)
        if escrita:
            self.metrics.footprints_closed += 1

    def on_candle_corrected(
        self, corrected: CandleCorrectedPayload, event_time: int
    ) -> None:
        """Una vela se corrigio -> footprint_corrected con SU revision (lockstep)."""
        payload = self._aggregate(
            corrected, MaturityState.CORRECTION, corrected.correction_revision
        )
        escrita = self._emit(
            payload, MarketFootprintEventType.FOOTPRINT_CORRECTED, event_time
        )
        if escrita:
            self.metrics.footprints_corrected += 1

    def _aggregate(
        self,
        candle: CandleClosedPayload | CandleCorrectedPayload,
        maturity_state: MaturityState,
        correction_revision: int | None,
    ) -> FootprintPayload:
        identity = FootprintStreamIdentity(
            exchange=candle.exchange,
            market_type=candle.market_type,
            symbol=candle.symbol,
            timeframe=candle.timeframe,
        )
        window_end = candle.open_time + candle.timeframe.duration_ms
        trades = self._reader.trades_in_window(
            candle.exchange,
            candle.market_type.value,
            candle.symbol,
            candle.open_time,
            window_end,
        )
        gaps = self._reader.overlapping_gaps(
            candle.exchange,
            candle.market_type.value,
            candle.symbol,
            candle.open_time,
            window_end,
        )
        return aggregate_footprint(
            identity,
            candle.open_time,
            candle.close_time,
            trades,
            gaps,
            maturity_state=maturity_state,
            correction_revision=correction_revision,
        )

    def _emit(
        self,
        payload: FootprintPayload,
        event_type: MarketFootprintEventType,
        event_time: int,
    ) -> bool:
        """Historico + outbox, ATOMICO. Idempotente por la idempotency_key del fp."""
        envelope = self._envelope(payload, event_type, event_time)
        escrita = self._writer.persist_and_enqueue(
            envelope_json=envelope,
            payload=payload,
            event_type=event_type.value,
            stream_key=payload.stream_key(),
            idempotency_key=payload.idempotency_key(event_type),
        )
        if not escrita:
            # Ya estaba (reprocesado un candle ya agregado): ni duplica ni re-encola.
            self.metrics.duplicates_skipped += 1
            return False
        cells = len(payload.cells)
        self.metrics.cells_last_bar = cells
        self.metrics.total_cells += cells
        self.metrics.max_cells_in_bar = max(self.metrics.max_cells_in_bar, cells)
        if not payload.is_complete:
            self.metrics.incomplete_bars += 1
        return True

    def _envelope(
        self,
        payload: FootprintPayload,
        event_type: MarketFootprintEventType,
        event_time: int,
    ) -> bytes:
        """El sobre canonico del footprint (ADR-003/007/011), espejo del de la vela."""
        ahora = self._clock.now_ms()
        stream_key = payload.stream_key()
        envelope = Envelope[FootprintPayload](
            event_type=event_type.value,
            event_schema_version=expected_event_schema_version(event_type.value),
            source=self._component_source,
            idempotency_key=payload.idempotency_key(event_type),
            stream_key=stream_key,
            # El footprint no nace de UN trade: no hay source_sequence de origen.
            source_sequence=None,
            # Publico: sin tenant (ADR-011).
            scope=Scope.PUBLIC_MARKET,
            # event_time = el de la VELA que dispara (ADR-007): instante de origen de la
            # barra, no nuestro reloj.
            event_time=event_time,
            ingestion_time=ahora,
            processing_time=ahora,
            correlation_id=stream_key,
            payload=payload,
        )
        return envelope.model_dump_json().encode()
