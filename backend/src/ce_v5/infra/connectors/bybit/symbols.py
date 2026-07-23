"""Traduccion de simbolos y granularidad de Bybit v5. SIN IO.

El contrato usa BASE-QUOTE (BTC-USDT). Bybit usa la forma PEGADA (BTCUSDT), como
Binance: la vuelta nativo->canonico NO se calcula (de 'BTCUSDT' no se deduce donde
parte), se CONSULTA el catalogo. Por eso el connector de Bybit implementa SymbolMapSink
(a diferencia de OKX, cuyo instId ya era canonico).

Bybit suscribe por topic kline.{interval}.{symbol}, con el intervalo en su codigo propio
(1, 5, 15, 60, 240, D) frente al canonico (1m, 5m, 15m, 1h, 4h, 1d).
"""

from __future__ import annotations

from source.families.market import Timeframe

_TIMEFRAME_TO_BYBIT: dict[str, str] = {
    Timeframe.M1.value: "1",
    Timeframe.M5.value: "5",
    Timeframe.M15.value: "15",
    Timeframe.H1.value: "60",
    Timeframe.H4.value: "240",
    Timeframe.D1.value: "D",
}
_BYBIT_TO_TIMEFRAME: dict[str, str] = {
    codigo: tf for tf, codigo in _TIMEFRAME_TO_BYBIT.items()
}


class SymbolTranslationError(ValueError):
    """El simbolo no tiene la forma canonica BASE-QUOTE."""


class TimeframeTranslationError(ValueError):
    """El timeframe (o el codigo de intervalo de Bybit) no esta soportado."""


def to_native(canonical: str) -> str:
    """'BTC-USDT' -> 'BTCUSDT' (la forma pegada que Bybit entiende)."""
    base, sep, quote = canonical.partition("-")
    if not sep or not base or not quote:
        msg = f"simbolo no canonico: {canonical!r}. Se espera BASE-QUOTE (BTC-USDT)."
        raise SymbolTranslationError(msg)
    return f"{base}{quote}"


def to_interval(timeframe: str) -> str:
    """Codigo de intervalo de Bybit: '1h' -> '60', '1d' -> 'D'."""
    try:
        return _TIMEFRAME_TO_BYBIT[timeframe]
    except KeyError as exc:
        msg = f"timeframe {timeframe!r} no soportado por Bybit en este sistema."
        raise TimeframeTranslationError(msg) from exc


def to_topic(canonical: str, timeframe: str) -> str:
    """Topic de suscripcion de VELAS: 'BTC-USDT' + '1h' -> 'kline.60.BTCUSDT'."""
    return f"kline.{to_interval(timeframe)}.{to_native(canonical)}"


def to_trade_topic(canonical: str) -> str:
    """Topic de suscripcion de TRADES: 'BTC-USDT' -> 'publicTrade.BTCUSDT'.

    SIN intervalo, y no es una omision: el flujo de trades es continuo y NO se bucketea
    a nivel de stream (ADR-014). El bucketeo por barra es del footprint, que es dato
    DERIVADO. El native (BTCUSDT) es el mismo que en velas: Bybit pega el simbolo y la
    vuelta a canonico se CONSULTA al catalogo (set_symbol_map), no se calcula.
    """
    return f"publicTrade.{to_native(canonical)}"


def timeframe_from_interval(interval: str) -> str:
    """Inverso de to_interval: '60' -> '1h'. Reconoce el mensaje entrante.

    ESTRICTO: un codigo desconocido se RECHAZA. Un parser permisivo aceptaria un dia
    un intervalo que no pedimos.
    """
    try:
        return _BYBIT_TO_TIMEFRAME[interval]
    except KeyError as exc:
        msg = f"intervalo de Bybit {interval!r} no soportado por el sistema."
        raise TimeframeTranslationError(msg) from exc
