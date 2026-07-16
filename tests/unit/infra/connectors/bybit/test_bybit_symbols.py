"""Pruebas de simbolos y granularidad de Bybit (T-03). Hermeticas."""

from __future__ import annotations

import pytest

from ce_v5.infra.connectors.bybit.symbols import (
    SymbolTranslationError,
    TimeframeTranslationError,
    timeframe_from_interval,
    to_interval,
    to_native,
    to_topic,
)
from source.families.market import Timeframe


def test_to_native_pega_el_simbolo() -> None:
    assert to_native("BTC-USDT") == "BTCUSDT"


@pytest.mark.parametrize("malo", ["BTCUSDT", "", "BTC-", "-USDT", "BTC/USDT"])
def test_to_native_rechaza_no_canonico(malo: str) -> None:
    with pytest.raises(SymbolTranslationError):
        to_native(malo)


def test_to_interval_usa_los_codigos_de_bybit() -> None:
    assert to_interval("1m") == "1"
    assert to_interval("5m") == "5"
    assert to_interval("15m") == "15"
    assert to_interval("1h") == "60"
    assert to_interval("4h") == "240"
    assert to_interval("1d") == "D"


def test_to_topic_arma_el_topic_de_kline() -> None:
    assert to_topic("BTC-USDT", "1h") == "kline.60.BTCUSDT"
    assert to_topic("ETH-USDT", "1d") == "kline.D.ETHUSDT"


def test_to_interval_rechaza_no_soportado() -> None:
    with pytest.raises(TimeframeTranslationError):
        to_interval("30m")


def test_timeframe_from_interval_es_el_inverso() -> None:
    for tf in ("1m", "5m", "15m", "1h", "4h", "1d"):
        assert timeframe_from_interval(to_interval(tf)) == tf


@pytest.mark.parametrize("malo", ["30", "120", "W", "M", "xx"])
def test_timeframe_from_interval_rechaza_lo_no_soportado(malo: str) -> None:
    with pytest.raises(TimeframeTranslationError):
        timeframe_from_interval(malo)


def test_todos_los_timeframes_canonicos_tienen_codigo() -> None:
    for tf in Timeframe:
        assert to_interval(tf.value)
