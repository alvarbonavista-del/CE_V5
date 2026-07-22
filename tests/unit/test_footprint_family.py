"""Tests de trades individuales y footprint (P07b; ADR-014, ADR-007, ADR-006).

Demuestran que el contrato DEFIENDE el borde -- el dato del exchange es entrada NO
confiable -- y que el footprint tiene una FORMA determinista (celdas ordenadas,
totales cuadrados, delta coherente) que es la base de la reproducibilidad bit a bit.
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from source.families.footprint import (
    FootprintCell,
    FootprintClosedPayload,
    FootprintCorrectedPayload,
    MarketFootprintEventType,
    MarketTrade,
    footprint_idempotency_key,
)
from source.families.market import (
    AggressorSide,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    Timeframe,
)
from source.families.registry import expected_event_schema_version, payload_class_for
from source.time import MaturityState

# Ventana 1m alineada: 2026-07-14T00:00:00Z.
OPEN_TIME = 1_784_073_600_000
CLOSE_TIME = OPEN_TIME + 60_000


def _cells() -> tuple[FootprintCell, ...]:
    return (
        FootprintCell(
            price=Decimal("100"),
            buy_volume=Decimal("3"),
            sell_volume=Decimal("1"),
            delta=Decimal("2"),
        ),
        FootprintCell(
            price=Decimal("101"),
            buy_volume=Decimal("2"),
            sell_volume=Decimal("2"),
            delta=Decimal("0"),
        ),
    )


def _footprint(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "exchange": "binance",
        "market_type": MarketType.SPOT,
        "symbol": "BTC-USDT",
        "timeframe": Timeframe.M1,
        "open_time": OPEN_TIME,
        "close_time": CLOSE_TIME,
        "cells": _cells(),
        "bar_buy_volume": Decimal("5"),
        "bar_sell_volume": Decimal("3"),
        "bar_delta": Decimal("2"),
        "trade_count": 8,
        "is_complete": True,
    }
    base.update(overrides)
    return base


def _trade(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "exchange": "binance",
        "market_type": MarketType.SPOT,
        "symbol": "BTC-USDT",
        "trade_id": "12345",
        "price": Decimal("100.5"),
        "qty": Decimal("0.25"),
        "aggressor_side": AggressorSide.BUY,
        "event_time": OPEN_TIME + 1234,
    }
    base.update(overrides)
    return base


class TestStreamKeyTradesFootprint:
    def test_trades_stream_key_sin_timeframe(self) -> None:
        clave = MarketStreamKey(
            exchange="binance",
            market_type=MarketType.SPOT,
            symbol="BTC-USDT",
            data_kind=MarketDataKind.TRADES,
        )
        assert clave.as_stream_key() == "market:trades:binance:spot:BTC-USDT"
        assert MarketStreamKey.parse(clave.as_stream_key()) == clave

    def test_footprint_stream_key_con_timeframe(self) -> None:
        clave = MarketStreamKey(
            exchange="binance",
            market_type=MarketType.SPOT,
            symbol="BTC-USDT",
            data_kind=MarketDataKind.FOOTPRINT,
            timeframe=Timeframe.M1,
        )
        assert clave.as_stream_key() == "market:footprint:binance:spot:BTC-USDT:1m"
        assert MarketStreamKey.parse(clave.as_stream_key()) == clave

    def test_trades_no_admite_timeframe(self) -> None:
        with pytest.raises(ValidationError):
            MarketStreamKey(
                exchange="binance",
                market_type=MarketType.SPOT,
                symbol="BTC-USDT",
                data_kind=MarketDataKind.TRADES,
                timeframe=Timeframe.M1,
            )

    def test_footprint_exige_timeframe(self) -> None:
        with pytest.raises(ValidationError):
            MarketStreamKey(
                exchange="binance",
                market_type=MarketType.SPOT,
                symbol="BTC-USDT",
                data_kind=MarketDataKind.FOOTPRINT,
            )


class TestMarketTrade:
    def test_trade_valido(self) -> None:
        trade = MarketTrade(**_trade())
        assert trade.aggressor_side is AggressorSide.BUY
        assert trade.stream_key() == "market:trades:binance:spot:BTC-USDT"

    @pytest.mark.parametrize("precio", [Decimal("0"), Decimal("-1"), Decimal("NaN")])
    def test_precio_no_valido_rechazado(self, precio: Decimal) -> None:
        with pytest.raises(ValidationError):
            MarketTrade(**_trade(price=precio))

    @pytest.mark.parametrize(
        "tam", [Decimal("0"), Decimal("-0.1"), Decimal("Infinity")]
    )
    def test_tamano_no_valido_rechazado(self, tam: Decimal) -> None:
        with pytest.raises(ValidationError):
            MarketTrade(**_trade(qty=tam))

    def test_lado_agresor_fuera_del_enum_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            MarketTrade(**_trade(aggressor_side="taker"))

    def test_campo_extra_del_exchange_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            MarketTrade(**_trade(campo_desconocido="lo que sea"))


class TestFootprintCell:
    def test_delta_incoherente_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            FootprintCell(
                price=Decimal("100"),
                buy_volume=Decimal("3"),
                sell_volume=Decimal("1"),
                delta=Decimal("5"),  # deberia ser 2
            )

    def test_volumen_negativo_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            FootprintCell(
                price=Decimal("100"),
                buy_volume=Decimal("-1"),
                sell_volume=Decimal("1"),
                delta=Decimal("-2"),
            )

    def test_precio_no_positivo_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            FootprintCell(
                price=Decimal("0"),
                buy_volume=Decimal("1"),
                sell_volume=Decimal("1"),
                delta=Decimal("0"),
            )


class TestFootprintPayload:
    def test_footprint_cerrado_valido(self) -> None:
        fp = FootprintClosedPayload(maturity_state=MaturityState.CLOSED, **_footprint())
        assert fp.stream_key() == "market:footprint:binance:spot:BTC-USDT:1m"
        assert fp.bar_delta == Decimal("2")


class TestCompletitud:
    """is_complete: si la barra vio TODOS sus trades (P07b, modelo de backfill honesto).

    True = se capturaron todos los trades de la ventana. False = un hueco de reconexion
    NO cubierto se solapa con esta barra, asi que le faltan trades y sus celdas no son
    la verdad completa del mercado.
    """

    def test_se_puede_declarar_completo_e_incompleto(self) -> None:
        datos = _footprint()
        datos.pop("is_complete")

        completo = FootprintClosedPayload(
            maturity_state=MaturityState.CLOSED, is_complete=True, **datos
        )
        incompleto = FootprintClosedPayload(
            maturity_state=MaturityState.CLOSED, is_complete=False, **datos
        )

        assert completo.is_complete is True
        assert incompleto.is_complete is False

    def test_omitirlo_da_INCOMPLETO_no_completo(self) -> None:
        # EL DEFAULT ES FAIL-SAFE, y este test es el que lo fija. Un default True
        # convertiria el olvido de un productor en una barra publicada como completa sin
        # serlo, que es exactamente la mentira que el campo existe para impedir. El
        # agregador de 3b lo pone SIEMPRE explicito; el default solo cubre el olvido.
        datos = _footprint()
        datos.pop("is_complete")

        fp = FootprintClosedPayload(maturity_state=MaturityState.CLOSED, **datos)

        assert fp.is_complete is False

    def test_es_ORTOGONAL_a_la_madurez(self) -> None:
        # Una barra puede estar CERRADA (su ventana temporal termino) y ser INCOMPLETA a
        # la vez: durante esa ventana el socket estuvo caido mas de lo que el REST pudo
        # rellenar. Por eso no hay validador que cruce los dos campos.
        datos = _footprint()
        datos.pop("is_complete")

        cerrada_incompleta = FootprintClosedPayload(
            maturity_state=MaturityState.CLOSED, is_complete=False, **datos
        )
        corregida_completa = FootprintCorrectedPayload(
            maturity_state=MaturityState.CORRECTION,
            correction_revision=1,
            corrects_idempotency_key="market.footprint_closed|k|1|closed",
            is_complete=True,
            **datos,
        )

        assert cerrada_incompleta.maturity_state is MaturityState.CLOSED
        assert cerrada_incompleta.is_complete is False
        assert corregida_completa.is_complete is True

    def test_celdas_desordenadas_rechazadas(self) -> None:
        descendente = tuple(reversed(_cells()))
        with pytest.raises(ValidationError):
            FootprintClosedPayload(
                maturity_state=MaturityState.CLOSED,
                **_footprint(cells=descendente),
            )

    def test_celdas_con_precio_repetido_rechazadas(self) -> None:
        repetidas = (
            FootprintCell(
                price=Decimal("100"),
                buy_volume=Decimal("3"),
                sell_volume=Decimal("1"),
                delta=Decimal("2"),
            ),
            FootprintCell(
                price=Decimal("100"),
                buy_volume=Decimal("2"),
                sell_volume=Decimal("2"),
                delta=Decimal("0"),
            ),
        )
        with pytest.raises(ValidationError):
            FootprintClosedPayload(
                maturity_state=MaturityState.CLOSED,
                **_footprint(cells=repetidas),
            )

    def test_ventana_desalineada_rechazada(self) -> None:
        with pytest.raises(ValidationError):
            FootprintClosedPayload(
                maturity_state=MaturityState.CLOSED,
                **_footprint(open_time=OPEN_TIME + 1, close_time=CLOSE_TIME + 1),
            )

    def test_close_time_fuera_de_ventana_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            FootprintClosedPayload(
                maturity_state=MaturityState.CLOSED,
                **_footprint(close_time=OPEN_TIME + 60_001),
            )

    def test_totales_de_barra_descuadrados_rechazados(self) -> None:
        with pytest.raises(ValidationError):
            FootprintClosedPayload(
                maturity_state=MaturityState.CLOSED,
                **_footprint(bar_buy_volume=Decimal("6")),  # la suma real es 5
            )

    def test_bar_delta_incoherente_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            FootprintClosedPayload(
                maturity_state=MaturityState.CLOSED,
                **_footprint(bar_delta=Decimal("3")),  # deberia ser 2
            )

    def test_cerrado_marcado_correccion_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            FootprintClosedPayload(
                maturity_state=MaturityState.CORRECTION, **_footprint()
            )

    def test_correccion_sin_revision_rechazada(self) -> None:
        with pytest.raises(ValidationError):
            FootprintCorrectedPayload(
                maturity_state=MaturityState.CORRECTION,
                corrects_idempotency_key="market.footprint_closed|x|0|closed",
                **_footprint(),
            )

    def test_correccion_sin_referencia_al_original_rechazada(self) -> None:
        with pytest.raises(ValidationError):
            FootprintCorrectedPayload(
                maturity_state=MaturityState.CORRECTION,
                correction_revision=1,
                **_footprint(),
            )

    def test_footprint_valido_de_cada_tipo(self) -> None:
        closed = FootprintClosedPayload(
            maturity_state=MaturityState.CLOSED, **_footprint()
        )
        corrected = FootprintCorrectedPayload(
            maturity_state=MaturityState.CORRECTION,
            corrects_idempotency_key=closed.idempotency_key(
                MarketFootprintEventType.FOOTPRINT_CLOSED
            ),
            correction_revision=1,
            **_footprint(),
        )
        assert closed.stream_key() == corrected.stream_key()


class TestIdempotencyKey:
    def test_formula_de_una_cerrada_verbatim(self) -> None:
        closed = FootprintClosedPayload(
            maturity_state=MaturityState.CLOSED, **_footprint()
        )
        assert closed.idempotency_key(MarketFootprintEventType.FOOTPRINT_CLOSED) == (
            f"market.footprint_closed|market:footprint:binance:spot:BTC-USDT:1m"
            f"|{OPEN_TIME}|closed"
        )

    def test_dos_correcciones_de_la_misma_barra_son_dos_hechos(self) -> None:
        stream_key = "market:footprint:binance:spot:BTC-USDT:1m"
        primera = footprint_idempotency_key(
            event_type=MarketFootprintEventType.FOOTPRINT_CORRECTED,
            stream_key=stream_key,
            open_time=OPEN_TIME,
            maturity_state=MaturityState.CORRECTION,
            correction_revision=1,
        )
        segunda = footprint_idempotency_key(
            event_type=MarketFootprintEventType.FOOTPRINT_CORRECTED,
            stream_key=stream_key,
            open_time=OPEN_TIME,
            maturity_state=MaturityState.CORRECTION,
            correction_revision=2,
        )
        assert primera != segunda

    def test_revision_exigida_en_correccion(self) -> None:
        with pytest.raises(ValueError, match="correction_revision"):
            footprint_idempotency_key(
                event_type=MarketFootprintEventType.FOOTPRINT_CORRECTED,
                stream_key="market:footprint:binance:spot:BTC-USDT:1m",
                open_time=OPEN_TIME,
                maturity_state=MaturityState.CORRECTION,
            )

    def test_reproducibilidad_misma_entrada_misma_clave(self) -> None:
        # El corazon de P07b: los mismos datos producen el mismo footprint y, por
        # tanto, la misma idempotency_key, bit a bit. Dos payloads identicos
        # construidos por separado dan la MISMA clave.
        uno = FootprintClosedPayload(
            maturity_state=MaturityState.CLOSED, **_footprint()
        )
        dos = FootprintClosedPayload(
            maturity_state=MaturityState.CLOSED, **_footprint()
        )
        assert uno.idempotency_key(
            MarketFootprintEventType.FOOTPRINT_CLOSED
        ) == dos.idempotency_key(MarketFootprintEventType.FOOTPRINT_CLOSED)


class TestRegistroCA06:
    def test_los_dos_footprint_resuelven_a_su_payload_concreto(self) -> None:
        assert (
            payload_class_for(MarketFootprintEventType.FOOTPRINT_CLOSED.value)
            is FootprintClosedPayload
        )
        assert (
            payload_class_for(MarketFootprintEventType.FOOTPRINT_CORRECTED.value)
            is FootprintCorrectedPayload
        )

    def test_event_schema_version_de_los_dos(self) -> None:
        for event_type in MarketFootprintEventType:
            assert expected_event_schema_version(event_type.value) == 1
