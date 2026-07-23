"""Traduccion de trades de Bybit (WS y REST) a RawTrade (P07b 3a-ii-Bybit). Hermetica.

Bybit da los MISMOS hechos con NOMBRES de campo distintos en WS ('i/p/v/S/T') y en REST
('execId/price/size/side/time'): por eso hay dos traducciones. El CI caza aqui que 'S'/
'side' se lea como lado agresor (Buy->buy, Sell->sell), que los precios viajen como
TEXTO, que el id/tiempo sean numericos, que los extra se ignoren y que lo malformado
falle en ALTO. El socket real se valida en caliente (regla 5.18).
"""

from __future__ import annotations

import pytest

from ce_v5.infra.connectors.bybit.translate import (
    BybitTranslationError,
    raw_trade_from_bybit_rest,
    raw_trade_from_bybit_ws,
)


def _ws(**overrides: object) -> dict[str, object]:
    # Un elemento del 'data' de un push publicTrade (con extras que deben ignorarse).
    base: dict[str, object] = {
        "i": "2290000001182375771",
        "p": "65452.2",
        "v": "0.001036",
        "S": "Buy",
        "T": "1784793475660",
        "s": "BTCUSDT",
        "seq": 42,
        "BT": False,
        "RPI": False,
    }
    base.update(overrides)
    return base


def _rest(**overrides: object) -> dict[str, object]:
    # Una fila de recent-trade (con extras que deben ignorarse).
    base: dict[str, object] = {
        "execId": "2290000001182376173",
        "price": "65446.3",
        "size": "0.001036",
        "side": "Sell",
        "time": "1784793525468",
        "symbol": "BTCUSDT",
        "isBlockTrade": False,
        "isRPITrade": False,
    }
    base.update(overrides)
    return base


def test_ws_traduce_una_compra() -> None:
    raw = raw_trade_from_bybit_ws(_ws(), "BTC-USDT", "spot")
    assert raw.exchange == "bybit"
    assert raw.market_type == "spot"
    assert raw.symbol == "BTC-USDT"
    assert raw.trade_id == "2290000001182375771"
    assert raw.price == "65452.2"
    assert raw.qty == "0.001036"
    assert raw.aggressor_side == "buy"
    assert raw.event_time_ms == 1784793475660
    assert raw.source_sequence == 2290000001182375771


def test_rest_traduce_una_venta_con_otros_nombres() -> None:
    raw = raw_trade_from_bybit_rest(_rest(), "BTC-USDT", "spot")
    assert raw.trade_id == "2290000001182376173"
    assert raw.price == "65446.3"
    assert raw.qty == "0.001036"
    assert raw.aggressor_side == "sell"
    assert raw.event_time_ms == 1784793525468
    assert raw.source_sequence == 2290000001182376173


def test_side_se_lee_del_flag_del_exchange_no_se_estima() -> None:
    assert raw_trade_from_bybit_ws(
        _ws(S="Sell"), "BTC-USDT", "spot"
    ).aggressor_side == ("sell")
    assert (
        raw_trade_from_bybit_rest(_rest(side="Buy"), "BTC-USDT", "spot").aggressor_side
        == "buy"
    )


def test_un_side_desconocido_pasa_tal_cual_y_lo_caza_la_frontera() -> None:
    # El traductor NO valida el lado (eso es de la frontera): un valor raro pasa TAL
    # CUAL para que la normalizacion lo rechace, no se mangonea aqui.
    assert raw_trade_from_bybit_ws(
        _ws(S="Taker"), "BTC-USDT", "spot"
    ).aggressor_side == ("Taker")


def test_precios_se_conservan_como_texto() -> None:
    raw = raw_trade_from_bybit_ws(
        _ws(p="0.000000010", v="1000000"), "PEPE-USDT", "spot"
    )
    assert raw.price == "0.000000010"
    assert raw.qty == "1000000"


@pytest.mark.parametrize("clave", ["i", "p", "v", "S", "T"])
def test_ws_falta_una_clave_falla_fuerte(clave: str) -> None:
    msg = _ws()
    del msg[clave]
    with pytest.raises(BybitTranslationError):
        raw_trade_from_bybit_ws(msg, "BTC-USDT", "spot")


@pytest.mark.parametrize("clave", ["execId", "price", "size", "side", "time"])
def test_rest_falta_una_clave_falla_fuerte(clave: str) -> None:
    row = _rest()
    del row[clave]
    with pytest.raises(BybitTranslationError):
        raw_trade_from_bybit_rest(row, "BTC-USDT", "spot")


def test_id_no_numerico_falla_fuerte() -> None:
    with pytest.raises(BybitTranslationError):
        raw_trade_from_bybit_ws(_ws(i="no-es-id"), "BTC-USDT", "spot")


def test_tiempo_no_numerico_falla_fuerte() -> None:
    with pytest.raises(BybitTranslationError):
        raw_trade_from_bybit_rest(_rest(time="ayer"), "BTC-USDT", "spot")


def test_no_es_un_objeto_falla_fuerte() -> None:
    with pytest.raises(BybitTranslationError):
        raw_trade_from_bybit_ws(["i", "1"], "BTC-USDT", "spot")  # type: ignore[arg-type]
    with pytest.raises(BybitTranslationError):
        raw_trade_from_bybit_rest(["execId"], "BTC-USDT", "spot")  # type: ignore[arg-type]
