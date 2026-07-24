"""Nombre del stream de LIBRO (orderbook) de Binance. SIN RED."""

from __future__ import annotations

import pytest

from ce_v5.infra.connectors.binance.symbols import (
    SymbolTranslationError,
    to_orderbook_stream_name,
    to_stream_name,
    to_trade_stream_name,
)


def test_to_orderbook_stream_name_va_en_minusculas() -> None:
    # Mismo fallo silencioso que en velas/trades: en mayusculas Binance no reconoce el
    # stream, no da error, y la suscripcion se queda MUDA para siempre.
    assert to_orderbook_stream_name("BTC-USDT") == "btcusdt@depth@100ms"
    assert to_orderbook_stream_name("ETH-EUR") == "etheur@depth@100ms"
    assert to_orderbook_stream_name("T-USDT") == "tusdt@depth@100ms"


def test_el_stream_de_libro_NO_lleva_intervalo() -> None:
    # El libro es continuo y su granularidad es depth/channel, no un timeframe a nivel
    # de stream (ADR-014): por eso el nombre no lleva timeframe, solo la cadencia
    # @100ms.
    nombre = to_orderbook_stream_name("BTC-USDT")
    assert nombre.endswith("@depth@100ms")
    assert "kline" not in nombre
    assert "1m" not in nombre


def test_no_colisiona_con_velas_ni_con_trades_del_mismo_par() -> None:
    # Los tres viajan por la MISMA conexion combinada: si dos nombres coincidieran, un
    # flujo desapareceria del reparto sin que nadie se enterase.
    libro = to_orderbook_stream_name("BTC-USDT")
    assert libro != to_stream_name("BTC-USDT", "1m")
    assert libro != to_trade_stream_name("BTC-USDT")


@pytest.mark.parametrize("basura", ["BTCUSDT", "", "-USDT", "BTC-", "BTC"])
def test_un_simbolo_no_canonico_se_rechaza(basura: str) -> None:
    with pytest.raises(SymbolTranslationError):
        to_orderbook_stream_name(basura)
