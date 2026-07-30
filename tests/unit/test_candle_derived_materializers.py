"""Cableado LOTE 1a de P08b: candle-derived sobre read_ohlcv_window (MAT-06).

Sin BD: se prueba el BINDING (source_id -> funcion pura correcta), el tipo de spec, la
ventana, y que la salida es BIT-A-BIT la de la funcion pura. La composicion real
lector+materializador contra PostgreSQL la cubre el camino de integracion.
"""

from __future__ import annotations

from decimal import Decimal

from ce_v5.entrypoints.worker_rules.materializers import (
    SOURCE_MATERIALIZERS,
    CandlePointLocalSpec,
    CandleWindowedSpec,
)
from ce_v5.infra.db.market_candles import CandleOHLCV
from ce_v5.platform.rules.discovery import discover_declarations
from ce_v5.platform.rules.indicators.candle import (
    CANDLE_BODY_PCT_SOURCE_ID,
    CANDLE_LOWER_SHADOW_PCT_SOURCE_ID,
    CANDLE_UPPER_SHADOW_PCT_SOURCE_ID,
    body_pct,
    lower_shadow_pct,
    upper_shadow_pct,
)
from ce_v5.platform.rules.indicators.volume import (
    LOOKBACK_DEFAULT,
    VOLUME_RATIO_VS_AVG_SOURCE_ID,
    ratio_vs_avg,
)
from source.datasource import MemoryModel


def _candle(open_time: int, o: str, h: str, low: str, c: str, v: str) -> CandleOHLCV:
    return CandleOHLCV(
        open_time=open_time,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal(v),
    )


def _point_local(source_id: str) -> CandlePointLocalSpec:
    spec = SOURCE_MATERIALIZERS[source_id]
    assert isinstance(spec, CandlePointLocalSpec)
    return spec


def _windowed(source_id: str) -> CandleWindowedSpec:
    spec = SOURCE_MATERIALIZERS[source_id]
    assert isinstance(spec, CandleWindowedSpec)
    return spec


class TestCandleAnatomyPointLocal:
    def test_body_pct_extract_es_la_funcion_pura(self) -> None:
        candle = _candle(0, "20", "100", "0", "45", "10")
        esperado = body_pct(
            (candle.open,), (candle.high,), (candle.low,), (candle.close,)
        )[0]
        assert (
            _point_local(CANDLE_BODY_PCT_SOURCE_ID).extract(candle)
            == esperado
            == Decimal(25)
        )

    def test_upper_extract_es_la_funcion_pura(self) -> None:
        candle = _candle(0, "20", "100", "0", "45", "10")
        esperado = upper_shadow_pct(
            (candle.open,), (candle.high,), (candle.low,), (candle.close,)
        )[0]
        assert (
            _point_local(CANDLE_UPPER_SHADOW_PCT_SOURCE_ID).extract(candle)
            == esperado
            == Decimal(55)
        )

    def test_lower_extract_es_la_funcion_pura(self) -> None:
        candle = _candle(0, "20", "100", "0", "45", "10")
        esperado = lower_shadow_pct(
            (candle.open,), (candle.high,), (candle.low,), (candle.close,)
        )[0]
        assert (
            _point_local(CANDLE_LOWER_SHADOW_PCT_SOURCE_ID).extract(candle)
            == esperado
            == Decimal(20)
        )

    def test_los_tres_no_son_la_misma_medida(self) -> None:
        candle = _candle(0, "20", "100", "0", "45", "10")
        b = _point_local(CANDLE_BODY_PCT_SOURCE_ID).extract(candle)
        u = _point_local(CANDLE_UPPER_SHADOW_PCT_SOURCE_ID).extract(candle)
        low = _point_local(CANDLE_LOWER_SHADOW_PCT_SOURCE_ID).extract(candle)
        assert len({b, u, low}) == 3


class TestVolumeRatioWindowed:
    def test_la_ventana_es_lookback_mas_uno(self) -> None:
        assert (
            _windowed(VOLUME_RATIO_VS_AVG_SOURCE_ID).window_bars == LOOKBACK_DEFAULT + 1
        )

    def test_transform_es_la_funcion_pura(self) -> None:
        ventana = tuple(
            _candle(i, "10", "11", "9", "10", str(i + 1))
            for i in range(LOOKBACK_DEFAULT + 1)
        )
        esperado = ratio_vs_avg(
            tuple(c.volume for c in ventana), lookback=LOOKBACK_DEFAULT
        )[-1]
        assert _windowed(VOLUME_RATIO_VS_AVG_SOURCE_ID).transform(ventana) == esperado

    def test_transform_no_es_constante(self) -> None:
        base = [_candle(i, "10", "11", "9", "10", "5") for i in range(LOOKBACK_DEFAULT)]
        v1 = tuple([*base, _candle(99, "10", "11", "9", "10", "50")])
        v2 = tuple([*base, _candle(99, "10", "11", "9", "10", "1")])
        t = _windowed(VOLUME_RATIO_VS_AVG_SOURCE_ID).transform
        assert t(v1) != t(v2)


class TestDeclaracionesEnElCatalogo:
    def test_las_cuatro_declaradas(self) -> None:
        ids = {d.source_id for d in discover_declarations()}
        assert {
            CANDLE_BODY_PCT_SOURCE_ID,
            CANDLE_UPPER_SHADOW_PCT_SOURCE_ID,
            CANDLE_LOWER_SHADOW_PCT_SOURCE_ID,
            VOLUME_RATIO_VS_AVG_SOURCE_ID,
        } <= ids

    def test_memory_model_por_fuente(self) -> None:
        by_id = {d.source_id: d for d in discover_declarations()}
        assert by_id[CANDLE_BODY_PCT_SOURCE_ID].memory_model is MemoryModel.POINT_LOCAL
        assert (
            by_id[CANDLE_UPPER_SHADOW_PCT_SOURCE_ID].memory_model
            is MemoryModel.POINT_LOCAL
        )
        assert (
            by_id[CANDLE_LOWER_SHADOW_PCT_SOURCE_ID].memory_model
            is MemoryModel.POINT_LOCAL
        )
        assert by_id[VOLUME_RATIO_VS_AVG_SOURCE_ID].memory_model is MemoryModel.WINDOWED

    def test_volume_ratio_declara_lookback_en_cache_key(self) -> None:
        by_id = {d.source_id: d for d in discover_declarations()}
        vr = by_id[VOLUME_RATIO_VS_AVG_SOURCE_ID]
        assert "lookback" in vr.cache_key_schema
        assert {p.name for p in vr.params} == {"lookback"}
