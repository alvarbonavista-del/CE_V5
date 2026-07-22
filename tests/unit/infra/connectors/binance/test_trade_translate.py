"""Traduccion de mensajes @trade de Binance. SIN RED.

Los mensajes tienen la forma EXACTA que documenta Binance para el stream
<symbol>@trade, grabada aqui a proposito: probarla contra el Binance real seria probar
la red, no nuestra logica, y ademas ataria el CI a un tercero que puede estar caido o
banearnos la IP.

EL MULTIPLEXADO NO SE PRUEBA AQUI, Y SE DECLARA (regla 5.18): que velas y trades
viajen por la misma conexion y se enruten por el campo 'e' es camino de SOCKET, y el CI
es hermetico. Eso se valida EN CALIENTE contra el Binance real. Lo que si se prueba a
fondo es esto: la traduccion, que es donde de verdad se puede meter un error de logica.
"""

from __future__ import annotations

from typing import Any

import pytest

from ce_v5.infra.connectors.binance.translate import (
    BinanceTranslationError,
    raw_trade_from_binance,
)

_TRADE_TIME = 1_784_073_612_345
_EVENT_TIME = _TRADE_TIME + 3  # el exchange EMITE unos ms despues del hecho.


def _mensaje(**overrides: Any) -> dict[str, Any]:
    """Un @trade de Binance con su forma REAL (web-socket-streams.md)."""
    msg: dict[str, Any] = {
        "e": "trade",
        "E": _EVENT_TIME,  # cuando el exchange EMITIO el mensaje
        "s": "BTCUSDT",  # simbolo NATIVO
        "t": 12_345_678,  # trade id (monotono por simbolo)
        "p": "104250.13000000",  # los precios llegan como TEXTO
        "q": "0.00351000",  # la cantidad, tambien
        "T": _TRADE_TIME,  # cuando OCURRIO el trade
        "m": False,  # is the buyer the market maker?
        "M": True,  # ignorado por Binance ("ignore")
    }
    msg.update(overrides)
    return msg


def test_traduce_un_trade_completo() -> None:
    trade = raw_trade_from_binance(_mensaje(), "BTC-USDT", "spot")

    assert trade.exchange == "binance"
    assert trade.market_type == "spot"
    # El canonico lo pone el LLAMADOR (lo consulta en el catalogo): de 'BTCUSDT' no se
    # puede deducir donde parte, y adivinarlo seria escribir el precio de una moneda en
    # el historico de otra.
    assert trade.symbol == "BTC-USDT"
    assert trade.trade_id == "12345678"


def test_el_precio_y_la_cantidad_viajan_como_TEXTO_INTACTO() -> None:
    # NI float, NI redondeo, NI "limpieza" de ceros. Un float binario no representa
    # 0.1 exacto y en M5 esto es dinero: los ceros de cola se conservan tal cual.
    trade = raw_trade_from_binance(_mensaje(), "BTC-USDT", "spot")

    assert trade.price == "104250.13000000"
    assert trade.qty == "0.00351000"
    assert isinstance(trade.price, str)
    assert isinstance(trade.qty, str)


def test_m_false_es_agresor_COMPRADOR() -> None:
    # m = "is the buyer the market maker?". FALSE -> el maker fue el vendedor, luego
    # quien cruzo el spread fue el COMPRADOR.
    trade = raw_trade_from_binance(_mensaje(m=False), "BTC-USDT", "spot")

    assert trade.aggressor_side == "buy"


def test_m_true_es_agresor_VENDEDOR() -> None:
    # TRUE -> el comprador estaba en el libro, luego el taker fue el VENDEDOR. Este par
    # de tests es la razon de ser del footprint reproducible: el lado es un HECHO que
    # publica el exchange, no una estimacion nuestra.
    trade = raw_trade_from_binance(_mensaje(m=True), "BTC-USDT", "spot")

    assert trade.aggressor_side == "sell"


def test_el_event_time_es_el_del_TRADE_no_el_de_la_emision() -> None:
    # ADR-007: el event_time lo fija el ORIGEN del hecho. 'T' es cuando ocurrio el
    # trade; 'E' es cuando el exchange mando el mensaje. Coger 'E' fecharia el hecho
    # unos milisegundos tarde y el footprint caeria en la barra equivocada justo en la
    # frontera, que es donde mas duele.
    trade = raw_trade_from_binance(_mensaje(), "BTC-USDT", "spot")

    assert trade.event_time_ms == _TRADE_TIME
    assert trade.event_time_ms != _EVENT_TIME


def test_source_sequence_es_el_trade_id() -> None:
    # El trade id de Binance es monotono por simbolo: sirve de secuencia de origen sin
    # inventarse nada.
    trade = raw_trade_from_binance(_mensaje(t=987_654_321), "BTC-USDT", "spot")

    assert trade.source_sequence == 987_654_321
    assert trade.trade_id == "987654321"


@pytest.mark.parametrize("clave", ["t", "p", "q", "T", "m"])
def test_falta_una_clave_y_NO_se_traduce_a_medias(clave: str) -> None:
    # NUNCA se devuelve un RawTrade incompleto: el lector convierte la excepcion en una
    # metrica observable, no en un dato. Un trade a medias es una celda de footprint
    # que miente.
    msg = _mensaje()
    del msg[clave]

    with pytest.raises(BinanceTranslationError):
        raw_trade_from_binance(msg, "BTC-USDT", "spot")


def test_el_modulo_NO_valida_dominio() -> None:
    # SOLO TRADUCE FORMATO. Un precio negativo o un cero pasan de largo: los rechaza la
    # FRONTERA DE CONFIANZA (platform/market/trade_normalize.py), que es UNA sola para
    # los tres exchanges. Si cada conector validase lo suyo, una de las tres
    # validaciones seria la mas floja y el atacante elegiria esa.
    trade = raw_trade_from_binance(_mensaje(p="-1", q="0"), "BTC-USDT", "spot")

    assert trade.price == "-1"
    assert trade.qty == "0"
