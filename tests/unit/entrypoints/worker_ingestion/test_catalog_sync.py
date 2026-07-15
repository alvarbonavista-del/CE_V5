"""sync_catalog trata el catalogo del exchange como ENTRADA NO CONFIABLE. SIN RED.

Hallazgo de la validacion en caliente (B12b): Binance devuelve algun instrumento con
simbolo NO-ASCII (nativo con caracteres chinos). El patron {1,20} de [A-Z0-9] lo rechaza
CORRECTAMENTE -- no es un ticker que representemos --, pero el upsert crasheaba con
CheckViolation en vez de saltarlo. Aqui se fija: se SALTA, se CUENTA y NO se lanza; una
listing rara no puede dejar sin catalogo a los otros miles de pares (fault isolation,
ADR-006). Con dobles en memoria: sync_catalog solo necesita sus puertos.
"""

from __future__ import annotations

from collections.abc import Sequence

from ce_v5.entrypoints.worker_ingestion.catalog_sync import sync_catalog
from source.families.market import (
    Instrument,
    MarketStreamKey,
    RawCandle,
    Timeframe,
)


class _FakeDataSource:
    """Datasource FALSO: entrega un catalogo fijo y recuerda el mapa que recibio.

    Implementa set_symbol_map (como el connector real) para poder comprobar que el mapa
    tampoco incluye los no representables: espeja el catalogo que de verdad se persiste.
    """

    def __init__(self, instrumentos: Sequence[Instrument]) -> None:
        self._instrumentos = list(instrumentos)
        self.map_recibido: list[Instrument] | None = None

    def open(self, key: MarketStreamKey) -> None:
        return None

    def close(self, key: MarketStreamKey) -> None:
        return None

    def active(self) -> set[str]:
        return set()

    def poll(self, timeout_ms: int) -> Sequence[RawCandle]:
        return []

    def fetch_recent(self, key: MarketStreamKey, limit: int) -> Sequence[RawCandle]:
        return []

    def list_instruments(self, market_type: str) -> Sequence[Instrument]:
        return list(self._instrumentos)

    def supported_timeframes(self) -> frozenset[Timeframe]:
        return frozenset()

    def drain_reconnected(self) -> set[str]:
        return set()

    def set_symbol_map(self, instruments: Sequence[Instrument]) -> None:
        self.map_recibido = list(instruments)


class _FakeCatalogWriter:
    """Catalogo FALSO en memoria: registra los upsert y no borra nada."""

    def __init__(self) -> None:
        self.upserts: list[tuple[str, str, str, str, str]] = []

    def upsert(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        native_symbol: str,
        status: str = "active",
    ) -> None:
        self.upserts.append((exchange, market_type, symbol, native_symbol, status))

    def deactivate_missing(
        self, exchange: str, market_type: str, present_symbols: list[str]
    ) -> int:
        return 0


def _instrumento(symbol: str, native_symbol: str) -> Instrument:
    return Instrument(
        exchange="binance",
        market_type="spot",
        symbol=symbol,
        native_symbol=native_symbol,
        active=True,
    )


def test_salta_el_no_representable_lo_cuenta_y_no_crashea() -> None:
    # Dos validos (incluido T-USDT, ticker de 1 caracter del hallazgo previo) y uno con
    # simbolo canonico no representable (base "AB安", no-ASCII). El native puede ser
    # cualquier cosa (es del exchange); lo que exigimos canonico es NUESTRO symbol.
    ds = _FakeDataSource(
        [
            _instrumento("BTC-USDT", "BTCUSDT"),
            _instrumento("T-USDT", "TUSDT"),
            _instrumento("AB安-USDT", "AB安USDT"),  # no representable
        ]
    )
    writer = _FakeCatalogWriter()

    # NO lanza: el no representable se salta, no tumba la sincronizacion.
    resultado = sync_catalog(ds, writer)

    assert resultado.active == 2
    assert resultado.not_representable == 1

    # Solo los dos validos llegaron al catalogo; el no-ASCII nunca se intento insertar.
    simbolos_upsert = {u[2] for u in writer.upserts}
    assert simbolos_upsert == {"BTC-USDT", "T-USDT"}

    # El mapa nativo->canonico del connector tampoco recibe el no representable: espeja
    # el catalogo que de verdad se persiste.
    assert ds.map_recibido is not None
    assert {i.symbol for i in ds.map_recibido} == {"BTC-USDT", "T-USDT"}


def test_ticker_de_un_caracter_pasa_el_filtro() -> None:
    # Regresion atada al hallazgo anterior: T-USDT (base de 1 caracter, Threshold) es
    # un ticker LEGITIMO y NO se salta.
    ds = _FakeDataSource([_instrumento("T-USDT", "TUSDT")])
    writer = _FakeCatalogWriter()

    resultado = sync_catalog(ds, writer)

    assert resultado.not_representable == 0
    assert resultado.active == 1
    assert writer.upserts[0][2] == "T-USDT"
