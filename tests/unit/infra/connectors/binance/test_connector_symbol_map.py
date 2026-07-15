"""El mapa nativo -> canonico del connector real (cierre de B6b). SIN RED.

De 'BTCUSDT' NO se puede deducir donde parte (BTC-USDT o BT-CUSDT): es una CONSULTA al
catalogo, no un calculo. El connector nace con ese mapa VACIO y hay que poblarlo con
set_symbol_map desde el catalogo sincronizado. Aqui se demuestra, sin abrir un socket,
que:

- CON el mapa, un kline REAL de BTCUSDT se traduce a la clave canonica BTC-USDT;
- SIN el mapa, ese mismo kline se cuenta como translation_error y NO produce vela
  (fault isolation observable, JAMAS un descarte en silencio).

Se ejercita el camino interno _encolar: es EXACTAMENTE la ruta que recorre un mensaje
del WebSocket (parsear -> resolver canonico -> traducir -> encolar), menos el socket. El
IO real (abrir la conexion) NO se prueba en CI: se valida en caliente (B12, regla 5.18).
"""

from __future__ import annotations

import json
from typing import Any

from ce_v5.infra.connectors.binance.connector import BinanceSpotConnector
from source.families.market import Instrument

_OPEN = 1_784_073_600_000
_CLOSE = _OPEN + 59_999

_BTC = Instrument(
    exchange="binance",
    market_type="spot",
    symbol="BTC-USDT",  # canonico
    native_symbol="BTCUSDT",  # como lo llama Binance
    active=True,
)


def _mensaje_kline_btcusdt() -> str:
    """Un kline de Binance con su forma REAL (web-socket-streams.md), como texto."""
    kline: dict[str, Any] = {
        "t": _OPEN,
        "T": _CLOSE,
        "s": "BTCUSDT",  # simbolo NATIVO
        "i": "1m",
        "f": 100,
        "L": 200,
        "o": "100.00000000",
        "c": "105.00000000",
        "h": "110.00000000",
        "l": "95.00000000",
        "v": "12.50000000",
        "n": 50,
        "x": True,  # vela CERRADA
        "q": "1300.0",
        "V": "6.0",
        "Q": "600.0",
        "B": "0",
    }
    sobre = {
        "e": "kline",
        "E": _OPEN + 42,  # event_time DEL EXCHANGE
        "s": "BTCUSDT",
        "k": kline,
    }
    return json.dumps(sobre)


def test_con_mapa_un_kline_de_btcusdt_se_traduce_a_la_clave_canonica() -> None:
    connector = BinanceSpotConnector()
    connector.set_symbol_map([_BTC])

    # La MISMA ruta que un mensaje del WebSocket, sin el socket.
    connector._encolar(_mensaje_kline_btcusdt())  # noqa: SLF001

    velas = connector.poll(timeout_ms=0)
    assert len(velas) == 1
    # BTCUSDT (nativo) resuelto a BTC-USDT (canonico) CONSULTANDO el mapa, sin adivinar.
    assert velas[0].symbol == "BTC-USDT"
    assert velas[0].exchange == "binance"
    assert velas[0].open_time_ms == _OPEN
    assert velas[0].is_closed is True
    assert connector.metrics.translation_errors == 0


def test_sin_mapa_el_mismo_kline_se_cuenta_como_error_y_no_produce_vela() -> None:
    connector = BinanceSpotConnector()  # mapa VACIO: nadie llamo a set_symbol_map.

    connector._encolar(_mensaje_kline_btcusdt())  # noqa: SLF001

    # Sin el catalogo, 'BTCUSDT' no resuelve a canonico: se descarta y se CUENTA (fault
    # isolation observable), nunca en silencio. Y la cola queda vacia: cero velas.
    assert connector.metrics.translation_errors == 1
    assert connector.poll(timeout_ms=0) == []


def test_set_symbol_map_reemplaza_el_mapa_no_acumula() -> None:
    # Un resync refleja el catalogo VIGENTE: si un par desaparece del catalogo, deja de
    # resolver. set_symbol_map reemplaza el mapa entero, no acumula historia.
    connector = BinanceSpotConnector()
    connector.set_symbol_map([_BTC])
    connector.set_symbol_map(
        [
            Instrument(
                exchange="binance",
                market_type="spot",
                symbol="ETH-USDT",
                native_symbol="ETHUSDT",
                active=True,
            )
        ]
    )

    # BTCUSDT ya no esta en el catalogo vigente: su kline se descarta y se cuenta.
    connector._encolar(_mensaje_kline_btcusdt())  # noqa: SLF001
    assert connector.metrics.translation_errors == 1
    assert connector.poll(timeout_ms=0) == []
