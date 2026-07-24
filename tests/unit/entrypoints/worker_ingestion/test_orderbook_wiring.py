"""Cableado del LIBRO en el worker de ingesta, EN FRIO (sin red, sin DB, sin build).

Se prueban las tres piezas puras del enganche aditivo (Tanda IV parcial):
- _as_orderbook_source: el MISMO feed visto por su cara de libro, o None si no la sirve
  (degradacion DECLARADA, como trades: un feed mudo no finge un motor sano);
- _OrderbookSampler: la cadencia del muestreo (~1/s), sin reloj propio;
- _drain_orderbook: drena el libro y, si toca por cadencia, muestrea CADA libro vivo con
  fault isolation POR libro. La FRONTERA no se toca aqui (elevada a Central).

build_context necesita DB y se cubre en integracion; aqui NADA toca la red.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from ce_v5.entrypoints.worker_ingestion.__main__ import (
    _drain_orderbook,
    _OrderbookSampler,
)
from ce_v5.entrypoints.worker_ingestion.composition import (
    IngestionContext,
    _as_orderbook_source,
)
from ce_v5.platform.market.datasource import MarketDataSourcePort
from ce_v5.platform.market.orderbook_source import OrderbookDataSourcePort


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


def test_sampler_dispara_en_el_primero_y_respeta_la_cadencia() -> None:
    sampler = _OrderbookSampler(1000)
    assert sampler.due(0) is True  # el primero SIEMPRE dispara.
    assert sampler.due(500) is False  # dentro de la ventana: no toca.
    assert sampler.due(1000) is True  # cumplida la cadencia: dispara.
    assert sampler.due(1500) is False
    assert sampler.due(2000) is True


class _FakeSnapshot:
    """Registra las muestras pedidas. Ignora el libro (aqui no se valida el motor)."""

    def __init__(self, *, revienta: bool = False) -> None:
        self.samples: list[int] = []
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
        if self._revienta:
            raise RuntimeError("base parpadea")
        self.samples.append(sample_time)
        return True


class _FakeEngine:
    """Registra los drenados y expone unos libros vivos por books()."""

    def __init__(self, books: dict[str, object]) -> None:
        self.drained = 0
        self._books = books

    def drain_once(self) -> None:
        self.drained += 1

    def books(self) -> dict[str, object]:
        return self._books


def _ctx(engine: object, snapshot: object) -> IngestionContext:
    # SimpleNamespace basta: _drain_orderbook solo mira los dos campos de orderbook.
    return cast(
        IngestionContext,
        SimpleNamespace(orderbook_engine=engine, orderbook_snapshot=snapshot),
    )


def test_drain_orderbook_sin_motor_no_hace_nada() -> None:
    # Feed sin libro: motor None. El ciclo es un no-op limpio, no una excepcion.
    _drain_orderbook(_ctx(None, None), _OrderbookSampler(1000), 0)


def test_drain_orderbook_drena_siempre_y_muestrea_a_cadencia() -> None:
    engine = _FakeEngine({"btc": object()})
    snapshot = _FakeSnapshot()
    ctx = _ctx(engine, snapshot)
    sampler = _OrderbookSampler(1000)

    _drain_orderbook(ctx, sampler, 0)  # due: drena + muestrea.
    _drain_orderbook(ctx, sampler, 500)  # dentro de ventana: drena, NO muestrea.
    _drain_orderbook(ctx, sampler, 1000)  # cadencia cumplida: drena + muestrea.

    assert engine.drained == 3  # se drena en CADA ciclo, muestree o no.
    assert snapshot.samples == [0, 1000]  # solo cuando toca por cadencia.


def test_drain_orderbook_aisla_el_fallo_de_una_muestra() -> None:
    engine = _FakeEngine({"btc": object(), "eth": object()})
    snapshot = _FakeSnapshot(revienta=True)  # take_sample siempre revienta.
    # La excepcion de la muestra NO sube: se aisla POR libro y el ciclo sigue.
    _drain_orderbook(_ctx(engine, snapshot), _OrderbookSampler(1000), 0)
    assert engine.drained == 1  # el drenado ocurrio pese al fallo del muestreo.


def test_drain_orderbook_no_muestrea_sin_libros_vivos() -> None:
    engine = _FakeEngine({})  # ningun libro sembrado todavia.
    snapshot = _FakeSnapshot()
    _drain_orderbook(_ctx(engine, snapshot), _OrderbookSampler(1000), 0)
    assert engine.drained == 1
    assert snapshot.samples == []
