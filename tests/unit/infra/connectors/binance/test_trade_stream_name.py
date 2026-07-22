"""Nombre del stream de TRADES de Binance. SIN RED."""

from __future__ import annotations

import pytest

from ce_v5.infra.connectors.binance.symbols import (
    SymbolTranslationError,
    to_stream_name,
    to_trade_stream_name,
)


def test_to_trade_stream_name_va_en_minusculas() -> None:
    # Mismo fallo silencioso que en velas: en mayusculas Binance no reconoce el stream,
    # no devuelve error, y la suscripcion se queda MUDA para siempre.
    assert to_trade_stream_name("BTC-USDT") == "btcusdt@trade"
    assert to_trade_stream_name("ETH-EUR") == "etheur@trade"
    assert to_trade_stream_name("T-USDT") == "tusdt@trade"


def test_el_stream_de_trades_NO_lleva_intervalo() -> None:
    # El flujo de trades es continuo y no se bucketea a nivel de stream (ADR-014): el
    # bucketeo por barra es del footprint, que es dato DERIVADO. Por eso la funcion no
    # acepta timeframe y su nombre no lo lleva.
    nombre = to_trade_stream_name("BTC-USDT")

    assert nombre.endswith("@trade")
    assert "kline" not in nombre
    assert "1m" not in nombre


def test_no_colisiona_con_el_stream_de_velas_del_mismo_par() -> None:
    # Los dos viajan por la MISMA conexion combinada: si los nombres coincidieran, uno
    # de los dos flujos desapareceria del reparto sin que nadie se enterase.
    assert to_trade_stream_name("BTC-USDT") != to_stream_name("BTC-USDT", "1m")


@pytest.mark.parametrize("basura", ["BTCUSDT", "", "-USDT", "BTC-", "BTC"])
def test_un_simbolo_no_canonico_se_rechaza(basura: str) -> None:
    # Hereda el rechazo de to_native: el contrato usa SIEMPRE BASE-QUOTE, y la forma
    # nativa del exchange no vale aqui.
    with pytest.raises(SymbolTranslationError):
        to_trade_stream_name(basura)
