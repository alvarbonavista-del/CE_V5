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
    CANDLE_OPEN_SOURCE_ID,
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
from ce_v5.platform.rules.indicators.vwap import (
    N_CANDLES_DEFAULT,
    VWAP_DISTANCE_PCT_SOURCE_ID,
    VWAP_VALUE_SOURCE_ID,
)
from ce_v5.platform.rules.indicators.vwap import (
    distance_pct as vwap_distance_pct,
)
from ce_v5.platform.rules.indicators.vwap import (
    value as vwap_value,
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


class TestCandleOpenPointLocal:
    """candle.open: IDENTIDAD sobre el campo open de la vela, sin funcion pura propia.

    A diferencia de body_pct/upper/lower (que derivan un porcentaje del OHLC), aqui el
    extract devuelve el open TAL CUAL: es la fuente base cruda, no un computo.
    """

    def test_extract_es_el_open_de_la_vela(self) -> None:
        candle = _candle(0, "20", "100", "0", "45", "10")
        assert _point_local(CANDLE_OPEN_SOURCE_ID).extract(candle) == Decimal("20")

    def test_extract_no_es_una_constante(self) -> None:
        # Dos velas con open DISTINTO dan valores distintos: si extract devolviera otro
        # campo (o un cero fijo), esto lo caza.
        una = _candle(0, "20", "100", "0", "45", "10")
        otra = _candle(1, "77", "100", "0", "45", "10")
        assert _point_local(CANDLE_OPEN_SOURCE_ID).extract(una) != _point_local(
            CANDLE_OPEN_SOURCE_ID
        ).extract(otra)


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
    def test_las_cinco_declaradas(self) -> None:
        ids = {d.source_id for d in discover_declarations()}
        assert {
            CANDLE_BODY_PCT_SOURCE_ID,
            CANDLE_UPPER_SHADOW_PCT_SOURCE_ID,
            CANDLE_LOWER_SHADOW_PCT_SOURCE_ID,
            CANDLE_OPEN_SOURCE_ID,
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
        assert by_id[CANDLE_OPEN_SOURCE_ID].memory_model is MemoryModel.POINT_LOCAL
        assert by_id[VOLUME_RATIO_VS_AVG_SOURCE_ID].memory_model is MemoryModel.WINDOWED

    def test_candle_open_continuous_sin_params_y_misma_cache_key_que_body_pct(
        self,
    ) -> None:
        by_id = {d.source_id: d for d in discover_declarations()}
        abierto = by_id[CANDLE_OPEN_SOURCE_ID]
        body = by_id[CANDLE_BODY_PCT_SOURCE_ID]
        assert abierto.servibility is body.servibility
        assert abierto.params == ()
        assert abierto.overridable_params == ()
        assert abierto.cache_key_schema == body.cache_key_schema
        assert abierto.consumes == body.consumes == ()

    def test_volume_ratio_declara_lookback_en_cache_key(self) -> None:
        by_id = {d.source_id: d for d in discover_declarations()}
        vr = by_id[VOLUME_RATIO_VS_AVG_SOURCE_ID]
        assert "lookback" in vr.cache_key_schema
        assert {p.name for p in vr.params} == {"lookback"}


def _hlcv(window: tuple[CandleOHLCV, ...]) -> tuple[tuple[Decimal, ...], ...]:
    return (
        tuple(c.high for c in window),
        tuple(c.low for c in window),
        tuple(c.close for c in window),
        tuple(c.volume for c in window),
    )


def _vwap_window_normal() -> tuple[CandleOHLCV, ...]:
    # ventana de N velas con volumen positivo y precios distinguibles por barra
    out = []
    x = 777
    for i in range(N_CANDLES_DEFAULT):
        x = (1103515245 * x + 12345) % 2147483648
        base = Decimal(x % 100000) / Decimal(100)
        close = base + (Decimal((x // 7) % 2000) - Decimal(1000)) / Decimal(100)
        high = max(base, close) + Decimal((x // 11) % 500) / Decimal(100)
        low = min(base, close) - Decimal((x // 13) % 500) / Decimal(100)
        vol = Decimal(1) + Decimal((x // 17) % 100000) / Decimal(100)
        out.append(
            CandleOHLCV(
                open_time=i, open=base, high=high, low=low, close=close, volume=vol
            )
        )
    return tuple(out)


def _vwap_window_vol_cero() -> tuple[CandleOHLCV, ...]:
    # volumen total 0 ; H=20,L=4,C=6 -> HLC3=(20+4+6)/3=10 ; media=10 ; close=6
    # (!=10, !=0)
    return tuple(
        CandleOHLCV(
            open_time=i,
            open=Decimal("10"),
            high=Decimal("20"),
            low=Decimal("4"),
            close=Decimal("6"),
            volume=Decimal("0"),
        )
        for i in range(N_CANDLES_DEFAULT)
    )


class TestVwapWindowed:
    def test_la_ventana_es_n_candles(self) -> None:
        assert _windowed(VWAP_VALUE_SOURCE_ID).window_bars == N_CANDLES_DEFAULT
        assert _windowed(VWAP_DISTANCE_PCT_SOURCE_ID).window_bars == N_CANDLES_DEFAULT

    def test_value_normal_es_la_funcion_pura(self) -> None:
        w = _vwap_window_normal()
        h, low, c, v = _hlcv(w)
        esperado = vwap_value(h, low, c, v, n_candles=N_CANDLES_DEFAULT)[-1]
        assert esperado is not None
        assert _windowed(VWAP_VALUE_SOURCE_ID).transform(w) == esperado

    def test_distance_normal_es_la_funcion_pura(self) -> None:
        w = _vwap_window_normal()
        h, low, c, v = _hlcv(w)
        esperado = vwap_distance_pct(h, low, c, v, n_candles=N_CANDLES_DEFAULT)[-1]
        assert esperado is not None
        assert _windowed(VWAP_DISTANCE_PCT_SOURCE_ID).transform(w) == esperado

    def test_value_fallback_hlc3_en_volumen_cero(self) -> None:
        w = _vwap_window_vol_cero()
        h, low, c, v = _hlcv(w)
        # (a) la funcion pura devuelve None (VWAP indefinido con volumen 0)
        assert vwap_value(h, low, c, v, n_candles=N_CANDLES_DEFAULT)[-1] is None
        # (b)+(c) el materializador devuelve el fallback HLC3 = 10 (!= close 6,
        # != 0): muerde
        assert _windowed(VWAP_VALUE_SOURCE_ID).transform(w) == Decimal(10)

    def test_distance_fallback_en_volumen_cero(self) -> None:
        w = _vwap_window_vol_cero()
        h, low, c, v = _hlcv(w)
        # (a) la funcion pura devuelve None
        assert vwap_distance_pct(h, low, c, v, n_candles=N_CANDLES_DEFAULT)[-1] is None
        # (b)+(c) distancia contra el fallback: |6-10|/10*100 = 40 (muerde)
        assert _windowed(VWAP_DISTANCE_PCT_SOURCE_ID).transform(w) == Decimal(40)

    def test_declaradas_windowed_con_n_candles(self) -> None:
        by_id = {d.source_id: d for d in discover_declarations()}
        for sid in (VWAP_VALUE_SOURCE_ID, VWAP_DISTANCE_PCT_SOURCE_ID):
            assert by_id[sid].memory_model is MemoryModel.WINDOWED
            assert "n_candles" in by_id[sid].cache_key_schema
