"""Traduccion de una vela de Bybit v5 a RawCandle. SIN IO.

Como los otros connectors: SOLO traduce formato; la validacion de dominio la hace la
frontera de confianza (platform/market/normalize.py). Precios como TEXTO, nunca float.

Bybit da DOS formas de vela, y por eso hay DOS traducciones:
- WS (canal kline): OBJETO con campos nombrados, mas rico que OKX/Binance: trae start
  (apertura), end (cierre), timestamp (event_time del push) y confirm (bool).
- REST (/v5/market/kline): array [start, open, high, low, close, volume, turnover], sin
  confirm ni end ni timestamp; son velas historicas (cerradas).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from source.families.market import RawCandle, Timeframe

_SUPPORTED: frozenset[Timeframe] = frozenset(
    {
        Timeframe.M1,
        Timeframe.M5,
        Timeframe.M15,
        Timeframe.H1,
        Timeframe.H4,
        Timeframe.D1,
    }
)
# array REST de Bybit: [start, open, high, low, close, volume, turnover].
_REST_MIN_FIELDS = 7


class BybitTranslationError(ValueError):
    """La vela de Bybit no tiene la forma que su documentacion promete."""


def supported_bybit_timeframes() -> frozenset[Timeframe]:
    """Los timeframes que ESTE sistema usa de Bybit."""
    return _SUPPORTED


def _timeframe(timeframe: str) -> Timeframe:
    try:
        tf = Timeframe(timeframe)
    except ValueError as exc:
        msg = f"timeframe {timeframe!r} no soportado por el sistema."
        raise BybitTranslationError(msg) from exc
    if tf not in _SUPPORTED:
        msg = f"timeframe {timeframe!r} no declarado como soportado para Bybit."
        raise BybitTranslationError(msg)
    return tf


def _requerido(origen: dict[str, Any], clave: str) -> Any:  # noqa: ANN401
    if clave not in origen:
        msg = f"vela WS de Bybit sin la clave {clave!r}: no se traduce a medias."
        raise BybitTranslationError(msg)
    return origen[clave]


def raw_candle_from_bybit_ws(
    obj: dict[str, Any], canonical_symbol: str, market_type: str, timeframe: str
) -> RawCandle:
    """Un objeto de vela WS de Bybit -> RawCandle (dato CRUDO, sin validar)."""
    if not isinstance(obj, dict):
        msg = f"vela WS de Bybit no es un objeto: {type(obj)!r}."
        raise BybitTranslationError(msg)
    tf = _timeframe(timeframe)
    return RawCandle(
        exchange="bybit",
        market_type=market_type,
        symbol=canonical_symbol,
        timeframe=tf.value,
        open_time_ms=int(_requerido(obj, "start")),
        close_time_ms=int(_requerido(obj, "end")),
        open=str(_requerido(obj, "open")),
        high=str(_requerido(obj, "high")),
        low=str(_requerido(obj, "low")),
        close=str(_requerido(obj, "close")),
        volume=str(_requerido(obj, "volume")),
        # confirm (bool) lo dice el EXCHANGE.
        is_closed=bool(_requerido(obj, "confirm")),
        # timestamp: instante del push que fija el ORIGEN (ADR-007). Bybit SI lo da.
        event_time_ms=int(_requerido(obj, "timestamp")),
        source_sequence=None,
    )


def raw_candle_from_bybit_rest(
    row: Sequence[object], canonical_symbol: str, market_type: str, timeframe: str
) -> RawCandle:
    """Una fila REST de vela de Bybit -> RawCandle. Solo velas historicas (cerradas)."""
    if not isinstance(row, (list, tuple)):
        msg = f"vela REST de Bybit no es un array: {type(row)!r}."
        raise BybitTranslationError(msg)
    if len(row) < _REST_MIN_FIELDS:
        msg = (
            f"array de vela REST de Bybit con {len(row)} campos; se esperan al menos "
            f"{_REST_MIN_FIELDS} [start,open,high,low,close,volume,turnover]."
        )
        raise BybitTranslationError(msg)
    tf = _timeframe(timeframe)
    open_time_ms = int(str(row[0]))
    close_time_ms = open_time_ms + tf.duration_ms - 1
    return RawCandle(
        exchange="bybit",
        market_type=market_type,
        symbol=canonical_symbol,
        timeframe=tf.value,
        open_time_ms=open_time_ms,
        # El REST no da hora de cierre: se deriva (open + intervalo - 1).
        close_time_ms=close_time_ms,
        open=str(row[1]),
        high=str(row[2]),
        low=str(row[3]),
        close=str(row[4]),
        volume=str(row[5]),
        # El REST solo devuelve velas historicas CERRADAS.
        is_closed=True,
        # El REST no trae timestamp de push: el instante del hecho es el cierre.
        event_time_ms=close_time_ms,
        source_sequence=None,
    )
