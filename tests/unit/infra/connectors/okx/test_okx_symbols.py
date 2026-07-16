"""Pruebas de la traduccion de simbolos y granularidad de OKX (T-03). Hermeticas."""

from __future__ import annotations

import pytest

from ce_v5.infra.connectors.okx.symbols import (
    SymbolTranslationError,
    TimeframeTranslationError,
    timeframe_from_channel,
    to_bar,
    to_channel,
    to_native,
)
from source.families.market import Timeframe


def test_to_native_es_identidad_para_canonico() -> None:
    assert to_native("BTC-USDT") == "BTC-USDT"


@pytest.mark.parametrize("malo", ["BTCUSDT", "", "BTC-", "-USDT", "BTC/USDT"])
def test_to_native_rechaza_no_canonico(malo: str) -> None:
    with pytest.raises(SymbolTranslationError):
        to_native(malo)


def test_to_channel_mapea_horas_y_dias_en_mayuscula() -> None:
    assert to_channel("1m") == "candle1m"
    assert to_channel("5m") == "candle5m"
    assert to_channel("15m") == "candle15m"
    assert to_channel("1h") == "candle1H"
    assert to_channel("4h") == "candle4H"
    assert to_channel("1d") == "candle1D"


def test_to_bar_devuelve_el_sufijo_okx() -> None:
    assert to_bar("1m") == "1m"
    assert to_bar("1h") == "1H"
    assert to_bar("1d") == "1D"


def test_to_channel_rechaza_timeframe_no_soportado() -> None:
    with pytest.raises(TimeframeTranslationError):
        to_channel("30m")


def test_timeframe_from_channel_es_el_inverso() -> None:
    for tf in ("1m", "5m", "15m", "1h", "4h", "1d"):
        assert timeframe_from_channel(to_channel(tf)) == tf


@pytest.mark.parametrize(
    "malo", ["tickers", "candle30m", "candle1W", "candle", "candlexx"]
)
def test_timeframe_from_channel_rechaza_lo_no_soportado(malo: str) -> None:
    with pytest.raises(TimeframeTranslationError):
        timeframe_from_channel(malo)


def test_todos_los_timeframes_canonicos_tienen_canal() -> None:
    for tf in Timeframe:
        assert to_channel(tf.value).startswith("candle")
