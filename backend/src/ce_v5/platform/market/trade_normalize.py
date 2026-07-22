"""LA FRONTERA DE CONFIANZA DE LOS TRADES (ADR-006, ADR-007).

Gemela de normalize.py (velas) y por el MISMO motivo: aqui, y solo aqui, un trade de un
tercero se convierte en un hecho del sistema. Todo lo que entra por un exchange pasa por
esta puerta; lo que no la cruza, no existe.

UN SOLO SITIO, A PROPOSITO: si cada adaptador de exchange validara lo suyo, tendriamos
tres validaciones distintas y una de ellas seria la mas floja; el atacante elegiria esa.
Los adaptadores TRADUCEN (el flag `m` de Binance, `S` de Bybit, `side` de OKX a
'buy'|'sell'); esta funcion DECIDE.

JAMAS se "arregla" un dato: o el hecho es integro, o no existe. Un trade con el precio
corregido a ojo es una mentira con formato correcto, y un trade es la materia prima del
footprint: una mentira aqui se propaga a cada celda de la barra.
"""

from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import ValidationError

from ce_v5.platform.market.errors import MarketError
from source.families.footprint import MarketTrade
from source.families.market import MarketStreamKey, MarketType, RawTrade


class RawTradeRejectionReason(StrEnum):
    """Por que se rechaza un trade crudo. Conjunto CERRADO (ADR-016).

    Los mismos tres motivos que la vela, porque son las mismas tres formas de que un
    dato de tercero sea inaceptable: no es mio, no es un numero, o no cumple el
    contrato. No hay un motivo por timeframe: la clave de trades no lo lleva (ADR-014).
    """

    SYMBOL_MISMATCH = "symbol_mismatch"  # suplantacion de flujo
    MALFORMED_NUMBER = "malformed_number"  # 'abc', '', None
    CONTRACT_VIOLATION = "contract_violation"  # NaN, no positivo, lado desconocido


class RawTradeRejected(MarketError):
    """El trade crudo NO entra. Lleva el motivo como DATO, no como texto."""

    def __init__(
        self,
        reason: RawTradeRejectionReason,
        detail: str,
        expected: MarketStreamKey,
    ) -> None:
        super().__init__(f"{reason.value}: {detail} (flujo esperado: {expected})")
        self.reason = reason
        self.detail = detail
        self.expected = expected


def _decimal(valor: str, campo: str, expected: MarketStreamKey) -> Decimal:
    """Texto -> Decimal. NUNCA float: un float binario no representa 0.1 exacto.

    Y en trades esto es doblemente critico: el footprint AGREGA volumenes trade a
    trade, asi que un error de redondeo por operacion se acumula barra tras barra hasta
    que la celda ya no es la suma de sus trades.
    """
    try:
        return Decimal(valor)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise RawTradeRejected(
            RawTradeRejectionReason.MALFORMED_NUMBER,
            f"el campo '{campo}' no es un numero decimal: {valor!r}",
            expected,
        ) from exc


def _verificar_pertenencia(raw: RawTrade, expected: MarketStreamKey) -> None:
    """ANTI-SUPLANTACION. Lo PRIMERO, antes de mirar ningun precio.

    El trade debe pertenecer AL FLUJO QUE SE PIDIO. Un exchange comprometido, un bug
    suyo o un intermediario podrian colar un trade de OTRO simbolo por el stream de
    BTC-USDT; si lo aceptasemos, estariamos metiendo el precio de una moneda en el
    footprint de OTRA, y una regla de orderflow dispararia sobre un volumen que no es
    el suyo.

    NO se compara timeframe: la clave de trades no lo lleva (MarketStreamKey lo prohibe
    para data_kind=trades) y RawTrade tampoco lo trae. El flujo de trades es continuo;
    el bucketeo por barra pertenece al footprint, que es dato derivado.
    """
    desajustes = [
        ("exchange", raw.exchange, expected.exchange),
        ("market_type", raw.market_type, expected.market_type.value),
        ("symbol", raw.symbol, expected.symbol),
    ]
    for campo, recibido, esperado in desajustes:
        if recibido != esperado:
            raise RawTradeRejected(
                RawTradeRejectionReason.SYMBOL_MISMATCH,
                f"el trade dice {campo}={recibido!r} pero el stream suscrito es "
                f"{campo}={esperado!r}: no pertenece a este flujo",
                expected,
            )


def trade_from_raw(raw: RawTrade, expected: MarketStreamKey) -> MarketTrade:
    """Convierte un trade crudo en un HECHO del sistema, o lo RECHAZA.

    ANTI-SUPLANTACION (primero de todo): el trade debe pertenecer AL FLUJO QUE SE PIDIO.
    Si no coincide exchange, market_type o symbol: SYMBOL_MISMATCH, y no entra.

    Despues: Decimal(texto) de price y qty con captura de InvalidOperation
    (MALFORMED_NUMBER) y construccion de MarketTrade. El lado agresor entra como TEXTO y
    lo valida el enum CERRADO del contrato: un 'taker', un 'BUY' o un lado vacio no son
    AggressorSide y el contrato los caza. Cualquier ValidationError (precio o tamano no
    positivo, NaN, Infinity, lado desconocido, trade_id vacio o desmesurado) se traduce
    a CONTRACT_VIOLATION.

    JAMAS devuelve un trade a medias ni "arregla" un dato: o el hecho es integro, o no
    existe.
    """
    _verificar_pertenencia(raw, expected)

    # Superado el control anti-suplantacion, market_type es IGUAL al de la clave
    # suscrita, que ya viene TIPADA: por construccion es un valor valido del vocabulario
    # y convertirlo no puede fallar. No se envuelve en un try, igual que en las velas:
    # un motivo de rechazo que ningun test puede alcanzar seria una rama que nadie ha
    # probado. Si algun dia fallara, que sea FAIL-LOUD y se vea.
    market_type = MarketType(raw.market_type)

    price = _decimal(raw.price, "price", expected)
    qty = _decimal(raw.qty, "qty", expected)

    try:
        return MarketTrade(
            exchange=raw.exchange,
            market_type=market_type,
            symbol=raw.symbol,
            trade_id=raw.trade_id,
            price=price,
            qty=qty,
            # El lado llega como TEXTO del adaptador y lo valida el enum del contrato:
            # la traduccion del flag nativo es del adaptador, la DECISION es de aqui.
            aggressor_side=raw.aggressor_side,
            event_time=raw.event_time_ms,
            source_sequence=raw.source_sequence,
        )
    except ValidationError as exc:
        raise RawTradeRejected(
            RawTradeRejectionReason.CONTRACT_VIOLATION,
            f"el trade viola el contrato: {exc.error_count()} error(es)",
            expected,
        ) from exc
