"""Cableado de los detectores footprint+vela como DataSources (P08c-DET-01 paso b).

Sin BD: se prueba la BARRA COMPUESTA (DetectorBar), el BINDING (source_id -> transform
correcta, tipo de spec, ventana) y que la transform produce el CARRIER esperado sobre
ventanas sinteticas. La composicion real contra PostgreSQL -- incluido el fail-loud de
alineacion footprint/vela, que necesita dos tablas de verdad -- vive en
tests/integration/test_detector_materializers.py (basename DISTINTO a proposito: tests/
no es un paquete y dos modulos de test homonimos colisionan en la recoleccion).
"""

from __future__ import annotations

from decimal import Decimal

from ce_v5.entrypoints.worker_rules.materializers import (
    DETECTOR_WINDOW_BARS,
    SOURCE_MATERIALIZERS,
    DetectorBar,
    DetectorWindowedSpec,
    _absorption_signal,
    _climax_signal,
    _void_signal,
)
from ce_v5.infra.db.market_candles import CandleOHLCV
from ce_v5.platform.rules.absorption import (
    ABSORPTION_ASK_STRENGTH_SOURCE_ID,
    ABSORPTION_BID_STRENGTH_SOURCE_ID,
    AbsorptionSide,
)
from ce_v5.platform.rules.climax import (
    CLIMAX_BOTTOM_STRENGTH_SOURCE_ID,
    CLIMAX_TOP_STRENGTH_SOURCE_ID,
    ClimaxSide,
)
from ce_v5.platform.rules.void import (
    VOID_SNAP_BEARISH_SOURCE_ID,
    VOID_SNAP_BULLISH_SOURCE_ID,
)
from source.families.footprint import FootprintCell, FootprintClosedPayload
from source.families.market import MarketType, Timeframe
from source.rules.scalar import ScalarType
from source.time import MaturityState

_TF = Timeframe.M1
_OPEN = 1_784_073_600_000


def _footprint(
    open_time: int, *, buy: str, sell: str, low_price: str, high_price: str
) -> FootprintClosedPayload:
    """Footprint que fija el span (high-low) y el delta (buy-sell).

    DOS celdas cuando hay span; UNA sola cuando low == high, porque el contrato exige
    precios ascendentes SIN repetir nivel: un footprint de span 0 es, por definicion, un
    unico nivel.
    """
    cells = (
        (
            FootprintCell(
                price=Decimal(low_price),
                buy_volume=Decimal(buy),
                sell_volume=Decimal(sell),
                delta=Decimal(buy) - Decimal(sell),
            ),
        )
        if low_price == high_price
        else (
            FootprintCell(
                price=Decimal(low_price),
                buy_volume=Decimal(buy),
                sell_volume=Decimal(0),
                delta=Decimal(buy),
            ),
            FootprintCell(
                price=Decimal(high_price),
                buy_volume=Decimal(0),
                sell_volume=Decimal(sell),
                delta=-Decimal(sell),
            ),
        )
    )
    return FootprintClosedPayload(
        maturity_state=MaturityState.CLOSED,
        exchange="binance",
        market_type=MarketType.SPOT,
        symbol="BTC-USDT",
        timeframe=_TF,
        open_time=open_time,
        close_time=open_time + _TF.duration_ms,
        cells=cells,
        bar_buy_volume=Decimal(buy),
        bar_sell_volume=Decimal(sell),
        bar_delta=Decimal(buy) - Decimal(sell),
        trade_count=2,
        is_complete=True,
    )


def _bar(
    indice: int,
    *,
    buy: str = "5",
    sell: str = "5",
    low_price: str = "100",
    high_price: str = "110",
    open_price: str = "105",
    close_price: str = "105",
) -> DetectorBar:
    open_time = _OPEN + indice * _TF.duration_ms
    return DetectorBar(
        footprint=_footprint(
            open_time, buy=buy, sell=sell, low_price=low_price, high_price=high_price
        ),
        candle=CandleOHLCV(
            open_time=open_time,
            open=Decimal(open_price),
            high=Decimal(high_price),
            low=Decimal(low_price),
            close=Decimal(close_price),
            volume=Decimal(buy) + Decimal(sell),
        ),
    )


class TestDetectorBar:
    def test_el_volumen_agresor_suma_los_dos_lados_del_footprint(self) -> None:
        bar = _bar(0, buy="7", sell="3")
        assert bar.aggressor_volume == Decimal(10)

    def test_el_volumen_agresor_no_es_el_de_la_vela(self) -> None:
        # Muerde: si el cableado leyera candle.volume en vez del footprint, los
        # detectores razonarian sobre volumen publicado y no sobre AGRESION.
        bar = DetectorBar(
            footprint=_footprint(
                _OPEN, buy="7", sell="3", low_price="100", high_price="110"
            ),
            candle=CandleOHLCV(
                open_time=_OPEN,
                open=Decimal(105),
                high=Decimal(110),
                low=Decimal(100),
                close=Decimal(105),
                volume=Decimal(999),
            ),
        )
        assert bar.aggressor_volume == Decimal(10) != bar.candle.volume


class TestAbsorptionTransform:
    """La transform de absorption sobre una ventana: umbral adaptativo + veredicto."""

    def _ventana_con_absorcion_bid(self) -> list[DetectorBar]:
        # Barras previas de ratio BAJO (volumen 10 / span 10 = 1) para que el umbral
        # adaptativo caiga en su PISO (2.0), y una ultima barra con ratio ALTO (1000/10
        # = 100), agresion vendedora fuerte y precio CONTENIDO que SUBE: delta<0 y
        # displacement>0 son direcciones opuestas -> absorcion de VENDEDORES (BID).
        previas = [_bar(i, buy="5", sell="5") for i in range(30)]
        ultima = _bar(30, buy="100", sell="900", open_price="104", close_price="105")
        return [*previas, ultima]

    def test_detecta_absorcion_bid_sobre_la_ultima_barra(self) -> None:
        senal = _absorption_signal(self._ventana_con_absorcion_bid())
        assert senal.detected is True
        assert senal.side is AbsorptionSide.BID

    def test_bid_publica_la_fuerza_y_ask_publica_cero(self) -> None:
        ventana = self._ventana_con_absorcion_bid()
        bid = SOURCE_MATERIALIZERS[ABSORPTION_BID_STRENGTH_SOURCE_ID]
        ask = SOURCE_MATERIALIZERS[ABSORPTION_ASK_STRENGTH_SOURCE_ID]
        assert isinstance(bid, DetectorWindowedSpec)
        assert isinstance(ask, DetectorWindowedSpec)

        valor_bid = bid.transform(ventana)
        valor_ask = ask.transform(ventana)

        assert valor_bid.scalar_type is ScalarType.DECIMAL
        assert valor_bid.decimal_value is not None
        assert valor_bid.decimal_value > 0
        assert valor_ask.decimal_value == Decimal(0)

    def test_una_ventana_plana_no_dispara_ningun_lado(self) -> None:
        # Sin ratio excepcional no hay candidatura: las dos salidas son 0. Es el caso
        # ABRUMADORAMENTE mayoritario y tiene que servirse como hecho, no como hueco.
        ventana = [_bar(i) for i in range(31)]
        for source_id in (
            ABSORPTION_BID_STRENGTH_SOURCE_ID,
            ABSORPTION_ASK_STRENGTH_SOURCE_ID,
        ):
            spec = SOURCE_MATERIALIZERS[source_id]
            assert isinstance(spec, DetectorWindowedSpec)
            assert spec.transform(ventana).decimal_value == Decimal(0)

    def test_el_umbral_sale_de_las_barras_previas_no_de_la_evaluada(self) -> None:
        # Si la barra evaluada entrase en su propia distribucion, su ratio gigante
        # subiria el percentil y se auto-anularia. Con las previas planas el umbral
        # queda en el piso y la deteccion ocurre; ese contraste es la prueba.
        con_previas_planas = _absorption_signal(self._ventana_con_absorcion_bid())
        assert con_previas_planas.detected is True

    def test_una_barra_de_span_cero_no_contamina_el_umbral(self) -> None:
        # Span 0 -> ratio indefinido: esa barra NO entra en la distribucion. Si entrase
        # como 0, hundiria el percentil y falsearia el umbral.
        ventana = [_bar(i, low_price="100", high_price="100") for i in range(30)]
        ventana.append(
            _bar(30, buy="100", sell="900", open_price="104", close_price="105")
        )
        senal = _absorption_signal(ventana)
        assert senal.detected is True


class TestBindingEnElRegistro:
    def test_las_dos_estan_cableadas_con_spec_de_detector(self) -> None:
        for source_id in (
            ABSORPTION_BID_STRENGTH_SOURCE_ID,
            ABSORPTION_ASK_STRENGTH_SOURCE_ID,
        ):
            assert isinstance(SOURCE_MATERIALIZERS[source_id], DetectorWindowedSpec)

    def test_la_ventana_es_la_de_normalizacion_de_los_detectores(self) -> None:
        spec = SOURCE_MATERIALIZERS[ABSORPTION_BID_STRENGTH_SOURCE_ID]
        assert isinstance(spec, DetectorWindowedSpec)
        assert spec.window_bars == DETECTOR_WINDOW_BARS == 100

    def test_cada_source_id_tiene_su_propia_transform(self) -> None:
        # Si las dos compartieran transform, el catalogo ofreceria dos fuentes y el
        # motor serviria la misma.
        bid = SOURCE_MATERIALIZERS[ABSORPTION_BID_STRENGTH_SOURCE_ID]
        ask = SOURCE_MATERIALIZERS[ABSORPTION_ASK_STRENGTH_SOURCE_ID]
        assert isinstance(bid, DetectorWindowedSpec)
        assert isinstance(ask, DetectorWindowedSpec)
        assert bid.transform is not ask.transform


class TestClimaxTransform:
    """La transform de climax: volumen AGRESOR del footprint + high/low/close de
    vela."""

    def _ventana_con_climax_top(self) -> list[DetectorBar]:
        # 30 barras normales (volumen 10, rango 100-110) y una ultima EXCEPCIONAL en
        # volumen y rango cuyo cierre queda en el tercio INFERIOR -> climax de TECHO.
        previas = [_bar(i, buy="5", sell="5") for i in range(30)]
        ultima = _bar(
            30,
            buy="5000",
            sell="5000",
            low_price="50",
            high_price="200",
            close_price="60",
        )
        return [*previas, ultima]

    def test_detecta_climax_top_sobre_la_ultima_barra(self) -> None:
        senal = _climax_signal(self._ventana_con_climax_top())
        assert senal.detected is True
        assert senal.side is ClimaxSide.TOP

    def test_top_publica_la_fuerza_y_bottom_publica_cero(self) -> None:
        ventana = self._ventana_con_climax_top()
        top = SOURCE_MATERIALIZERS[CLIMAX_TOP_STRENGTH_SOURCE_ID]
        bottom = SOURCE_MATERIALIZERS[CLIMAX_BOTTOM_STRENGTH_SOURCE_ID]
        assert isinstance(top, DetectorWindowedSpec)
        assert isinstance(bottom, DetectorWindowedSpec)

        valor_top = top.transform(ventana)
        assert valor_top.scalar_type is ScalarType.DECIMAL
        assert valor_top.decimal_value is not None
        assert valor_top.decimal_value > 0
        assert bottom.transform(ventana).decimal_value == Decimal(0)

    def test_una_ventana_plana_no_dispara_ningun_lado(self) -> None:
        ventana = [_bar(i) for i in range(31)]
        for source_id in (
            CLIMAX_TOP_STRENGTH_SOURCE_ID,
            CLIMAX_BOTTOM_STRENGTH_SOURCE_ID,
        ):
            spec = SOURCE_MATERIALIZERS[source_id]
            assert isinstance(spec, DetectorWindowedSpec)
            assert spec.transform(ventana).decimal_value == Decimal(0)

    def test_por_debajo_de_min_candles_no_hay_veredicto(self) -> None:
        # MIN_CANDLES=20: con menos, el nucleo devuelve "sin senal" (0 legitimo), no un
        # hueco. El warm-up de la SERIE lo resuelve aparte materialize_windowed.
        ventana = [_bar(i) for i in range(5)]
        assert _climax_signal(ventana).detected is False

    def test_usa_el_volumen_agresor_del_footprint_no_el_de_la_vela(self) -> None:
        # Las velas llevan volume = buy+sell del footprint en el helper, asi que para
        # aislar esto se construye una ventana donde candle.volume es constante y solo
        # cambia el footprint: si el detector leyera candle.volume, la ultima barra no
        # destacaria en volumen y no habria climax.
        ventana = self._ventana_con_climax_top()
        plana = [
            DetectorBar(
                footprint=bar.footprint,
                candle=CandleOHLCV(
                    open_time=bar.candle.open_time,
                    open=bar.candle.open,
                    high=bar.candle.high,
                    low=bar.candle.low,
                    close=bar.candle.close,
                    volume=Decimal(1),
                ),
            )
            for bar in ventana
        ]
        assert _climax_signal(plana).detected is True


class TestVoidTransform:
    """La transform de void: el LVN sale del footprint de la propia ventana."""

    def _ventana_con_snap(self) -> list[DetectorBar]:
        # El perfil de la ventana concentra volumen en 100 y 110 (los extremos de las
        # celdas), asi que el LVN cae en algun bin intermedio poco visitado. El precio
        # cruza ese nivel al alza y VUELVE en la siguiente barra -> snap BEARISH.
        #
        # Para que el cruce sea inequivoco se mueven los CIERRES por encima y por debajo
        # del centro del rango, dejando el footprint constante.
        ventana = [_bar(i, close_price="101") for i in range(28)]
        ventana.append(_bar(28, close_price="109"))  # cruza hacia arriba
        ventana.append(_bar(29, close_price="101"))  # y vuelve
        return ventana

    def test_las_dos_salidas_son_indicadoras_cero_o_uno(self) -> None:
        ventana = self._ventana_con_snap()
        for source_id in (VOID_SNAP_BULLISH_SOURCE_ID, VOID_SNAP_BEARISH_SOURCE_ID):
            spec = SOURCE_MATERIALIZERS[source_id]
            assert isinstance(spec, DetectorWindowedSpec)
            valor = spec.transform(ventana)
            assert valor.scalar_type is ScalarType.DECIMAL
            assert valor.decimal_value in {Decimal(0), Decimal(1)}

    def test_una_ventana_sin_cruces_no_dispara_ninguna(self) -> None:
        # Cierres constantes: nadie cruza el LVN, asi que no hay snap por ningun lado.
        ventana = [_bar(i, close_price="105") for i in range(30)]
        for source_id in (VOID_SNAP_BULLISH_SOURCE_ID, VOID_SNAP_BEARISH_SOURCE_ID):
            spec = SOURCE_MATERIALIZERS[source_id]
            assert isinstance(spec, DetectorWindowedSpec)
            assert spec.transform(ventana).decimal_value == Decimal(0)

    def test_las_dos_direcciones_nunca_son_uno_a_la_vez(self) -> None:
        # Un snap tiene UNA direccion: la contraria al cruce que lo origino.
        ventana = self._ventana_con_snap()
        bullish = SOURCE_MATERIALIZERS[VOID_SNAP_BULLISH_SOURCE_ID]
        bearish = SOURCE_MATERIALIZERS[VOID_SNAP_BEARISH_SOURCE_ID]
        assert isinstance(bullish, DetectorWindowedSpec)
        assert isinstance(bearish, DetectorWindowedSpec)
        valores = {
            bullish.transform(ventana).decimal_value,
            bearish.transform(ventana).decimal_value,
        }
        assert valores != {Decimal(1)}

    def test_el_lvn_sale_del_footprint_de_la_ventana_no_de_un_parametro(self) -> None:
        # El veredicto se computa sin que nadie pase un LVN: si el materializador no lo
        # derivase del footprint, esto reventaria por falta de nivel.
        senal = _void_signal(self._ventana_con_snap())
        assert senal.detected in {True, False}
