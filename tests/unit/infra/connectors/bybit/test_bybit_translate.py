"""Pruebas de la traduccion de velas de Bybit a RawCandle (T-03). Hermeticas."""

from __future__ import annotations

import pytest

from ce_v5.infra.connectors.bybit.translate import (
    BybitTranslationError,
    raw_candle_from_bybit_rest,
    raw_candle_from_bybit_ws,
    supported_bybit_timeframes,
)
from source.families.market import Timeframe


def _obj(confirm: bool) -> dict[str, object]:
    return {
        "start": 1784202180000,
        "end": 1784202239999,
        "interval": "1",
        "open": "64255.2",
        "close": "64263.7",
        "high": "64300.0",
        "low": "64200.0",
        "volume": "12.5",
        "turnover": "803291.0",
        "confirm": confirm,
        "timestamp": 1784202210000,
    }


def test_traduce_una_vela_ws_cerrada() -> None:
    raw = raw_candle_from_bybit_ws(_obj(True), "BTC-USDT", "spot", "1m")
    assert raw.exchange == "bybit"
    assert raw.symbol == "BTC-USDT"
    assert raw.timeframe == "1m"
    assert raw.open_time_ms == 1784202180000
    assert raw.close_time_ms == 1784202239999
    assert raw.open == "64255.2"
    assert raw.close == "64263.7"
    assert raw.volume == "12.5"
    assert raw.is_closed is True
    assert raw.event_time_ms == 1784202210000
    assert raw.source_sequence is None


def test_ws_confirm_false_es_provisional() -> None:
    raw = raw_candle_from_bybit_ws(_obj(False), "BTC-USDT", "spot", "1h")
    assert raw.is_closed is False


def test_ws_sin_una_clave_falla_fuerte() -> None:
    incompleto = _obj(True)
    del incompleto["timestamp"]
    with pytest.raises(BybitTranslationError):
        raw_candle_from_bybit_ws(incompleto, "BTC-USDT", "spot", "1m")


def test_ws_precios_como_texto() -> None:
    obj = _obj(True)
    obj["open"] = "0.000000010"
    raw = raw_candle_from_bybit_ws(obj, "PEPE-USDT", "spot", "5m")
    assert raw.open == "0.000000010"


def test_traduce_una_vela_rest_cerrada() -> None:
    row = [
        "1784202180000",
        "64255.2",
        "64300.0",
        "64200.0",
        "64263.7",
        "12.5",
        "803291",
    ]
    raw = raw_candle_from_bybit_rest(row, "BTC-USDT", "spot", "1m")
    assert raw.open_time_ms == 1784202180000
    assert raw.close_time_ms == 1784202180000 + Timeframe.M1.duration_ms - 1
    assert raw.close == "64263.7"
    assert raw.is_closed is True
    assert raw.event_time_ms == raw.close_time_ms


def test_rest_array_corto_falla_fuerte() -> None:
    with pytest.raises(BybitTranslationError):
        raw_candle_from_bybit_rest(["1", "2", "3"], "BTC-USDT", "spot", "1m")


def test_timeframe_no_soportado_falla_fuerte() -> None:
    with pytest.raises(BybitTranslationError):
        raw_candle_from_bybit_ws(_obj(True), "BTC-USDT", "spot", "30m")


def test_supported_bybit_timeframes_son_los_seis() -> None:
    assert supported_bybit_timeframes() == frozenset(Timeframe)
