"""Traduccion de mensajes kline de Binance. SIN RED.

Los mensajes de este fichero tienen la forma EXACTA que documenta Binance para el
stream <symbol>@kline_<interval>. Estan grabados aqui a proposito: probar la
traduccion contra el Binance real seria probar la red, no nuestra logica, y ademas
haria el CI dependiente de un tercero que puede estar caido o banearnos la IP.

El IO de verdad (connector.py) NO se prueba en CI: se valida EN CALIENTE (B12). Es la
unica forma honesta de comprobar que un socket funciona, y queda declarado (regla
5.18: la diferencia entre lo que cubre el CI y lo que cubre la validacion en caliente
se escribe, no se supone).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from ce_v5.infra.connectors.binance.translate import (
    BinanceTranslationError,
    raw_candle_from_binance,
    supported_binance_timeframes,
)
from ce_v5.platform.market.normalize import candle_payload_from_raw
from source.families.market import (
    CandleClosedPayload,
    CandleUpdatedPayload,
    MarketCandleEventType,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    Timeframe,
)
from source.time import MaturityState

_OPEN = 1_784_073_600_000
_CLOSE = _OPEN + 59_999

_CLAVE = MarketStreamKey(
    exchange="binance",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.CANDLES,
    timeframe=Timeframe.M1,
)


def _mensaje(*, cerrada: bool, **overrides: Any) -> dict[str, Any]:
    """Un kline de Binance con su forma REAL (web-socket-streams.md)."""
    kline: dict[str, Any] = {
        "t": _OPEN,  # open time
        "T": _CLOSE,  # close time
        "s": "BTCUSDT",  # simbolo NATIVO
        "i": "1m",  # intervalo
        "f": 100,  # first trade id
        "L": 200,  # last trade id (monotono)
        "o": "100.00000000",  # los precios llegan como TEXTO
        "c": "105.00000000",
        "h": "110.00000000",
        "l": "95.00000000",
        "v": "12.50000000",
        "n": 50,
        "x": cerrada,  # <- si la vela esta CERRADA lo dice el exchange
        "q": "1300.0",
        "V": "6.0",
        "Q": "600.0",
        "B": "0",
    }
    kline.update(overrides)
    return {
        "e": "kline",
        "E": _OPEN + 42,  # event_time DEL EXCHANGE
        "s": "BTCUSDT",
        "k": kline,
    }


class TestTraduccion:
    def test_vela_cerrada(self) -> None:
        raw = raw_candle_from_binance(
            _mensaje(cerrada=True), canonical_symbol="BTC-USDT", market_type="spot"
        )

        assert raw.exchange == "binance"
        assert raw.symbol == "BTC-USDT"  # CANONICO, no el nativo BTCUSDT.
        assert raw.timeframe == "1m"
        assert raw.open_time_ms == _OPEN
        assert raw.close_time_ms == _CLOSE
        # Los precios se copian TAL CUAL, como TEXTO: ni float, ni redondeo.
        assert raw.open == "100.00000000"
        assert raw.close == "105.00000000"
        assert raw.volume == "12.50000000"
        assert raw.is_closed is True  # de 'x'
        assert raw.event_time_ms == _OPEN + 42  # de 'E', el reloj DEL EXCHANGE
        assert raw.source_sequence == 200  # de 'L'

    def test_vela_en_formacion(self) -> None:
        raw = raw_candle_from_binance(
            _mensaje(cerrada=False), canonical_symbol="BTC-USDT", market_type="spot"
        )
        assert raw.is_closed is False

    def test_timeframes_soportados(self) -> None:
        # Binance sirve mas intervalos (3m, 2h, 1w...), pero solo declaramos los que el
        # sistema usa: declarar lo que no se usa es vocabulario muerto.
        assert supported_binance_timeframes() == frozenset(
            {
                Timeframe.M1,
                Timeframe.M5,
                Timeframe.M15,
                Timeframe.H1,
                Timeframe.H4,
                Timeframe.D1,
            }
        )


class TestMensajesMalformados:
    @pytest.mark.parametrize("clave", ["t", "T", "o", "h", "l", "c", "v", "x", "i"])
    def test_falta_una_clave_de_la_vela(self, clave: str) -> None:
        # NUNCA un RawCandle a medias: o el mensaje esta completo, o excepcion. El
        # lector la convierte en metrica observable, no en un dato.
        mensaje = _mensaje(cerrada=True)
        del mensaje["k"][clave]
        with pytest.raises(BinanceTranslationError, match=clave):
            raw_candle_from_binance(mensaje, "BTC-USDT", "spot")

    def test_falta_el_event_time(self) -> None:
        mensaje = _mensaje(cerrada=True)
        del mensaje["E"]
        with pytest.raises(BinanceTranslationError, match="E"):
            raw_candle_from_binance(mensaje, "BTC-USDT", "spot")

    def test_falta_el_bloque_kline(self) -> None:
        with pytest.raises(BinanceTranslationError, match="k"):
            raw_candle_from_binance({"e": "kline", "E": 1}, "BTC-USDT", "spot")

    def test_intervalo_que_no_pedimos(self) -> None:
        # '3m' es un intervalo REAL de Binance que nosotros no usamos. Se descarta
        # igual: abrir la puerta a lo que nadie pidio es abrirla.
        with pytest.raises(BinanceTranslationError, match="3m"):
            raw_candle_from_binance(_mensaje(cerrada=True, i="3m"), "BTC-USDT", "spot")


class TestEndToEndSinRed:
    """Traduccion + frontera de confianza, encajadas. El CI prueba la cadena entera."""

    def test_una_vela_cerrada_de_binance_llega_a_ser_un_hecho_del_sistema(self) -> None:
        raw = raw_candle_from_binance(_mensaje(cerrada=True), "BTC-USDT", "spot")

        event_type, payload = candle_payload_from_raw(raw, _CLAVE)

        assert event_type is MarketCandleEventType.CANDLE_CLOSED
        assert isinstance(payload, CandleClosedPayload)
        assert payload.maturity_state is MaturityState.CLOSED
        assert payload.close == Decimal("105.00000000")
        assert payload.stream_key() == "market:candles:binance:spot:BTC-USDT:1m"

    def test_una_vela_en_formacion_llega_como_provisional(self) -> None:
        raw = raw_candle_from_binance(_mensaje(cerrada=False), "BTC-USDT", "spot")

        event_type, payload = candle_payload_from_raw(raw, _CLAVE)

        assert event_type is MarketCandleEventType.CANDLE_UPDATED
        assert isinstance(payload, CandleUpdatedPayload)
        assert payload.maturity_state is MaturityState.PROVISIONAL

    def test_una_vela_incoherente_de_binance_NO_entra(self) -> None:
        # Binance tambien puede mandar basura (un bug suyo, un intermediario). La
        # traduccion la deja pasar (solo traduce formato); quien dice que no es la
        # FRONTERA DE CONFIANZA. Aqui se ve la separacion funcionando.
        raw = raw_candle_from_binance(
            _mensaje(cerrada=True, h="90.0"),  # maximo POR DEBAJO del cierre
            "BTC-USDT",
            "spot",
        )
        assert raw.high == "90.0"  # la traduccion NO lo arregla...

        from ce_v5.platform.market.normalize import RawCandleRejected

        with pytest.raises(RawCandleRejected):  # ...y la frontera lo rechaza.
            candle_payload_from_raw(raw, _CLAVE)
