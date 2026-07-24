"""Cableado del LIBRO en el worker de ingesta, EN FRIO (sin red, sin DB, sin build).

Cubre el enganche aditivo de la Tanda IV completa:
- _as_orderbook_source: el MISMO feed visto por su cara de libro, o None si no la sirve;
- _OrderbookSampler: la cadencia de la MUESTRA (~1/s), sin reloj propio;
- _OrderbookFrontier: el trigger de la FRONTERA por RELOJ DE BARRA (opcion 3) -- cruzar
  un limite de tf dispara la barra que cerro, keyed a su open_time; un cruce de 5m
  dispara tambien el de 1m; sin candle_corrected en ningun camino;
- _active_candle_keys: las (symbol, tf) de VELA realmente abiertas;
- _drain_orderbook/_fronterizar: dispara take_frontier por barra cerrada, fire-anyway.

build_context necesita DB y se cubre en integracion; aqui NADA toca la red.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import cast

from ce_v5.entrypoints.worker_ingestion import __main__ as m
from ce_v5.entrypoints.worker_ingestion.__main__ import (
    _active_candle_keys,
    _drain_orderbook,
    _OrderbookFrontier,
    _OrderbookSampler,
)
from ce_v5.entrypoints.worker_ingestion.composition import (
    IngestionContext,
    _as_orderbook_source,
)
from ce_v5.platform.market.datasource import MarketDataSourcePort
from ce_v5.platform.market.orderbook_source import OrderbookDataSourcePort
from source.families.market import (
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    Timeframe,
)

# T0 alineado a 5m (y por tanto a 1m): un instante de frontera comun a ambos tf.
_T0 = 1_784_073_600_000


def _candle_key(symbol: str, tf: Timeframe) -> MarketStreamKey:
    return MarketStreamKey(
        exchange="binance",
        market_type=MarketType.SPOT,
        symbol=symbol,
        data_kind=MarketDataKind.CANDLES,
        timeframe=tf,
    )


# -- _as_orderbook_source ---------------------------------------------------


class _FeedSinLibro:
    """Un feed que solo sirve velas/trades: NO tiene seed ni poll_deltas."""


class _FeedConLibro:
    """Un feed que ademas sirve libro: seed y poll_deltas (satisface por FORMA)."""

    def seed(self, key: object) -> object:  # pragma: no cover - forma, no logica.
        raise NotImplementedError

    def poll_deltas(self, timeout_ms: int) -> list[object]:  # pragma: no cover
        return []


def test_as_orderbook_source_devuelve_none_si_el_feed_no_sirve_libro() -> None:
    feed = cast(MarketDataSourcePort, _FeedSinLibro())
    assert _as_orderbook_source(feed) is None


def test_as_orderbook_source_devuelve_el_mismo_feed_si_sirve_libro() -> None:
    feed = cast(MarketDataSourcePort, _FeedConLibro())
    # EL MISMO objeto: no se construye un segundo feed ni un segundo socket.
    assert _as_orderbook_source(feed) is cast(OrderbookDataSourcePort, feed)


# -- _OrderbookSampler ------------------------------------------------------


def test_sampler_dispara_en_el_primero_y_respeta_la_cadencia() -> None:
    sampler = _OrderbookSampler(1000)
    assert sampler.due(0) is True  # el primero SIEMPRE dispara.
    assert sampler.due(500) is False  # dentro de la ventana: no toca.
    assert sampler.due(1000) is True  # cumplida la cadencia: dispara.
    assert sampler.due(1500) is False
    assert sampler.due(2000) is True


# -- _OrderbookFrontier (trigger por reloj de barra) ------------------------


def test_frontier_no_dispara_en_el_primer_avistamiento() -> None:
    # No hay barra anterior que cerrar sin inventarse cuando abrio: solo se registra.
    frontier = _OrderbookFrontier()
    assert frontier.due_bars([_candle_key("BTC-USDT", Timeframe.M1)], _T0 - 10) == []


def test_frontier_dispara_una_vez_al_cruzar_el_limite_keyed_a_open_time() -> None:
    frontier = _OrderbookFrontier()
    key = _candle_key("BTC-USDT", Timeframe.M1)
    frontier.due_bars([key], _T0 - 10)  # registra el bucket previo.

    cerradas = frontier.due_bars([key], _T0 + 10)  # cruza a la barra siguiente.
    assert len(cerradas) == 1
    _key, tf, open_time, close_time = cerradas[0]
    assert tf is Timeframe.M1
    assert open_time == _T0 - 60_000  # la barra que cerro: open = boundary anterior.
    assert close_time == _T0

    # Dentro del mismo bucket ya NO vuelve a disparar.
    assert frontier.due_bars([key], _T0 + 20) == []


def test_cruzar_5m_dispara_tambien_la_frontera_de_1m() -> None:
    frontier = _OrderbookFrontier()
    k1 = _candle_key("BTC-USDT", Timeframe.M1)
    k5 = _candle_key("BTC-USDT", Timeframe.M5)
    frontier.due_bars([k1, k5], _T0 - 10)  # ambos registrados.

    cerradas = frontier.due_bars([k1, k5], _T0 + 10)  # T0 es cruce de 1m Y de 5m.
    por_tf = {tf: (o, c) for (_k, tf, o, c) in cerradas}
    assert set(por_tf) == {Timeframe.M1, Timeframe.M5}  # AMBOS activos disparan.
    assert por_tf[Timeframe.M1] == (_T0 - 60_000, _T0)
    assert por_tf[Timeframe.M5] == (_T0 - 300_000, _T0)


def test_frontier_dispara_en_barra_plana_solo_depende_del_reloj() -> None:
    # Cond.5: el disparo es del RELOJ, no del volumen: sin un solo delta/vela nueva,
    # cruzar el limite cierra la barra igual. La 'planitud' es irrelevante aqui.
    frontier = _OrderbookFrontier()
    key = _candle_key("ETH-USDT", Timeframe.M1)
    frontier.due_bars([key], _T0 - 10)
    assert len(frontier.due_bars([key], _T0 + 10)) == 1


def test_frontier_olvida_las_claves_que_dejan_de_estar_activas() -> None:
    frontier = _OrderbookFrontier()
    key = _candle_key("BTC-USDT", Timeframe.M1)
    frontier.due_bars([key], _T0 - 10)  # registrada.
    assert frontier.due_bars([], _T0 + 10) == []  # ya no activa: se poda, no dispara.
    # Al reaparecer es un PRIMER avistamiento otra vez: no dispara una barra rancia.
    assert frontier.due_bars([key], _T0 + 20) == []


def test_la_frontera_no_toca_ningun_camino_de_candle_corrected() -> None:
    # Cond.4: PROHIBIDO cablear candle_corrected a la frontera. Se verifica que el
    # trigger y su cableado no referencian correccion: su unica entrada es reloj+claves.
    fuente = (
        inspect.getsource(m._OrderbookFrontier)
        + inspect.getsource(m._fronterizar)
        + inspect.getsource(m._active_candle_keys)
    )
    assert "corrected" not in fuente.lower()


# -- _active_candle_keys ----------------------------------------------------


def test_active_candle_keys_filtra_solo_velas_con_timeframe() -> None:
    active = {
        _candle_key("BTC-USDT", Timeframe.M1).as_stream_key(),
        MarketStreamKey(
            exchange="binance",
            market_type=MarketType.SPOT,
            symbol="BTC-USDT",
            data_kind=MarketDataKind.TRADES,
        ).as_stream_key(),
        MarketStreamKey(
            exchange="binance",
            market_type=MarketType.SPOT,
            symbol="BTC-USDT",
            data_kind=MarketDataKind.ORDERBOOK,
        ).as_stream_key(),
        "basura:no:es:una:clave",
    }
    ctx = cast(
        IngestionContext,
        SimpleNamespace(datasource=SimpleNamespace(active=lambda: active)),
    )
    claves = _active_candle_keys(ctx)
    assert len(claves) == 1
    assert claves[0].data_kind is MarketDataKind.CANDLES
    assert claves[0].timeframe is Timeframe.M1


# -- _drain_orderbook / _fronterizar ----------------------------------------


class _FakeSnapshot:
    def __init__(self, *, revienta: bool = False) -> None:
        self.samples: list[int] = []
        self.frontiers: list[tuple[str, int]] = []  # (symbol, open_time)
        self._revienta = revienta

    def take_sample(
        self,
        book: object,
        *,
        timeframe: object,
        open_time: int,
        close_time: int,
        sample_time: int,
    ) -> bool:
        self.samples.append(sample_time)
        return True

    def take_frontier(
        self,
        book: object,
        *,
        timeframe: object,
        open_time: int,
        close_time: int,
    ) -> bool:
        if self._revienta:
            raise RuntimeError("base parpadea")
        symbol = getattr(book, "symbol", None) or "SIN-LIBRO"
        self.frontiers.append((symbol, open_time))
        return True


class _FakeEngine:
    def __init__(self, books: dict[str, object]) -> None:
        self.drained = 0
        self._books = books

    def drain_once(self) -> None:
        self.drained += 1

    def books(self) -> dict[str, object]:
        return self._books

    def book_for(self, stream_id: str) -> object:
        return self._books.get(stream_id)


def _ctx(engine: object, snapshot: object, active: set[str]) -> IngestionContext:
    return cast(
        IngestionContext,
        SimpleNamespace(
            orderbook_engine=engine,
            orderbook_snapshot=snapshot,
            datasource=SimpleNamespace(active=lambda: active),
        ),
    )


def test_drain_orderbook_sin_motor_no_hace_nada() -> None:
    ctx = cast(
        IngestionContext,
        SimpleNamespace(orderbook_engine=None, orderbook_snapshot=None),
    )
    _drain_orderbook(ctx, _OrderbookSampler(1000), _OrderbookFrontier(), 0)


def test_drain_orderbook_drena_muestrea_y_fronteriza() -> None:
    key = _candle_key("BTC-USDT", Timeframe.M1)
    ob_stream = MarketStreamKey(
        exchange="binance",
        market_type=MarketType.SPOT,
        symbol="BTC-USDT",
        data_kind=MarketDataKind.ORDERBOOK,
    ).as_stream_key()
    book = SimpleNamespace(symbol="BTC-USDT")
    engine = _FakeEngine({ob_stream: book})
    snapshot = _FakeSnapshot()
    ctx = _ctx(engine, snapshot, {key.as_stream_key()})
    sampler = _OrderbookSampler(1000)
    frontier = _OrderbookFrontier()

    _drain_orderbook(ctx, sampler, frontier, _T0 - 10)  # muestra (due); frontier regist
    _drain_orderbook(ctx, sampler, frontier, _T0 + 10)  # cruza barra -> frontera

    assert engine.drained == 2  # se drena en cada ciclo.
    assert len(snapshot.samples) == 1  # solo el primero cae en cadencia.
    assert snapshot.frontiers == [("BTC-USDT", _T0 - 60_000)]  # una, keyed a open_time.


def test_fronteriza_un_simbolo_sin_libro_dispara_igual_fire_anyway() -> None:
    # book_for devuelve None (sin libro sembrado): se pasa un OrderbookBook() vacio y
    # take_frontier decide (no publica, cond.5). Aqui el fake solo registra la llamada.
    key = _candle_key("BTC-USDT", Timeframe.M1)
    engine = _FakeEngine({})  # ningun libro sembrado.
    snapshot = _FakeSnapshot()
    ctx = _ctx(engine, snapshot, {key.as_stream_key()})
    frontier = _OrderbookFrontier()

    _drain_orderbook(ctx, _OrderbookSampler(1000), frontier, _T0 - 10)
    _drain_orderbook(ctx, _OrderbookSampler(1000), frontier, _T0 + 10)
    # Fire-anyway: take_frontier SE LLAMA aunque no haya libro (symbol del libro vacio).
    assert len(snapshot.frontiers) == 1
    assert snapshot.frontiers[0][1] == _T0 - 60_000


def test_fronteriza_aisla_el_fallo_de_una_barra() -> None:
    key = _candle_key("BTC-USDT", Timeframe.M1)
    engine = _FakeEngine({})
    snapshot = _FakeSnapshot(revienta=True)  # take_frontier revienta.
    ctx = _ctx(engine, snapshot, {key.as_stream_key()})
    frontier = _OrderbookFrontier()

    _drain_orderbook(ctx, _OrderbookSampler(1000), frontier, _T0 - 10)
    # La excepcion de la frontera NO sube: se aisla por barra y el ciclo sigue.
    _drain_orderbook(ctx, _OrderbookSampler(1000), frontier, _T0 + 10)
    assert engine.drained == 2  # el drenado ocurrio pese al fallo de la frontera.
