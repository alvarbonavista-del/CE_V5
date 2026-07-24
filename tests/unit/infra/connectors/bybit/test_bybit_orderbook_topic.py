"""Topic de suscripcion del LIBRO (orderbook) de Bybit v5. SIN RED."""

from __future__ import annotations

import pytest

from ce_v5.infra.connectors.bybit.symbols import (
    SymbolTranslationError,
    to_orderbook_topic,
    to_topic,
    to_trade_topic,
)


def test_topic_del_libro_con_profundidad_por_defecto() -> None:
    # Profundidad 200 por defecto (fuente fijada por Central): orderbook.200.<native>.
    assert to_orderbook_topic("BTC-USDT") == "orderbook.200.BTCUSDT"
    assert to_orderbook_topic("ETH-USDT") == "orderbook.200.ETHUSDT"


def test_la_profundidad_es_parametrizable() -> None:
    # Su tasa entra en la medicion del paso 8 (cond.6): la profundidad se puede cambiar.
    assert to_orderbook_topic("BTC-USDT", depth=50) == "orderbook.50.BTCUSDT"
    assert to_orderbook_topic("BTC-USDT", depth=1) == "orderbook.1.BTCUSDT"


def test_no_colisiona_con_velas_ni_con_trades_del_mismo_par() -> None:
    libro = to_orderbook_topic("BTC-USDT")
    assert libro != to_topic("BTC-USDT", "1m")
    assert libro != to_trade_topic("BTC-USDT")


@pytest.mark.parametrize("basura", ["BTCUSDT", "", "-USDT", "BTC-", "BTC"])
def test_un_simbolo_no_canonico_se_rechaza(basura: str) -> None:
    with pytest.raises(SymbolTranslationError):
        to_orderbook_topic(basura)
