"""Tests del motor de snapshot del LIBRO L2 (P07c Tanda III). SIN RED NI BASE.

Con un OrderbookBook real (sembrado a mano) y fakes del writer/reader. Se demuestra: la
muestra lleva el is_complete del libro (cond.3); la frontera sale incompleta si una
discontinuidad solapa la barra -- espejo del footprint --; el top-K recorta y ordena
bien; y la idempotency_key cambia con K, cadencia, ventana y formula_version (cond.1).
"""

from __future__ import annotations

from decimal import Decimal

from ce_v5.platform.market.orderbook_book import OrderbookBook
from ce_v5.platform.market.orderbook_snapshot import (
    OrderbookSnapshotConfig,
    OrderbookSnapshotEngine,
)
from source.families.market import Timeframe
from source.families.orderbook import (
    MarketOrderbookEventType,
    MarketOrderbookSnapshotKind,
    OrderbookResyncedPayload,
    OrderbookSnapshotPayload,
)

_TF = Timeframe.H1
_OPEN = 1_784_073_600_000  # alineado a 1h.
_CLOSE = _OPEN + _TF.duration_ms
_SAMPLE_TIME = _OPEN + 30_000


class _Clock:
    def now_ms(self) -> int:
        return _OPEN + 5


class _Writer:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, OrderbookSnapshotPayload]] = []
        self.published_event_time: list[int] = []
        self.samples: list[OrderbookSnapshotPayload] = []
        self._discs: list[tuple[str, str, str, int, int | None, int]] = []

    def preload_discontinuity(
        self, exchange: str, market_type: str, symbol: str, event_time: int
    ) -> None:
        self._discs.append((exchange, market_type, symbol, 100, None, event_time))

    def persist_and_enqueue(
        self,
        envelope_json: bytes,
        payload: object,
        event_type: str,
        stream_key: str,
        idempotency_key: str,
        event_time: int,
    ) -> bool:
        assert isinstance(payload, OrderbookSnapshotPayload | OrderbookResyncedPayload)
        assert isinstance(payload, OrderbookSnapshotPayload)
        self.published.append((event_type, idempotency_key, payload))
        self.published_event_time.append(event_time)
        return True

    def persist_sample(
        self, payload: OrderbookSnapshotPayload, event_time: int
    ) -> bool:
        self.samples.append(payload)
        return True

    def record_discontinuity(self, *args: object) -> bool:  # noqa: ANN401 - no usado aqui
        return True

    def overlapping_discontinuities(
        self, exchange: str, market_type: str, symbol: str, ws: int, we: int
    ) -> tuple[tuple[int, int | None, int], ...]:
        return tuple(
            (fr, to, et)
            for (ex, mt, sy, fr, to, et) in self._discs
            if ex == exchange and mt == market_type and sy == symbol and ws <= et < we
        )


def _book(bids: list[tuple[str, str]], asks: list[tuple[str, str]]) -> OrderbookBook:
    from source.families.market import RawOrderbookSeed

    book = OrderbookBook()
    book.seed(
        RawOrderbookSeed(
            exchange="binance",
            market_type="spot",
            symbol="BTC-USDT",
            bids=bids,
            asks=asks,
            base_sequence=100,
        )
    )
    return book


def _incompleto(book: OrderbookBook) -> OrderbookBook:
    from source.families.market import RawOrderbookDelta

    # Un salto de secuencia deja el libro en resync (is_complete=False).
    book.apply(
        RawOrderbookDelta(
            exchange="binance",
            market_type="spot",
            symbol="BTC-USDT",
            bids=[],
            asks=[],
            first_update_id=200,
            final_update_id=205,
        )
    )
    return book


def _engine(writer: _Writer, **config: object) -> OrderbookSnapshotEngine:
    return OrderbookSnapshotEngine(
        writer,
        writer,
        _Clock(),
        component_source="worker_orderbook",
        config=OrderbookSnapshotConfig(**config),  # type: ignore[arg-type]
    )


_BIDS = [("100.5", "1"), ("100.4", "2"), ("100.3", "3")]
_ASKS = [("100.6", "1"), ("100.7", "2"), ("100.8", "3")]


class TestSample:
    def test_la_muestra_lleva_el_is_complete_del_libro(self) -> None:
        writer = _Writer()
        engine = _engine(writer)
        book = _book(_BIDS, _ASKS)

        assert engine.take_sample(
            book,
            timeframe=_TF,
            open_time=_OPEN,
            close_time=_CLOSE,
            sample_time=_SAMPLE_TIME,
        )
        muestra = writer.samples[0]
        assert muestra.kind is MarketOrderbookSnapshotKind.SAMPLE
        assert muestra.sample_time == _SAMPLE_TIME
        assert muestra.is_complete is True
        assert engine.metrics.samples_persisted == 1

    def test_una_muestra_de_un_libro_incompleto_sale_incompleta(self) -> None:
        writer = _Writer()
        engine = _engine(writer)
        book = _incompleto(_book(_BIDS, _ASKS))

        engine.take_sample(
            book,
            timeframe=_TF,
            open_time=_OPEN,
            close_time=_CLOSE,
            sample_time=_SAMPLE_TIME,
        )
        assert writer.samples[0].is_complete is False


class TestFrontier:
    def test_frontier_completo_si_no_hay_discontinuidad(self) -> None:
        writer = _Writer()
        engine = _engine(writer)
        book = _book(_BIDS, _ASKS)

        assert engine.take_frontier(
            book, timeframe=_TF, open_time=_OPEN, close_time=_CLOSE
        )
        event_type, _clave, payload = writer.published[0]
        assert event_type == MarketOrderbookEventType.ORDERBOOK_FRONTIER.value
        assert payload.kind is MarketOrderbookSnapshotKind.FRONTIER
        assert payload.sample_time is None
        assert payload.is_complete is True
        assert engine.metrics.frontiers_published == 1
        assert engine.metrics.incomplete_frontiers == 0

    def test_frontier_incompleto_si_una_discontinuidad_solapa_la_barra(self) -> None:
        # Espejo EXACTO del footprint: un resync DENTRO de [open, close) marca la barra
        # incompleta, aunque el libro ya se recuperase (is_complete del libro = True).
        writer = _Writer()
        writer.preload_discontinuity("binance", "spot", "BTC-USDT", _OPEN + 1000)
        engine = _engine(writer)
        book = _book(_BIDS, _ASKS)  # el libro esta completo AHORA

        engine.take_frontier(book, timeframe=_TF, open_time=_OPEN, close_time=_CLOSE)
        assert writer.published[0][2].is_complete is False
        assert engine.metrics.incomplete_frontiers == 1

    def test_una_discontinuidad_fuera_de_la_barra_no_la_marca(self) -> None:
        writer = _Writer()
        writer.preload_discontinuity("binance", "spot", "BTC-USDT", _CLOSE + 1)
        engine = _engine(writer)
        book = _book(_BIDS, _ASKS)

        engine.take_frontier(book, timeframe=_TF, open_time=_OPEN, close_time=_CLOSE)
        assert writer.published[0][2].is_complete is True

    def test_el_event_time_del_frontier_es_open_time_no_close(self) -> None:
        # Cond.2: la frontera es la foto de ESA barra; su event_time (ADR-007) es el
        # as_of = open_time, la misma ancla que su idempotency_key. NO close_time.
        writer = _Writer()
        engine = _engine(writer)
        engine.take_frontier(
            _book(_BIDS, _ASKS), timeframe=_TF, open_time=_OPEN, close_time=_CLOSE
        )
        assert writer.published_event_time[0] == _OPEN

    def test_frontier_de_libro_sin_semilla_no_publica_fire_anyway(self) -> None:
        # Cond.5: el trigger dispara en CADA barra (fire-anyway), pero un libro sin
        # semilla no tiene top-K que fotografiar y un snapshot vacio viola 5.21: NO se
        # publica y se cuenta. Disparar siempre, publicar solo lo real.
        writer = _Writer()
        engine = _engine(writer)
        sin_semilla = OrderbookBook()  # nunca sembrado: bids/asks vacios.

        publicado = engine.take_frontier(
            sin_semilla, timeframe=_TF, open_time=_OPEN, close_time=_CLOSE
        )
        assert publicado is False
        assert writer.published == []
        assert engine.metrics.frontiers_skipped_unseeded == 1
        assert engine.metrics.frontiers_published == 0


class TestTopK:
    def test_el_top_k_recorta_y_ordena(self) -> None:
        writer = _Writer()
        engine = _engine(writer, depth_k=2)
        book = _book(_BIDS, _ASKS)

        engine.take_frontier(book, timeframe=_TF, open_time=_OPEN, close_time=_CLOSE)
        payload = writer.published[0][2]
        assert payload.depth_k == 2
        # bids: los DOS de precio mas alto, DESCENDENTE.
        assert [lvl.price for lvl in payload.bids] == [
            Decimal("100.5"),
            Decimal("100.4"),
        ]
        # asks: los DOS de precio mas bajo, ASCENDENTE.
        assert [lvl.price for lvl in payload.asks] == [
            Decimal("100.6"),
            Decimal("100.7"),
        ]

    def test_la_secuencia_del_libro_viaja_al_payload(self) -> None:
        writer = _Writer()
        engine = _engine(writer)
        book = _book(_BIDS, _ASKS)
        engine.take_frontier(book, timeframe=_TF, open_time=_OPEN, close_time=_CLOSE)
        assert writer.published[0][2].sequence == 100


class TestIdempotencyReproducible:
    """Cond.1: la idempotency_key cambia con K, cadencia, ventana y formula_version."""

    def _key(self, writer: _Writer) -> str:
        return writer.published[0][1]

    def _frontier(self, **config: object) -> str:
        writer = _Writer()
        engine = _engine(writer, **config)
        engine.take_frontier(
            _book(_BIDS, _ASKS), timeframe=_TF, open_time=_OPEN, close_time=_CLOSE
        )
        return self._key(writer)

    def test_misma_config_misma_clave(self) -> None:
        assert self._frontier(depth_k=2) == self._frontier(depth_k=2)

    def test_distinta_K_distinta_clave(self) -> None:
        assert self._frontier(depth_k=2) != self._frontier(depth_k=3)

    def test_distinta_cadencia_distinta_clave(self) -> None:
        assert self._frontier(cadence_ms=1000) != self._frontier(cadence_ms=500)

    def test_distinta_formula_version_distinta_clave(self) -> None:
        assert self._frontier(formula_version=1) != self._frontier(formula_version=2)

    def test_distinto_clock_source_distinta_clave(self) -> None:
        # Refino de procedencia (Central): una captura por reloj real y otra por reloj
        # simulado del MISMO as_of son hechos distintos y no colisionan.
        assert self._frontier(clock_source="system") != self._frontier(
            clock_source="simulated"
        )

    def test_distinta_ventana_distinta_clave(self) -> None:
        writer_a, writer_b = _Writer(), _Writer()
        otra = _OPEN + _TF.duration_ms
        _engine(writer_a).take_frontier(
            _book(_BIDS, _ASKS), timeframe=_TF, open_time=_OPEN, close_time=_CLOSE
        )
        _engine(writer_b).take_frontier(
            _book(_BIDS, _ASKS),
            timeframe=_TF,
            open_time=otra,
            close_time=otra + _TF.duration_ms,
        )
        assert self._key(writer_a) != self._key(writer_b)
