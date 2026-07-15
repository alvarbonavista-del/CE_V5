"""Tests de LA FRONTERA DE CONFIANZA (ADR-006).

Un exchange es entrada NO confiable. Aqui se demuestra que ningun dato roto, ajeno o
ilegible se convierte en un hecho del sistema, y que NUNCA se devuelve un payload
"arreglado": o el hecho es integro, o se lanza. Un dato corregido a ojo es una mentira
con formato correcto.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from ce_v5.platform.market.normalize import (
    RawCandleRejected,
    RawCandleRejectionReason,
    candle_payload_from_raw,
)
from source.families.market import (
    CandleClosedPayload,
    CandleUpdatedPayload,
    MarketCandleEventType,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    RawCandle,
    Timeframe,
)
from source.time import MaturityState

# Ventana 1m alineada.
_OPEN = 1_784_073_600_000
_CLOSE = _OPEN + 59_999

_ESPERADO = MarketStreamKey(
    exchange="binance",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.CANDLES,
    timeframe=Timeframe.M1,
)


def _raw(**overrides: object) -> RawCandle:
    base: dict[str, object] = {
        "exchange": "binance",
        "market_type": "spot",
        "symbol": "BTC-USDT",
        "timeframe": "1m",
        "open_time_ms": _OPEN,
        "close_time_ms": _CLOSE,
        "open": "100.00",
        "high": "110.00",
        "low": "95.00",
        "close": "105.00",
        "volume": "12.5",
        "is_closed": True,
        # ADR-007: el instante lo pone el EXCHANGE, no nuestro reloj.
        "event_time_ms": _CLOSE,
    }
    base.update(overrides)
    return RawCandle(**base)  # type: ignore[arg-type]


def _motivo(raw: RawCandle) -> RawCandleRejectionReason:
    with pytest.raises(RawCandleRejected) as excinfo:
        candle_payload_from_raw(raw, _ESPERADO)
    return excinfo.value.reason


class TestVelasIntegras:
    def test_vela_cerrada(self) -> None:
        event_type, payload = candle_payload_from_raw(_raw(), _ESPERADO)

        assert event_type is MarketCandleEventType.CANDLE_CLOSED
        assert isinstance(payload, CandleClosedPayload)
        assert payload.maturity_state is MaturityState.CLOSED
        # Los precios son Decimal, JAMAS float: en M5 esto es dinero.
        assert payload.close == Decimal("105.00")

    def test_vela_en_formacion(self) -> None:
        event_type, payload = candle_payload_from_raw(_raw(is_closed=False), _ESPERADO)

        assert event_type is MarketCandleEventType.CANDLE_UPDATED
        assert isinstance(payload, CandleUpdatedPayload)
        assert payload.maturity_state is MaturityState.PROVISIONAL


class TestAntiSuplantacion:
    def test_una_vela_de_otro_simbolo_por_el_stream_de_btc(self) -> None:
        # POR QUE ESTO IMPORTA: si la aceptasemos, estariamos escribiendo el precio de
        # UNA moneda en el historico de OTRA. Una regla de alerta o de trading
        # dispararia sobre un precio que no es el suyo, y en M5 eso es una ORDEN REAL
        # a un precio ajeno. Un exchange comprometido, un bug suyo o un intermediario
        # pueden colar esto; el sistema tiene que decir que no.
        assert (
            _motivo(_raw(symbol="ETH-USDT")) is RawCandleRejectionReason.SYMBOL_MISMATCH
        )

    def test_una_vela_de_otro_timeframe_por_el_stream_de_1m(self) -> None:
        # Una vela de 1h colada por el stream de 1m falsearia el historico de 1m: las
        # reglas de 1m evaluarian sobre un dato que abarca sesenta veces mas tiempo.
        assert _motivo(_raw(timeframe="1h")) is RawCandleRejectionReason.SYMBOL_MISMATCH

    def test_una_vela_de_otro_exchange(self) -> None:
        assert _motivo(_raw(exchange="okx")) is RawCandleRejectionReason.SYMBOL_MISMATCH

    def test_una_vela_de_otro_market_type(self) -> None:
        assert (
            _motivo(_raw(market_type="futures"))
            is RawCandleRejectionReason.SYMBOL_MISMATCH
        )


class TestNumerosIlegibles:
    @pytest.mark.parametrize("basura", ["abc", "", "  ", "1,5"])
    def test_precio_ilegible(self, basura: str) -> None:
        assert _motivo(_raw(close=basura)) is RawCandleRejectionReason.MALFORMED_NUMBER

    def test_volumen_ilegible(self) -> None:
        assert (
            _motivo(_raw(volume="mucho")) is RawCandleRejectionReason.MALFORMED_NUMBER
        )


class TestViolacionesDelContrato:
    @pytest.mark.parametrize("valor", ["NaN", "Infinity", "-Infinity"])
    def test_precio_no_finito(self, valor: str) -> None:
        # NaN e Infinity SON Decimal validos (no son numeros ilegibles): los caza el
        # CONTRATO, no el parser. Por eso el motivo es CONTRACT_VIOLATION.
        assert _motivo(_raw(high=valor)) is RawCandleRejectionReason.CONTRACT_VIOLATION

    def test_precio_negativo(self) -> None:
        assert _motivo(_raw(low="-1")) is RawCandleRejectionReason.CONTRACT_VIOLATION

    def test_volumen_negativo(self) -> None:
        assert (
            _motivo(_raw(volume="-0.1")) is RawCandleRejectionReason.CONTRACT_VIOLATION
        )

    def test_maximo_menor_que_minimo(self) -> None:
        assert (
            _motivo(_raw(high="90", low="95"))
            is RawCandleRejectionReason.CONTRACT_VIOLATION
        )

    def test_vela_desalineada_con_su_intervalo(self) -> None:
        assert (
            _motivo(_raw(open_time_ms=_OPEN + 1, close_time_ms=_CLOSE + 1))
            is RawCandleRejectionReason.CONTRACT_VIOLATION
        )

    def test_ventana_imposible(self) -> None:
        assert (
            _motivo(_raw(close_time_ms=_OPEN + 60_001))
            is RawCandleRejectionReason.CONTRACT_VIOLATION
        )


class TestTimeframeDesconocido:
    def test_timeframe_fuera_del_vocabulario_se_rechaza_como_suplantacion(self) -> None:
        # '2m' no existe en el vocabulario canonico, y la vela SE RECHAZA. Pero el
        # motivo NO es UNKNOWN_TIMEFRAME, sino SYMBOL_MISMATCH, y esto es correcto:
        # el stream suscrito es de 1m, asi que una vela que dice '2m' es, ANTES QUE
        # NADA, una vela que no pertenece a este flujo. El control anti-suplantacion
        # va PRIMERO a proposito (es el critico), y con una clave suscrita siempre
        # tipada, cualquier timeframe desconocido difiere del esperado y cae aqui.
        # Lo que importa esta garantizado: la vela NO ENTRA.
        assert _motivo(_raw(timeframe="2m")) is RawCandleRejectionReason.SYMBOL_MISMATCH


class TestJamasSeArreglaUnDato:
    @pytest.mark.parametrize(
        "roto",
        [
            {"symbol": "ETH-USDT"},
            {"close": "abc"},
            {"high": "NaN"},
            {"low": "-1"},
            {"open_time_ms": _OPEN + 7},
        ],
    )
    def test_ninguna_ruta_devuelve_payload_con_un_dato_invalido(
        self, roto: dict[str, object]
    ) -> None:
        # La propiedad que lo resume todo: si el dato no es integro, NO SALE NADA. Ni
        # un payload a medias, ni un valor "corregido", ni un None que alguien tratara
        # como vacio. Se lanza.
        with pytest.raises(RawCandleRejected):
            candle_payload_from_raw(_raw(**roto), _ESPERADO)
