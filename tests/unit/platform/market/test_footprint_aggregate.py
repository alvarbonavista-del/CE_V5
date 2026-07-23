"""Tests de la agregacion PURA del footprint (P07b 3b-1; ADR-007, I-04).

Sin IO, sin reloj, sin base: solo la funcion que convierte los trades de una ventana en
la foto por barra. Se demuestran las propiedades que Central fijo (LOCKED):

- CELDA = PRECIO EXACTO: una celda por nivel de precio nativo, sin agrupar ni capar.
- DELTA COHERENTE por celda y de barra (buy - sell), totales cuadrados con las celdas.
- CELDAS ORDENADAS por precio ascendente, sin repetir nivel.
- REPRODUCIBILIDAD BIT A BIT: los mismos trades en cualquier orden -> el MISMO payload,
  byte a byte igual. Ni el orden de llegada ni el reparto del mismo ms cuentan.
- DEDUP por trade_id: el mismo trade dos veces suma UNA.
- is_complete FAIL-SAFE: False si algun hueco solapa la ventana; True si ninguno toca.
- La CORRECCION referencia por corrects_idempotency_key al closed de la MISMA barra.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from ce_v5.platform.market.footprint_aggregate import (
    FootprintStreamIdentity,
    TradeGap,
    aggregate_footprint,
)
from source.families.footprint import (
    FootprintClosedPayload,
    FootprintCorrectedPayload,
    MarketFootprintEventType,
    MarketTrade,
    footprint_idempotency_key,
)
from source.families.market import AggressorSide, MarketType, Timeframe
from source.time import MaturityState

_TF = Timeframe.M1
_OPEN = 1_784_073_600_000  # alineado a M1 (divisible por 60_000).
_CLOSE = _OPEN + _TF.duration_ms

_IDENTITY = FootprintStreamIdentity(
    exchange="binance",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    timeframe=_TF,
)


def _trade(**overrides: object) -> MarketTrade:
    base: dict[str, object] = {
        "exchange": "binance",
        "market_type": MarketType.SPOT,
        "symbol": "BTC-USDT",
        "trade_id": "1",
        "price": Decimal("100"),
        "qty": Decimal("1"),
        "aggressor_side": AggressorSide.BUY,
        "event_time": _OPEN + 10,
    }
    base.update(overrides)
    return MarketTrade(**base)


def _closed(
    trades: list[MarketTrade], gaps: list[TradeGap] | None = None
) -> FootprintClosedPayload:
    payload = aggregate_footprint(
        _IDENTITY,
        _OPEN,
        _CLOSE,
        trades,
        gaps or [],
        maturity_state=MaturityState.CLOSED,
    )
    assert isinstance(payload, FootprintClosedPayload)
    return payload


class TestCeldasPorPrecioExacto:
    def test_una_celda_por_nivel_de_precio_nativo(self) -> None:
        # Tres precios distintos -> tres celdas, sin agrupar por tick ni redondear.
        payload = _closed(
            [
                _trade(trade_id="1", price=Decimal("100.01")),
                _trade(trade_id="2", price=Decimal("100.02")),
                _trade(trade_id="3", price=Decimal("100.03")),
            ]
        )
        assert [c.price for c in payload.cells] == [
            Decimal("100.01"),
            Decimal("100.02"),
            Decimal("100.03"),
        ]

    def test_dos_precios_muy_cercanos_no_se_funden(self) -> None:
        # LOSSLESS: 100.00 y 100.000001 son DOS niveles, jamas uno. Sin cap, sin step.
        payload = _closed(
            [
                _trade(trade_id="1", price=Decimal("100.00")),
                _trade(trade_id="2", price=Decimal("100.000001")),
            ]
        )
        assert len(payload.cells) == 2

    def test_el_mismo_precio_acumula_en_una_sola_celda(self) -> None:
        payload = _closed(
            [
                _trade(trade_id="1", price=Decimal("100"), qty=Decimal("1.5")),
                _trade(trade_id="2", price=Decimal("100"), qty=Decimal("2.5")),
            ]
        )
        assert len(payload.cells) == 1
        assert payload.cells[0].buy_volume == Decimal("4.0")


class TestDeltaYTotales:
    def test_delta_de_celda_es_buy_menos_sell(self) -> None:
        payload = _closed(
            [
                _trade(
                    trade_id="1", aggressor_side=AggressorSide.BUY, qty=Decimal("3")
                ),
                _trade(
                    trade_id="2", aggressor_side=AggressorSide.SELL, qty=Decimal("2")
                ),
            ]
        )
        cell = payload.cells[0]
        assert cell.buy_volume == Decimal("3")
        assert cell.sell_volume == Decimal("2")
        assert cell.delta == Decimal("1")

    def test_totales_de_barra_cuadran_con_las_celdas(self) -> None:
        payload = _closed(
            [
                _trade(trade_id="1", price=Decimal("100"), qty=Decimal("1")),
                _trade(
                    trade_id="2",
                    price=Decimal("101"),
                    qty=Decimal("4"),
                    aggressor_side=AggressorSide.SELL,
                ),
            ]
        )
        assert payload.bar_buy_volume == Decimal("1")
        assert payload.bar_sell_volume == Decimal("4")
        assert payload.bar_delta == Decimal("-3")
        assert payload.trade_count == 2


class TestOrden:
    def test_las_celdas_salen_ordenadas_por_precio_ascendente(self) -> None:
        # Llegan desordenadas por precio; salen ascendentes. El contrato lo exigiria,
        # pero es la funcion la que lo garantiza (sorted por precio).
        payload = _closed(
            [
                _trade(trade_id="1", price=Decimal("103")),
                _trade(trade_id="2", price=Decimal("101")),
                _trade(trade_id="3", price=Decimal("102")),
            ]
        )
        prices = [c.price for c in payload.cells]
        assert prices == sorted(prices)


class TestDedup:
    def test_el_mismo_trade_id_dos_veces_suma_una(self) -> None:
        # Idempotente ante reentrega: dos copias del MISMO trade no inflan el volumen.
        payload = _closed(
            [
                _trade(trade_id="7", qty=Decimal("5")),
                _trade(trade_id="7", qty=Decimal("5")),
            ]
        )
        assert payload.trade_count == 1
        assert payload.cells[0].buy_volume == Decimal("5")

    def test_dos_trades_del_mismo_ms_y_precio_son_hechos_distintos(self) -> None:
        # Identidad = trade_id: dos personas compraron lo mismo en el mismo ms. Suman.
        payload = _closed(
            [
                _trade(trade_id="A", qty=Decimal("1"), event_time=_OPEN + 5),
                _trade(trade_id="B", qty=Decimal("1"), event_time=_OPEN + 5),
            ]
        )
        assert payload.trade_count == 2
        assert payload.cells[0].buy_volume == Decimal("2")


class TestReproducibilidadBitABit:
    """EL INVARIANTE DE CENTRAL (I-04 1.1/4.4): el orden es IRRELEVANTE, byte a byte."""

    def test_el_mismo_lote_en_distinto_orden_da_el_mismo_payload(self) -> None:
        lote = [
            _trade(trade_id="1", price=Decimal("100"), qty=Decimal("1")),
            _trade(
                trade_id="2",
                price=Decimal("101"),
                qty=Decimal("2"),
                aggressor_side=AggressorSide.SELL,
            ),
            _trade(trade_id="3", price=Decimal("100"), qty=Decimal("3")),
            _trade(
                trade_id="4",
                price=Decimal("102"),
                qty=Decimal("4"),
                aggressor_side=AggressorSide.SELL,
            ),
        ]
        directo = _closed(lote)
        inverso = _closed(list(reversed(lote)))
        barajado = _closed([lote[2], lote[0], lote[3], lote[1]])

        # Byte a byte: no basta con "igual conjunto", es el MISMO JSON.
        assert (
            directo.model_dump_json()
            == inverso.model_dump_json()
            == barajado.model_dump_json()
        )

    def test_el_orden_de_trades_del_mismo_ms_no_altera_el_resultado(self) -> None:
        # Mismo ms, mismo precio, lados opuestos: sea cual sea el orden, celda identica.
        a = _trade(trade_id="A", qty=Decimal("3"), aggressor_side=AggressorSide.BUY)
        b = _trade(trade_id="B", qty=Decimal("2"), aggressor_side=AggressorSide.SELL)
        assert _closed([a, b]).model_dump_json() == _closed([b, a]).model_dump_json()


class TestIsComplete:
    def test_sin_huecos_la_barra_es_completa(self) -> None:
        assert _closed([_trade()], gaps=[]).is_complete is True

    def test_un_hueco_que_solapa_la_ventana_la_marca_incompleta(self) -> None:
        # El hueco cae DENTRO de [open, open+tf): faltan trades -> fail-safe False.
        gap: TradeGap = (_OPEN + 10, _OPEN + 20)
        assert _closed([_trade()], gaps=[gap]).is_complete is False

    def test_un_hueco_fuera_de_la_ventana_no_afecta(self) -> None:
        # El hueco termina ANTES de que empiece la barra: no le falta nada a esta barra.
        gap: TradeGap = (_OPEN - 100, _OPEN - 10)
        assert _closed([_trade()], gaps=[gap]).is_complete is True

    def test_un_hueco_de_extremo_desconocido_marca_incompleta(self) -> None:
        # FAIL-SAFE: un extremo None (incierto) se trata como infinito -> por si acaso,
        # solapa y la barra es incompleta.
        gap: TradeGap = (None, _OPEN + 5)
        assert _closed([_trade()], gaps=[gap]).is_complete is False

    def test_una_barra_sin_trades_puede_ser_completa(self) -> None:
        # Cero trades y cero huecos: hubo silencio de mercado, no perdida de dato.
        payload = _closed([], gaps=[])
        assert payload.cells == ()
        assert payload.trade_count == 0
        assert payload.is_complete is True


class TestCorreccion:
    def test_la_correccion_referencia_al_closed_de_la_misma_barra(self) -> None:
        payload = aggregate_footprint(
            _IDENTITY,
            _OPEN,
            _CLOSE,
            [_trade()],
            [],
            maturity_state=MaturityState.CORRECTION,
            correction_revision=2,
        )
        assert isinstance(payload, FootprintCorrectedPayload)
        assert payload.correction_revision == 2
        esperado = footprint_idempotency_key(
            event_type=MarketFootprintEventType.FOOTPRINT_CLOSED,
            stream_key=_IDENTITY.footprint_stream_key(),
            open_time=_OPEN,
            maturity_state=MaturityState.CLOSED,
            correction_revision=None,
        )
        assert payload.corrects_idempotency_key == esperado

    def test_una_correccion_sin_revision_es_un_error(self) -> None:
        with pytest.raises(ValueError, match="correction_revision"):
            aggregate_footprint(
                _IDENTITY,
                _OPEN,
                _CLOSE,
                [_trade()],
                [],
                maturity_state=MaturityState.CORRECTION,
                correction_revision=None,
            )

    def test_un_estado_de_madurez_no_soportado_es_un_error(self) -> None:
        with pytest.raises(ValueError, match="closed o correction"):
            aggregate_footprint(
                _IDENTITY,
                _OPEN,
                _CLOSE,
                [_trade()],
                [],
                maturity_state=MaturityState.PROVISIONAL,
            )
