"""Traduccion de un trade de OKX ('trades-all') a RawTrade (P07b 3a-ii). Hermetica.

Aqui vive el error de logica que el CI SI puede cazar: que 'side' se copie como lado
agresor, que price/qty viajen como TEXTO, que tradeId y ts sean numericos, y que un
mensaje malformado falle en ALTO (OkxTranslationError) en vez de colar un RawTrade a
medias. El IO (el socket real) se valida en caliente (regla 5.18).
"""

from __future__ import annotations

import pytest

from ce_v5.infra.connectors.okx.translate import (
    OkxTranslationError,
    raw_trade_from_okx,
)


def _msg(**overrides: object) -> dict[str, object]:
    # Un elemento del array 'data' de un push de 'trades-all' (OKX v5).
    base: dict[str, object] = {
        "instId": "BTC-USDT",
        "tradeId": "1035518850",
        "px": "66000.10",
        "sz": "0.0123",
        "side": "buy",
        "ts": "1700000000000",
    }
    base.update(overrides)
    return base


def test_traduce_un_trade_de_compra() -> None:
    raw = raw_trade_from_okx(_msg(), "BTC-USDT", "spot")
    assert raw.exchange == "okx"
    assert raw.market_type == "spot"
    assert raw.symbol == "BTC-USDT"
    assert raw.trade_id == "1035518850"
    assert raw.price == "66000.10"
    assert raw.qty == "0.0123"
    assert raw.aggressor_side == "buy"
    assert raw.event_time_ms == 1700000000000
    # El tradeId de OKX es monotono y contiguo: sirve de secuencia de origen.
    assert raw.source_sequence == 1035518850


def test_el_lado_se_lee_de_side_no_se_estima() -> None:
    # OKX publica 'side' = lado del TAKER. Se copia tal cual; que sea un valor legitimo
    # lo decide la frontera de confianza, no este traductor.
    raw = raw_trade_from_okx(_msg(side="sell"), "BTC-USDT", "spot")
    assert raw.aggressor_side == "sell"


def test_precios_se_conservan_como_texto() -> None:
    raw = raw_trade_from_okx(_msg(px="0.000000010", sz="1000000"), "PEPE-USDT", "spot")
    assert raw.price == "0.000000010"
    assert raw.qty == "1000000"


def test_un_side_desconocido_se_traduce_igual_y_lo_caza_la_frontera() -> None:
    # El traductor NO valida el lado (eso es de la frontera): un 'taker' pasa por aqui y
    # es la normalizacion quien lo rechaza. Se comprueba que el traductor no lo filtra.
    raw = raw_trade_from_okx(_msg(side="taker"), "BTC-USDT", "spot")
    assert raw.aggressor_side == "taker"


@pytest.mark.parametrize("clave", ["tradeId", "px", "sz", "side", "ts"])
def test_falta_una_clave_obligatoria_falla_fuerte(clave: str) -> None:
    msg = _msg()
    del msg[clave]
    with pytest.raises(OkxTranslationError):
        raw_trade_from_okx(msg, "BTC-USDT", "spot")


def test_tradeId_no_numerico_falla_fuerte() -> None:
    # El tradeId de OKX es un entero por contrato: uno no numerico es un mensaje
    # malformado, no un RawTrade a medias.
    with pytest.raises(OkxTranslationError):
        raw_trade_from_okx(_msg(tradeId="no-es-id"), "BTC-USDT", "spot")


def test_ts_no_numerico_falla_fuerte() -> None:
    # ts en ms; si no es numerico, se rechaza en ALTO en vez de dejar escapar una
    # ValueError desnuda por el except del lector.
    with pytest.raises(OkxTranslationError):
        raw_trade_from_okx(_msg(ts="ayer"), "BTC-USDT", "spot")


def test_no_es_un_objeto_falla_fuerte() -> None:
    with pytest.raises(OkxTranslationError):
        raw_trade_from_okx(["BTC-USDT", "1"], "BTC-USDT", "spot")  # type: ignore[arg-type]
