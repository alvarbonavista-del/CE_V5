"""LA FRONTERA DE CONFIANZA (ADR-006, ADR-007).

Aqui, y SOLO aqui, un dato de un tercero se convierte en un hecho del sistema. Todo
lo que entra por un exchange pasa por esta puerta; lo que no la cruza, no existe.

UN SOLO SITIO, A PROPOSITO: si cada adaptador de exchange validara lo suyo,
tendriamos tres validaciones distintas y una de ellas seria la mas floja; el atacante
elegiria esa. Los adaptadores TRADUCEN; esta funcion DECIDE.

JAMAS se "arregla" un dato: o el hecho es integro, o no existe. Un dato corregido a
ojo es una mentira con formato correcto.
"""

from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import ValidationError

from ce_v5.platform.market.errors import MarketError
from source.families.market import (
    CandleClosedPayload,
    CandlePayload,
    CandleUpdatedPayload,
    MarketCandleEventType,
    MarketStreamKey,
    MarketType,
    RawCandle,
    Timeframe,
)
from source.time import MaturityState


class RawCandleRejectionReason(StrEnum):
    """Por que se rechaza una vela cruda. Conjunto CERRADO (ADR-016)."""

    SYMBOL_MISMATCH = "symbol_mismatch"  # suplantacion de flujo
    MALFORMED_NUMBER = "malformed_number"  # 'abc', '', None
    CONTRACT_VIOLATION = "contract_violation"  # NaN, negativo, high<low, desalineada
    # Cuando exista un flujo SIN timeframe (orderbook/trades, previstos por ADR-014),
    # la clave esperada traera timeframe=None y hara falta un motivo propio. Se
    # anadira ENTONCES, con su test. No antes: una rama que ningun test alcanza es una
    # rama que nadie ha probado.


class RawCandleRejected(MarketError):
    """La vela cruda NO entra. Lleva el motivo como DATO, no como texto."""

    def __init__(
        self,
        reason: RawCandleRejectionReason,
        detail: str,
        expected: MarketStreamKey,
    ) -> None:
        super().__init__(f"{reason.value}: {detail} (flujo esperado: {expected})")
        self.reason = reason
        self.detail = detail
        self.expected = expected


_CAMPOS_NUMERICOS = ("open", "high", "low", "close", "volume")


def _decimal(valor: str, campo: str, expected: MarketStreamKey) -> Decimal:
    """Texto -> Decimal. NUNCA float: un float binario no representa 0.1 exacto."""
    try:
        return Decimal(valor)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise RawCandleRejected(
            RawCandleRejectionReason.MALFORMED_NUMBER,
            f"el campo '{campo}' no es un numero decimal: {valor!r}",
            expected,
        ) from exc


def _verificar_pertenencia(raw: RawCandle, expected: MarketStreamKey) -> None:
    """ANTI-SUPLANTACION. Lo PRIMERO, antes de mirar ningun precio.

    La vela debe pertenecer AL FLUJO QUE SE PIDIO. Un exchange comprometido, un bug
    suyo o un intermediario podrian colar una vela de OTRO simbolo por el stream de
    BTC-USDT; si la aceptasemos, estariamos escribiendo el precio de una moneda en el
    historico de OTRA, y una regla dispararia sobre un precio que no es el suyo.
    """
    esperado_timeframe = (
        None if expected.timeframe is None else expected.timeframe.value
    )
    desajustes = [
        ("exchange", raw.exchange, expected.exchange),
        ("market_type", raw.market_type, expected.market_type.value),
        ("symbol", raw.symbol, expected.symbol),
        ("timeframe", raw.timeframe, esperado_timeframe),
    ]
    for campo, recibido, esperado in desajustes:
        if recibido != esperado:
            raise RawCandleRejected(
                RawCandleRejectionReason.SYMBOL_MISMATCH,
                f"la vela dice {campo}={recibido!r} pero el stream suscrito es "
                f"{campo}={esperado!r}: no pertenece a este flujo",
                expected,
            )


def candle_payload_from_raw(
    raw: RawCandle, expected: MarketStreamKey
) -> tuple[MarketCandleEventType, CandlePayload]:
    """Convierte una vela cruda en un HECHO del sistema, o la RECHAZA.

    ANTI-SUPLANTACION (primero de todo): la vela debe pertenecer AL FLUJO QUE SE
    PIDIO. Si no coincide exchange, market_type, symbol o timeframe: SYMBOL_MISMATCH,
    y la vela NO entra.

    Despues: Decimal(texto) con captura de InvalidOperation (MALFORMED_NUMBER), y
    construccion del payload CONCRETO segun is_closed (CandleClosedPayload o
    CandleUpdatedPayload). Cualquier ValidationError del contrato (NaN, precio
    negativo, rango incoherente, vela desalineada, ventana imposible) se traduce a
    CONTRACT_VIOLATION.

    JAMAS devuelve un payload a medias ni "arregla" un dato: o el hecho es integro, o
    no existe.
    """
    _verificar_pertenencia(raw, expected)

    # Superado el control anti-suplantacion, market_type y timeframe son IGUALES a los
    # de la clave suscrita, que ya viene TIPADA: por construccion son valores validos
    # del vocabulario, y convertirlos no puede fallar. No se envuelve en un try: un
    # motivo de rechazo que ningun test puede alcanzar seria una rama que nadie ha
    # probado. Si algun dia fallara, que sea FAIL-LOUD y se vea.
    market_type = MarketType(raw.market_type)
    timeframe = Timeframe(raw.timeframe)

    numeros = {
        campo: _decimal(getattr(raw, campo), campo, expected)
        for campo in _CAMPOS_NUMERICOS
    }

    # El payload CONCRETO fija su maturity_state: una vela cerrada marcada como
    # provisional la rechaza el CONTRATO, no un if perdido por aqui.
    if raw.is_closed:
        event_type = MarketCandleEventType.CANDLE_CLOSED
        payload_cls: type[CandlePayload] = CandleClosedPayload
        maturity = MaturityState.CLOSED
    else:
        event_type = MarketCandleEventType.CANDLE_UPDATED
        payload_cls = CandleUpdatedPayload
        maturity = MaturityState.PROVISIONAL

    try:
        payload = payload_cls(
            maturity_state=maturity,
            exchange=raw.exchange,
            market_type=market_type,
            symbol=raw.symbol,
            timeframe=timeframe,
            open_time=raw.open_time_ms,
            close_time=raw.close_time_ms,
            **numeros,
        )
    except ValidationError as exc:
        # NaN, Infinity, precio no positivo, volumen negativo, rango OHLC
        # incoherente, vela desalineada, ventana imposible: el CONTRATO lo caza.
        raise RawCandleRejected(
            RawCandleRejectionReason.CONTRACT_VIOLATION,
            f"la vela viola el contrato: {exc.error_count()} error(es)",
            expected,
        ) from exc

    return event_type, payload
