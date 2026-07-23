"""El topic de trades de Bybit: 'publicTrade.<native>'. SIN RED.

Se fija por test para que no derive: un cambio de topic dejaria la suscripcion muda
(Bybit no da error, simplemente no llegan trades), que es el peor fallo -- silencioso.
"""

from __future__ import annotations

from ce_v5.infra.connectors.bybit.symbols import to_trade_topic


def test_topic_de_trades_es_publictrade_native() -> None:
    # native PEGADO (BTCUSDT), no canonico: es la forma que Bybit entiende en el topic.
    assert to_trade_topic("BTC-USDT") == "publicTrade.BTCUSDT"


def test_topic_de_trades_traduce_el_simbolo_a_nativo() -> None:
    assert to_trade_topic("SOL-USDT") == "publicTrade.SOLUSDT"
