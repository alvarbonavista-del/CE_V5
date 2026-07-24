"""Traduccion de simbolos y granularidad de OKX. SIN IO.

El contrato usa la forma canonica BASE-QUOTE (BTC-USDT). El instId de OKX YA ES esa
forma, asi que nativo<->canonico es IDENTIDAD (a diferencia de Binance, que usa
BTCUSDT pegado y no puede deshacerlo sin consultar catalogo).

OKX suscribe por (channel, instId): el canal de velas es candle<bar>, con el bar en
mayusculas para horas y dias (1H, 4H, 1D) frente al canonico en minusculas (1h, 4h,
1d). Aqui vive ese mapeo, en los dos sentidos, contra el vocabulario CERRADO Timeframe.
"""

from __future__ import annotations

from source.families.market import Timeframe

# canonico -> bar de OKX. Mismo sufijo para el canal WS (candle<bar>) y el parametro
# 'bar' del REST de velas. OKX ofrece mas (30m, 1W...); solo mapeamos los canonicos.
_TIMEFRAME_TO_OKX_BAR: dict[str, str] = {
    Timeframe.M1.value: "1m",
    Timeframe.M5.value: "5m",
    Timeframe.M15.value: "15m",
    Timeframe.H1.value: "1H",
    Timeframe.H4.value: "4H",
    Timeframe.D1.value: "1D",
}
_OKX_BAR_TO_TIMEFRAME: dict[str, str] = {
    bar: tf for tf, bar in _TIMEFRAME_TO_OKX_BAR.items()
}

# Canal WS de TRADES individuales de OKX. Es 'trades-all', NO 'trades': el sondeo en
# vivo (condicion de Central) confirmo lo que Tardis.dev adelantaba -- 'trades' AGREGA
# trades y 'trades-all' los entrega UNO A UNO --, y el footprint necesita los
# individuales. A diferencia de las velas, el canal de trades NO lleva sufijo de bar: el
# flujo de trades es continuo y no se bucketea a nivel de stream (ADR-014).
_TRADES_CHANNEL = "trades-all"

# Canal WS del LIBRO L2 de OKX. Es 'books' (400 niveles, push cada 100 ms), en /public
# (fuente fijada por Central/I-02-V). El primer mensaje es action=snapshot (semilla) y
# los siguientes action=update (deltas), por el MISMO canal. SIN sufijo de bar: el libro
# no se bucketea por intervalo (ADR-014); su granularidad es la profundidad del canal.
_BOOKS_CHANNEL = "books"


class SymbolTranslationError(ValueError):
    """El simbolo no tiene la forma canonica BASE-QUOTE."""


class TimeframeTranslationError(ValueError):
    """El timeframe (o el bar de OKX) no esta entre los que el sistema soporta."""


def to_native(canonical: str) -> str:
    """El instId de OKX para un simbolo canonico. En OKX es IDENTIDAD (BTC-USDT).

    Se valida la forma BASE-QUOTE y se devuelve tal cual: OKX nombra el par igual que
    el canonico. Aun asi se valida, porque un simbolo que no es BASE-QUOTE no es un par
    que representemos.
    """
    base, sep, quote = canonical.partition("-")
    if not sep or not base or not quote:
        msg = f"simbolo no canonico: {canonical!r}. Se espera BASE-QUOTE (BTC-USDT)."
        raise SymbolTranslationError(msg)
    return canonical


def to_channel(timeframe: str) -> str:
    """Canal WS de velas de OKX para un timeframe canonico: '1h' -> 'candle1H'."""
    return f"candle{_bar(timeframe)}"


def to_trade_channel() -> str:
    """Canal WS de trades individuales de OKX: 'trades-all' (verificado en caliente).

    SIN sufijo de intervalo, y no es una omision: 'trades' agregaria los trades y
    'trades-all' los da uno a uno, que es lo que el footprint agrega. El bucketeo por
    barra pertenece al footprint (dato DERIVADO), no a este stream (ADR-014).
    """
    return _TRADES_CHANNEL


def to_orderbook_channel() -> str:
    """Canal WS del libro L2 de OKX: 'books' (fuente fijada por Central/I-02-V).

    SIN sufijo de intervalo: el libro no se bucketea por barra (ADR-014), su
    granularidad es la profundidad del canal (400 niveles). El snapshot vs delta lo
    distingue el campo 'action' del mensaje (snapshot | update), no el canal.
    """
    return _BOOKS_CHANNEL


def is_orderbook_channel(channel: str) -> bool:
    """True si el canal entrante es el del libro L2.

    El connector enruta con esto el mensaje que llega, igual que reconoce 'candle' de
    las velas y 'trades-all' de los trades: las tres clases comparten conexion y se
    separan por su canal.
    """
    return channel == _BOOKS_CHANNEL


def is_trade_channel(channel: str) -> bool:
    """True si el canal entrante es el de trades individuales.

    El connector enruta con esto el mensaje que llega, igual que reconoce el prefijo
    'candle' de las velas: velas y trades comparten conexion y se separan por su canal.
    """
    return channel == _TRADES_CHANNEL


def to_bar(timeframe: str) -> str:
    """Parametro 'bar' del REST de velas de OKX: '1h' -> '1H'."""
    return _bar(timeframe)


def timeframe_from_channel(channel: str) -> str:
    """Inverso de to_channel: 'candle1H' -> '1h'. Reconoce el mensaje entrante.

    ESTRICTO: un canal que no empieza por 'candle', o cuyo bar no mapea a un timeframe
    soportado, se RECHAZA. Un parser permisivo aceptaria un dia un canal no pedido.
    """
    prefijo = "candle"
    if not channel.startswith(prefijo):
        msg = f"canal OKX no es de velas: {channel!r} (se espera candle<bar>)."
        raise TimeframeTranslationError(msg)
    bar = channel[len(prefijo) :]
    try:
        return _OKX_BAR_TO_TIMEFRAME[bar]
    except KeyError as exc:
        msg = f"bar de OKX {bar!r} no soportado (canal {channel!r})."
        raise TimeframeTranslationError(msg) from exc


def _bar(timeframe: str) -> str:
    try:
        return _TIMEFRAME_TO_OKX_BAR[timeframe]
    except KeyError as exc:
        msg = f"timeframe {timeframe!r} no soportado por OKX en este sistema."
        raise TimeframeTranslationError(msg) from exc
