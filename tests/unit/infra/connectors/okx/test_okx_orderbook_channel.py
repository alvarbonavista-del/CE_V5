"""Canal WS del LIBRO (orderbook) de OKX. SIN RED."""

from __future__ import annotations

from ce_v5.infra.connectors.okx.symbols import (
    is_orderbook_channel,
    is_trade_channel,
    to_channel,
    to_orderbook_channel,
    to_trade_channel,
)


def test_el_canal_del_libro_es_books() -> None:
    assert to_orderbook_channel() == "books"


def test_el_canal_del_libro_no_lleva_bar() -> None:
    # El libro no se bucketea por intervalo (ADR-014): su canal no lleva sufijo de bar,
    # a diferencia de las velas (candle1H).
    canal = to_orderbook_channel()
    assert "candle" not in canal
    assert canal != to_channel("1h")
    assert canal != to_trade_channel()


def test_is_orderbook_channel_reconoce_solo_el_libro() -> None:
    # El connector enruta el mensaje entrante por su canal: el del libro no puede
    # confundirse con el de velas ni con el de trades.
    assert is_orderbook_channel("books") is True
    assert is_orderbook_channel("trades-all") is False
    assert is_orderbook_channel("candle1H") is False
    assert is_trade_channel(to_orderbook_channel()) is False
