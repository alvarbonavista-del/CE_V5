"""Costura END-TO-END del hueco de OKX -> footprint incompleto (P07b cierre; ADR-014).

Sella en UNA sola prueba la cadena completa que las tres capas ya cubren por separado:

  backfill OKX que AGOTA el tope de esfuerzo sin empalmar (cap silencioso de 300 + tope
  de _BACKFILL_MAX_PAGES paginas)  ->  covered=False (fail-safe)  ->  record_gap en
  market_trade_gap  ->  agregacion del footprint de una barra que SOLAPA ese hueco
  ->  market_footprint.is_complete = False  Y  la fila de OUTBOX del footprint lleva
  is_complete=False en su payload.

Contra PostgreSQL REAL y con el rol de INGESTA. La logica de truncado y cobertura es la
del conector OKX DE VERDAD (backfill_after_reconnect + _coverage_okx + bucle acotado);
solo el IO de red (_get_json) se sustituye por un fake determinista que reproduce el
historico newest-first paginado. El resto -- ingestor, writers, agregacion -- es
produccion.

MUERDE: si CUALQUIER eslabon marcara la barra como completa (el conector dando el hueco
por cubierto, el ingestor sin apuntarlo, o el footprint ignorandolo), los asserts de
is_complete=False fallarian. Es la garantia de que un backfill truncado JAMAS se publica
como una barra completa.

Base de JUGUETE: nunca datos reales (DOC_ENTREGABLES sec.5).
"""

from __future__ import annotations

import json
import os
import urllib.parse
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal

import pytest

from ce_v5.core.clock import SystemClock
from ce_v5.infra.connectors.okx.connector import (
    _BACKFILL_MAX_PAGES,
    OkxConfig,
    OkxSpotConnector,
)
from ce_v5.infra.db.market_footprint import PostgresFootprintWriter
from ce_v5.infra.db.market_trades import (
    PostgresTradeWriter,
    read_overlapping_gaps,
    read_trades_in_window,
)
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.platform.market.footprint_aggregate import TradeGap
from ce_v5.platform.market.footprint_ingestor import FootprintEngine
from ce_v5.platform.market.trade_ingestor import TradeIngestionEngine
from source.families.footprint import MarketFootprintEventType, MarketTrade
from source.families.market import (
    AggressorSide,
    CandleClosedPayload,
    LastSeenTrade,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    RawTrade,
    Timeframe,
    TradeBackfillResult,
)
from source.time import MaturityState

_DSN = os.environ.get("CE_V5_DATABASE_URL")
pytestmark = pytest.mark.skipif(_DSN is None, reason="requiere CE_V5_DATABASE_URL")

_EXCHANGE = "okx"
_MARKET_TYPE = "spot"
_SYMBOL = "BTC-USDT"
_TF = Timeframe.M1

# Barra M1 alineada. El ancla (ultimo trade contiguo previo al corte) cae DENTRO de la
# ventana; los trades del relleno caen muy lejos (event_time enorme), fuera de ella.
_W = 1_784_073_600_000
_WINDOW_END = _W + _TF.duration_ms
_ANCHOR_EVENT_TIME = _W + 10

_TRADES_KEY = MarketStreamKey(
    exchange=_EXCHANGE,
    market_type=MarketType.SPOT,
    symbol=_SYMBOL,
    data_kind=MarketDataKind.TRADES,
)


@pytest.fixture
def limpiar(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """market_trade / gap / footprint / outbox: append-only, los limpia el rol OWNER."""

    def _wipe() -> None:
        with migrator_db.transaction() as session:
            for tabla in ("market_footprint", "market_trade_gap", "market_trade"):
                session.execute(
                    f"DELETE FROM {tabla} "  # noqa: S608 - tabla literal, no hay input.
                    "WHERE exchange = %s AND market_type = %s AND symbol = %s",
                    (_EXCHANGE, _MARKET_TYPE, _SYMBOL),
                )
            session.execute("DELETE FROM outbox")

    _wipe()
    yield
    _wipe()


def _rest_historico_truncante(
    newest_id: int, page_size: int, calls: list[int | None]
) -> Callable[[str], object]:
    """Fake de _get_json de OKX: historico newest-first ENORME en paginas pequenas.

    Reproduce el cap silencioso de 300 (el connector pide EXACTAMENTE 300 y este fake lo
    verifica) y un corte tan largo que el relleno NUNCA alcanza al ancla dentro del tope
    de esfuerzo: el mas antiguo recuperado sigue > last_seen+1. event_time = _W + id,
    que para estos ids gigantes cae MUY lejos de la ventana de la barra.
    """

    def _get_json(path: str) -> object:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
        assert qs["limit"] == ["300"]  # el cap silencioso de OKX: nunca se pide mas.
        assert qs["type"] == ["1"]
        after = int(qs["after"][0]) if "after" in qs else None
        calls.append(after)
        top = newest_id if after is None else after - 1
        ids = [i for i in range(top, top - page_size, -1) if i >= 1]
        data = [
            {
                "instId": "BTC-USDT",
                "tradeId": str(i),
                "px": "66000.0",
                "sz": "0.01",
                "side": "buy" if i % 2 == 0 else "sell",
                "ts": str(_W + i),
            }
            for i in ids
        ]
        return {"code": "0", "data": data}

    return _get_json


@dataclass
class _OkxReconnectHarness:
    """Fuente para el ingestor: entrega la clave de trades reconectada UNA vez y delega
    el backfill en un OkxSpotConnector REAL (con _get_json fake). Sin red, sin socket.
    """

    connector: OkxSpotConnector
    _pendiente: set[str]

    def open(self, key: MarketStreamKey) -> None:
        return None

    def close(self, key: MarketStreamKey) -> None:
        return None

    def active(self) -> set[str]:
        return {_TRADES_KEY.as_stream_key()}

    def poll_trades(self, timeout_ms: int) -> Sequence[RawTrade]:
        return []

    def backfill_after_reconnect(
        self, key: MarketStreamKey, last_seen: LastSeenTrade
    ) -> TradeBackfillResult:
        # La cobertura la decide el conector OKX de verdad (bucle + cap + fail-safe).
        return self.connector.backfill_after_reconnect(key, last_seen)

    def drain_reconnected(self) -> set[str]:
        p = set(self._pendiente)
        self._pendiente.clear()
        return p


@dataclass(frozen=True, slots=True)
class _ReaderOnDb:
    """Adaptador de lectura del footprint sobre la base (espejo de _TradeReaderOnDb)."""

    database: PsycopgDatabase

    def trades_in_window(
        self, exchange: str, market_type: str, symbol: str, ws: int, we: int
    ) -> tuple[MarketTrade, ...]:
        with self.database.transaction() as session:
            return read_trades_in_window(session, exchange, market_type, symbol, ws, we)

    def overlapping_gaps(
        self, exchange: str, market_type: str, symbol: str, ws: int, we: int
    ) -> tuple[TradeGap, ...]:
        with self.database.transaction() as session:
            return read_overlapping_gaps(session, exchange, market_type, symbol, ws, we)


def _anchor() -> MarketTrade:
    """El ultimo trade CONTIGUO previo al corte, dentro de la ventana de la barra."""
    return MarketTrade(
        exchange=_EXCHANGE,
        market_type=MarketType.SPOT,
        symbol=_SYMBOL,
        trade_id="10",
        price=Decimal("100"),
        qty=Decimal("1"),
        aggressor_side=AggressorSide.BUY,
        event_time=_ANCHOR_EVENT_TIME,
    )


def _candle() -> CandleClosedPayload:
    return CandleClosedPayload(
        maturity_state=MaturityState.CLOSED,
        exchange=_EXCHANGE,
        market_type=MarketType.SPOT,
        symbol=_SYMBOL,
        timeframe=_TF,
        open_time=_W,
        close_time=_WINDOW_END,
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("99"),
        close=Decimal("105"),
        volume=Decimal("1"),
    )


class TestCosturaHuecoOkxFootprintIncompleto:
    def test_backfill_truncado_marca_la_barra_incompleta_y_el_outbox_lo_refleja(
        self, ingestion_db: PsycopgDatabase, limpiar: None
    ) -> None:
        writer = PostgresTradeWriter(ingestion_db)

        # 1) El ancla persistida: last_seen apuntara aqui (trade_id=10, en la barra).
        assert writer.persist(_anchor()) is True

        # 2) Conector OKX REAL con REST truncante: historico enorme, paginas pequenas ->
        #    el relleno agota el tope de esfuerzo sin empalmar con el ancla.
        connector = OkxSpotConnector(OkxConfig(backfill_page_pause_s=0.0))
        calls: list[int | None] = []
        connector._get_json = _rest_historico_truncante(  # type: ignore[assignment]  # noqa: SLF001
            1_000_000, 5, calls
        )

        # 3) El ingestor de trades corre su ciclo: dispara el backfill del stream que
        #    reconecto, lo declara NO cubierto y APUNTA el hueco en market_trade_gap.
        engine = TradeIngestionEngine(
            source=_OkxReconnectHarness(connector, {_TRADES_KEY.as_stream_key()}),
            writer=writer,
        )
        metrics = engine.drain_once()

        assert len(calls) == _BACKFILL_MAX_PAGES  # se agoto el esfuerzo (no empalmo).
        assert metrics.uncovered_gaps == 1  # covered=False -> hueco apuntado.

        # 4) El hueco esta en la base y SE SOLAPA con la ventana de la barra.
        reader = _ReaderOnDb(ingestion_db)
        gaps = reader.overlapping_gaps(
            _EXCHANGE, _MARKET_TYPE, _SYMBOL, _W, _WINDOW_END
        )
        assert len(gaps) == 1
        gap_from, _gap_to = gaps[0]
        assert gap_from == _ANCHOR_EVENT_TIME  # el hueco empieza en el ancla.

        # 5) Se agrega el footprint de esa barra por el camino de produccion.
        footprint_engine = FootprintEngine(
            reader=reader,
            writer=PostgresFootprintWriter(ingestion_db),
            clock=SystemClock(),
            component_source="test_seam",
        )
        footprint_engine.on_candle_closed(_candle(), event_time=_W)

        # 6a) El footprint persistido esta marcado INCOMPLETO (fail-safe): la barra vio
        #     el hueco solapado y NO se publica como completa.
        with ingestion_db.transaction() as session:
            fila = session.fetchone(
                "SELECT is_complete FROM market_footprint "
                "WHERE stream_key = %s AND open_time = %s",
                (_footprint_stream_key(), _W),
            )
        assert fila is not None
        assert fila[0] is False

        # 6b) Y la fila de OUTBOX (lo que un consumidor recibiria) tambien lo dice: su
        #     payload lleva is_complete=False. Ningun consumidor lo ve como completo.
        with ingestion_db.transaction() as session:
            fila_outbox = session.fetchone(
                "SELECT envelope FROM outbox WHERE stream_key = %s AND event_type = %s",
                (
                    _footprint_stream_key(),
                    MarketFootprintEventType.FOOTPRINT_CLOSED.value,
                ),
            )
        assert fila_outbox is not None
        envelope = (
            fila_outbox[0]
            if isinstance(fila_outbox[0], dict)
            else json.loads(str(fila_outbox[0]))
        )
        assert envelope["payload"]["is_complete"] is False
        # La barra SI tiene su celda (el ancla): incompleta no es lo mismo que vacia.
        assert metrics.uncovered_gaps == 1
        assert len(envelope["payload"]["cells"]) == 1


def _footprint_stream_key() -> str:
    return MarketStreamKey(
        exchange=_EXCHANGE,
        market_type=MarketType.SPOT,
        symbol=_SYMBOL,
        data_kind=MarketDataKind.FOOTPRINT,
        timeframe=_TF,
    ).as_stream_key()
