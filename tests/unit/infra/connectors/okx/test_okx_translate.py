"""Pruebas de la traduccion de velas de OKX a RawCandle (T-03). Hermeticas."""

from __future__ import annotations

import pytest

from ce_v5.infra.connectors.okx.translate import (
    OkxTranslationError,
    raw_candle_from_okx,
    supported_okx_timeframes,
)
from source.families.market import Timeframe


def _row(confirm: str) -> list[str]:
    # [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
    return [
        "1597026420000",
        "3.721",
        "3.743",
        "3.677",
        "3.708",
        "8422410",
        "22698348.04",
        "12698348.04",
        confirm,
    ]


def test_traduce_una_vela_cerrada() -> None:
    raw = raw_candle_from_okx(_row("1"), "LTC-USDT", "spot", "1m")
    assert raw.exchange == "okx"
    assert raw.market_type == "spot"
    assert raw.symbol == "LTC-USDT"
    assert raw.timeframe == "1m"
    assert raw.open_time_ms == 1597026420000
    assert raw.close_time_ms == 1597026420000 + Timeframe.M1.duration_ms - 1
    assert raw.open == "3.721"
    assert raw.high == "3.743"
    assert raw.low == "3.677"
    assert raw.close == "3.708"
    assert raw.volume == "8422410"
    assert raw.is_closed is True
    assert raw.event_time_ms == 1597026420000
    assert raw.source_sequence is None


def test_confirm_cero_es_provisional() -> None:
    raw = raw_candle_from_okx(_row("0"), "BTC-USDT", "spot", "1h")
    assert raw.is_closed is False
    assert raw.close_time_ms == raw.open_time_ms + Timeframe.H1.duration_ms - 1


def test_precios_se_conservan_como_texto() -> None:
    row = _row("1")
    row[1] = "0.000000010"
    raw = raw_candle_from_okx(row, "PEPE-USDT", "spot", "5m")
    assert raw.open == "0.000000010"


def test_array_corto_falla_fuerte() -> None:
    with pytest.raises(OkxTranslationError):
        raw_candle_from_okx(["1597026420000", "3.7"], "BTC-USDT", "spot", "1m")


def test_no_es_array_falla_fuerte() -> None:
    with pytest.raises(OkxTranslationError):
        raw_candle_from_okx({"ts": "1"}, "BTC-USDT", "spot", "1m")  # type: ignore[arg-type]


def test_timeframe_no_soportado_falla_fuerte() -> None:
    with pytest.raises(OkxTranslationError):
        raw_candle_from_okx(_row("1"), "BTC-USDT", "spot", "30m")


def test_supported_okx_timeframes_son_los_seis_canonicos() -> None:
    assert supported_okx_timeframes() == frozenset(Timeframe)
