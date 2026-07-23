"""El canal WS de trades de OKX: 'trades-all', y su reconocimiento. SIN RED.

'trades-all' (individuales) frente a 'trades' (agregado) es la decision LOCKED de la
tanda, confirmada en el sondeo en vivo. Aqui se fija por test para que no derive en
silencio: un cambio a 'trades' pasaria a ingerir trades AGREGADOS y el footprint
mentiria sin que nada fallase.
"""

from __future__ import annotations

from ce_v5.infra.connectors.okx.symbols import is_trade_channel, to_trade_channel


def test_el_canal_de_trades_es_trades_all() -> None:
    # NO 'trades' (agregado): 'trades-all' da los individuales que el footprint suma.
    assert to_trade_channel() == "trades-all"


def test_reconoce_el_canal_de_trades() -> None:
    assert is_trade_channel("trades-all") is True


def test_no_confunde_velas_ni_el_canal_agregado_con_trades() -> None:
    # 'candle1m' es de velas; 'trades' (a secas) es el AGREGADO que NO usamos: ninguno
    # debe enrutarse como el flujo de trades individuales.
    assert is_trade_channel("candle1m") is False
    assert is_trade_channel("trades") is False
    assert is_trade_channel("") is False
