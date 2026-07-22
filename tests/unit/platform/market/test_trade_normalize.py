"""Tests de LA FRONTERA DE CONFIANZA DE LOS TRADES (ADR-006).

Un exchange es entrada NO confiable, y un trade lo es doblemente: es la materia prima
del footprint, asi que un solo trade roto que entrase corromperia todas las celdas de su
barra. Aqui se demuestra que ningun trade roto, ajeno o ilegible se convierte en un
hecho del sistema, y que NUNCA se devuelve un trade "arreglado": o el hecho es integro,
o se lanza. Un dato corregido a ojo es una mentira con formato correcto.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from ce_v5.platform.market.trade_normalize import (
    RawTradeRejected,
    RawTradeRejectionReason,
    trade_from_raw,
)
from source.families.market import (
    AggressorSide,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    RawTrade,
)

_EVENT_TIME = 1_784_073_600_042

# La clave de un flujo de TRADES: sin timeframe (ADR-014). El contrato lo prohibe para
# data_kind=trades, y por eso la normalizacion no compara timeframe: no hay ninguno.
_ESPERADO = MarketStreamKey(
    exchange="binance",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.TRADES,
)


def _raw(**overrides: object) -> RawTrade:
    base: dict[str, object] = {
        "exchange": "binance",
        "market_type": "spot",
        "symbol": "BTC-USDT",
        "trade_id": "77001",
        "price": "104.25",
        "qty": "0.13",
        "aggressor_side": "buy",
        # ADR-007: el instante lo pone el EXCHANGE, no nuestro reloj.
        "event_time_ms": _EVENT_TIME,
    }
    base.update(overrides)
    return RawTrade(**base)  # type: ignore[arg-type]


def _motivo(raw: RawTrade) -> RawTradeRejectionReason:
    with pytest.raises(RawTradeRejected) as excinfo:
        trade_from_raw(raw, _ESPERADO)
    return excinfo.value.reason


class TestTradesIntegros:
    def test_un_trade_valido_se_convierte_en_hecho(self) -> None:
        trade = trade_from_raw(_raw(), _ESPERADO)

        assert trade.exchange == "binance"
        assert trade.market_type is MarketType.SPOT
        assert trade.symbol == "BTC-USDT"
        assert trade.trade_id == "77001"
        # Los precios y tamanos son Decimal, JAMAS float: el footprint los SUMA trade a
        # trade, y un error de redondeo por operacion se acumula barra tras barra.
        assert trade.price == Decimal("104.25")
        assert trade.qty == Decimal("0.13")
        assert isinstance(trade.price, Decimal)
        # El lado entro como TEXTO y salio como el enum CERRADO del contrato.
        assert trade.aggressor_side is AggressorSide.BUY
        assert trade.event_time == _EVENT_TIME

    def test_el_lado_vendedor_tambien_pasa(self) -> None:
        trade = trade_from_raw(_raw(aggressor_side="sell"), _ESPERADO)
        assert trade.aggressor_side is AggressorSide.SELL

    def test_la_secuencia_del_origen_se_conserva(self) -> None:
        # source_sequence es del ORIGEN: sirve para detectar huecos en el feed. Se pasa
        # tal cual; inventarlo o normalizarlo destruiria justo esa senal.
        trade = trade_from_raw(_raw(source_sequence=4210), _ESPERADO)
        assert trade.source_sequence == 4210

    def test_el_stream_key_del_trade_no_lleva_timeframe(self) -> None:
        # El flujo de trades es continuo: no se bucketea a nivel de stream (ADR-014).
        trade = trade_from_raw(_raw(), _ESPERADO)
        assert trade.stream_key() == "market:trades:binance:spot:BTC-USDT"
        assert trade.stream_key() == _ESPERADO.as_stream_key()


class TestSuplantacionDeFlujo:
    """ANTI-SUPLANTACION: lo PRIMERO que se comprueba, antes de mirar ningun precio.

    Si un trade de OTRO simbolo entrase por el stream de BTC, meteriamos el volumen de
    una moneda en el footprint de OTRA, y una regla de orderflow dispararia sobre un
    volumen que no es el suyo.
    """

    def test_otro_exchange(self) -> None:
        assert _motivo(_raw(exchange="okx")) is RawTradeRejectionReason.SYMBOL_MISMATCH

    def test_otro_simbolo(self) -> None:
        assert (
            _motivo(_raw(symbol="ETH-USDT")) is RawTradeRejectionReason.SYMBOL_MISMATCH
        )

    def test_otro_tipo_de_mercado(self) -> None:
        assert (
            _motivo(_raw(market_type="futures"))
            is RawTradeRejectionReason.SYMBOL_MISMATCH
        )

    def test_se_comprueba_antes_que_los_numeros(self) -> None:
        # Un trade ajeno CON el precio roto se rechaza por AJENO, no por el precio: la
        # pertenencia se decide antes de mirar nada mas. Si el orden fuera el contrario,
        # el motivo registrado ocultaria que alguien intento colar un flujo ajeno.
        assert (
            _motivo(_raw(symbol="ETH-USDT", price="abc"))
            is RawTradeRejectionReason.SYMBOL_MISMATCH
        )


class TestNumerosIlegibles:
    def test_precio_que_no_es_un_numero(self) -> None:
        assert _motivo(_raw(price="abc")) is RawTradeRejectionReason.MALFORMED_NUMBER

    def test_precio_vacio(self) -> None:
        assert _motivo(_raw(price="")) is RawTradeRejectionReason.MALFORMED_NUMBER

    def test_tamano_que_no_es_un_numero(self) -> None:
        assert _motivo(_raw(qty="mucho")) is RawTradeRejectionReason.MALFORMED_NUMBER

    def test_el_motivo_dice_QUE_campo_fallo(self) -> None:
        # El detalle es DIAGNOSTICO: sin el, "malformed_number" no dice si el exchange
        # rompio el precio o el tamano, y son averias distintas.
        with pytest.raises(RawTradeRejected) as excinfo:
            trade_from_raw(_raw(qty="mucho"), _ESPERADO)
        assert "qty" in excinfo.value.detail


class TestViolacionesDelContrato:
    def test_lado_agresor_que_no_existe(self) -> None:
        # 'taker' no es un LADO: dice que fue agresor, no si compro o vendio. Si
        # entrase, el footprint no sabria en que columna sumarlo.
        assert (
            _motivo(_raw(aggressor_side="taker"))
            is RawTradeRejectionReason.CONTRACT_VIOLATION
        )

    def test_lado_agresor_vacio(self) -> None:
        assert (
            _motivo(_raw(aggressor_side=""))
            is RawTradeRejectionReason.CONTRACT_VIOLATION
        )

    def test_precio_cero(self) -> None:
        assert _motivo(_raw(price="0")) is RawTradeRejectionReason.CONTRACT_VIOLATION

    def test_precio_negativo(self) -> None:
        assert _motivo(_raw(price="-1")) is RawTradeRejectionReason.CONTRACT_VIOLATION

    def test_tamano_cero(self) -> None:
        # Un trade con tamano 0 no es un trade: es ruido con formato de hecho.
        assert _motivo(_raw(qty="0")) is RawTradeRejectionReason.CONTRACT_VIOLATION

    def test_tamano_negativo(self) -> None:
        assert _motivo(_raw(qty="-0.5")) is RawTradeRejectionReason.CONTRACT_VIOLATION

    def test_precio_nan(self) -> None:
        # 'NaN' SI es un Decimal valido (pasa MALFORMED_NUMBER): lo caza el CONTRATO,
        # que exige finito. Sin esa comprobacion, un NaN se propagaria a cada suma del
        # footprint y toda la barra quedaria NaN, en silencio.
        assert _motivo(_raw(price="NaN")) is RawTradeRejectionReason.CONTRACT_VIOLATION

    def test_tamano_infinito(self) -> None:
        assert (
            _motivo(_raw(qty="Infinity")) is RawTradeRejectionReason.CONTRACT_VIOLATION
        )

    def test_trade_id_vacio(self) -> None:
        # El trade_id es la CLAVE DE DEDUP: sin el, una reconexion duplicaria el trade y
        # el footprint contaria dos veces el mismo volumen.
        assert _motivo(_raw(trade_id="")) is RawTradeRejectionReason.CONTRACT_VIOLATION


class TestElMotivoEsDato:
    def test_el_rechazo_lleva_motivo_y_flujo_esperado(self) -> None:
        # El reason_code es DATO, no texto libre (ADR-016): la UI lo renderiza por i18n
        # y las metricas cuentan por motivo. Una cadena hardcodeada no sirve para
        # ninguna de las dos cosas.
        with pytest.raises(RawTradeRejected) as excinfo:
            trade_from_raw(_raw(price="abc"), _ESPERADO)
        rechazo = excinfo.value
        assert rechazo.reason is RawTradeRejectionReason.MALFORMED_NUMBER
        assert rechazo.reason.value == "malformed_number"
        assert rechazo.expected == _ESPERADO
