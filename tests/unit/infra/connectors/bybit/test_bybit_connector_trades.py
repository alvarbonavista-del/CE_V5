"""Conector de Bybit, cara de TRADES: backfill de UNA llamada, enrutado y reconexion.

TODO SIN RED. El socket real se valida en caliente (5.18). Aqui se prueba lo que el CI
SI puede cazar:
- el backfill hace UNA sola llamada a recent-trade con limit=60 (el techo real, que
  Bybit capa en silencio) y NO asume mas de lo devuelto: no pagina;
- el enrutado por prefijo de topic: 'publicTrade.' -> cola de trades, 'kline.' -> velas;
- la reconexion marca TAMBIEN la clave de trades;
- set_symbol_map resuelve el native del topic de trades igual que en velas.
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable

from ce_v5.infra.connectors.bybit.connector import (
    _REST_TRADES_MAX,
    BybitSpotConnector,
)
from source.families.market import (
    Instrument,
    LastSeenTrade,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    Timeframe,
)

_EVENT_BASE = 1_784_793_000_000

_TRADES_KEY = MarketStreamKey(
    exchange="bybit",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.TRADES,  # SIN timeframe: el contrato lo prohibe (ADR-014).
)
_CANDLE_KEY = MarketStreamKey(
    exchange="bybit",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.CANDLES,
    timeframe=Timeframe.M1,
)


def _con_mapa() -> BybitSpotConnector:
    return BybitSpotConnector(native_to_canonical={"BTCUSDT": "BTC-USDT"})


def _rest_recent(
    newest_id: int, count: int, calls: list[str]
) -> Callable[[str], object]:
    """Fake de _get_json: recent-trade newest-first con `count` filas contiguas.

    Reproduce a Bybit: retCode=0, result.list, NEWEST-FIRST, y el cap de 60 (el fake da
    como mucho lo que se le pida, pero el connector siempre pide 60 -- se verifica).
    """

    def _get_json(path: str) -> object:
        calls.append(path)
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
        # El connector pide EXACTAMENTE el techo (60): nunca mas.
        assert qs["limit"] == [str(_REST_TRADES_MAX)]
        assert qs["category"] == ["spot"]
        ids = list(range(newest_id, newest_id - count, -1))  # newest-first
        lista = [
            {
                "execId": str(i),
                "price": "65000.0",
                "size": "0.01",
                "side": "Buy" if i % 2 == 0 else "Sell",
                "time": str(_EVENT_BASE + i),
            }
            for i in ids
        ]
        return {"retCode": 0, "result": {"list": lista}}

    return _get_json


def test_backfill_una_llamada_cubre_el_hueco_pequeno() -> None:
    connector = _con_mapa()
    calls: list[str] = []
    connector._get_json = _rest_recent(1000, 60, calls)  # type: ignore[assignment]  # noqa: SLF001

    # last_seen=970 cae dentro de la ventana de 60 (1000..941): cubierto.
    resultado = connector.backfill_after_reconnect(
        _TRADES_KEY, LastSeenTrade(trade_id="970", event_time_ms=_EVENT_BASE + 970)
    )

    assert len(calls) == 1  # UNA sola llamada, no pagina
    assert resultado.covered is True
    assert resultado.gap_from_event_time_ms is None
    assert len(resultado.raw_trades) == 60


def test_backfill_hueco_mayor_que_la_ventana_marca_incompleto() -> None:
    # EL CAMINO COMUN en Bybit: el corte excede los 60 trades de la ventana. UNA
    # llamada, no alcanza el last_seen -> covered=False, hueco acotado (fail-safe).
    connector = _con_mapa()
    calls: list[str] = []
    connector._get_json = _rest_recent(1000, 60, calls)  # type: ignore[assignment]  # noqa: SLF001

    resultado = connector.backfill_after_reconnect(
        _TRADES_KEY, LastSeenTrade(trade_id="500", event_time_ms=_EVENT_BASE + 500)
    )

    assert len(calls) == 1  # sigue siendo UNA llamada: no pagina para tapar mas
    assert resultado.covered is False
    assert resultado.gap_from_event_time_ms == _EVENT_BASE + 500
    # el extremo superior del hueco es el trade mas antiguo que el REST alcanzo (941).
    assert resultado.gap_to_event_time_ms == _EVENT_BASE + 941


def test_backfill_primera_conexion_no_hay_hueco() -> None:
    connector = _con_mapa()
    calls: list[str] = []
    connector._get_json = _rest_recent(1000, 60, calls)  # type: ignore[assignment]  # noqa: SLF001

    resultado = connector.backfill_after_reconnect(
        _TRADES_KEY, LastSeenTrade(trade_id=None, event_time_ms=None)
    )

    assert len(calls) == 1
    assert resultado.covered is True


def test_topics_de_incluye_velas_y_trades() -> None:
    connector = _con_mapa()
    topics = connector._topics_de((_TRADES_KEY, _CANDLE_KEY))  # noqa: SLF001
    assert set(topics) == {"publicTrade.BTCUSDT", "kline.1.BTCUSDT"}


def test_la_reconexion_marca_tambien_la_clave_de_trades() -> None:
    connector = _con_mapa()
    connector._deseados[_TRADES_KEY.as_stream_key()] = _TRADES_KEY  # noqa: SLF001
    connector._deseados[_CANDLE_KEY.as_stream_key()] = _CANDLE_KEY  # noqa: SLF001

    connector._registrar_reconexion((_TRADES_KEY, _CANDLE_KEY))  # noqa: SLF001

    assert connector.metrics.reconnections == 1
    assert connector.drain_reconnected() == {
        _TRADES_KEY.as_stream_key(),
        _CANDLE_KEY.as_stream_key(),
    }


def test_encolar_enruta_trades_y_velas_por_el_prefijo_del_topic() -> None:
    connector = _con_mapa()

    mensaje_trade = json.dumps(
        {
            "topic": "publicTrade.BTCUSDT",
            "type": "snapshot",
            "data": [
                {
                    "i": "555",
                    "p": "65000.0",
                    "v": "0.02",
                    "S": "Sell",
                    "T": str(_EVENT_BASE + 555),
                }
            ],
        }
    )
    connector._encolar(mensaje_trade)  # noqa: SLF001

    trades = connector.poll_trades(100)
    assert [t.trade_id for t in trades] == ["555"]
    assert trades[0].aggressor_side == "sell"
    assert connector.poll(0) == []  # nada se colo en la cola de velas


def test_set_symbol_map_resuelve_el_native_del_topic_de_trades() -> None:
    # Sin mapa, un publicTrade no resuelve a canonico y se cuenta como error; tras
    # set_symbol_map (el MISMO que usan las velas), el trade entra.
    connector = BybitSpotConnector()
    mensaje = json.dumps(
        {
            "topic": "publicTrade.ETHUSDT",
            "data": [
                {
                    "i": "10",
                    "p": "3000.0",
                    "v": "0.5",
                    "S": "Buy",
                    "T": str(_EVENT_BASE + 10),
                }
            ],
        }
    )
    connector._encolar(mensaje)  # noqa: SLF001
    assert connector.poll_trades(0) == []
    assert connector.metrics.translation_errors == 1

    connector.set_symbol_map(
        [
            Instrument(
                exchange="bybit",
                market_type="spot",
                symbol="ETH-USDT",
                native_symbol="ETHUSDT",
                active=True,
            )
        ]
    )
    connector._encolar(mensaje)  # noqa: SLF001
    trades = connector.poll_trades(100)
    assert [t.symbol for t in trades] == ["ETH-USDT"]
