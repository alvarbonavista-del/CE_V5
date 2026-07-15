"""Reconexion del connector: la senal por stream, resuelta desde _deseados. SIN RED.

El IO real (abrir el socket, reconectar de verdad) se valida EN CALIENTE (B12b): es la
unica forma honesta de probar un socket. Aqui se prueba la logica PURA que decide QUE
streams marcar como reconectados -- la resolucion nombre-de-stream de Binance -> clave
canonica desde lo deseado -- y el drain que entrega-y-limpia. Se pobla _deseados A MANO
para no arrancar ningun hilo lector (open() arrancaria red).
"""

from __future__ import annotations

from ce_v5.infra.connectors.binance.connector import BinanceSpotConnector
from source.families.market import (
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    Timeframe,
)

_BTC = MarketStreamKey(
    exchange="binance",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.CANDLES,
    timeframe=Timeframe.M1,
)


def _con_deseado(*keys: MarketStreamKey) -> BinanceSpotConnector:
    connector = BinanceSpotConnector()
    # Poblar _deseados A MANO: open() arrancaria un hilo lector (red). Aqui solo se
    # prueba la logica pura de resolucion y marcado.
    for key in keys:
        connector._deseados[key.as_stream_key()] = key  # noqa: SLF001
    return connector


def test_key_for_stream_name_resuelve_lo_deseado() -> None:
    connector = _con_deseado(_BTC)
    # 'btcusdt@kline_1m' es el nombre nativo de Binance para BTC-USDT 1m.
    assert connector._key_for_stream_name("btcusdt@kline_1m") == _BTC  # noqa: SLF001
    # Un nombre que no corresponde a nada deseado -> None.
    assert connector._key_for_stream_name("ethusdt@kline_1m") is None  # noqa: SLF001


def test_marcar_reconectados_y_drenar_entrega_y_limpia() -> None:
    connector = _con_deseado(_BTC)
    # Un stream deseado (btcusdt) y uno desconocido (no deseado): solo el primero marca.
    connector._marcar_reconectados(  # noqa: SLF001
        ("btcusdt@kline_1m", "ethusdt@kline_1m")
    )

    assert connector.drain_reconnected() == {_BTC.as_stream_key()}
    # drain LIMPIA: la segunda lectura ya no lo trae (operacion normal -> vacio).
    assert connector.drain_reconnected() == set()


def test_registrar_reconexion_cuenta_reconexiones_exitosas() -> None:
    # El contador cuenta reconexiones EXITOSAS (cierre limpio incluido), no drops. El
    # disparo real (salir del recv y reconectar) es el camino de red -> validado en
    # caliente (5.18); aqui se prueba SOLO la contabilidad, separada del socket.
    connector = _con_deseado(_BTC)
    assert connector.metrics.reconnections == 0

    connector._registrar_reconexion(("btcusdt@kline_1m",))  # noqa: SLF001

    assert connector.metrics.reconnections == 1  # cuenta la reconexion
    assert connector.drain_reconnected() == {_BTC.as_stream_key()}  # y marca el stream
