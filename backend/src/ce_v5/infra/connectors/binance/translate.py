"""Traduccion de un mensaje kline de Binance a RawCandle. SIN IO.

ESTE MODULO SOLO TRADUCE FORMATO. No valida rango, ni coherencia, ni nada de dominio:
de eso se encarga la FRONTERA DE CONFIANZA (platform/market/normalize.py), que es una
sola para los tres exchanges. Si cada conector validara lo suyo, tendriamos tres
validaciones distintas y una seria la mas floja; el atacante elegiria esa.

Los precios se copian TAL CUAL, como TEXTO. No se convierten a float (un float binario
no representa 0.1 exacto, y en M5 esto es dinero) y no se "limpian".

Un mensaje malformado (le falta una clave, o trae un intervalo que no pedimos) lanza
BinanceTranslationError. NUNCA se devuelve un RawCandle a medias: el lector convierte
la excepcion en una metrica observable, no en un dato.
"""

from __future__ import annotations

from typing import Any

from source.families.market import RawCandle, Timeframe

# Los intervalos de Binance coinciden en texto con los canonicos, pero eso es una
# COINCIDENCIA, no un contrato: se valida contra el vocabulario cerrado. Binance sirve
# mas intervalos (3m, 2h, 1w...); solo declaramos los que el sistema usa.
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


class BinanceTranslationError(ValueError):
    """El mensaje de Binance no tiene la forma que su documentacion promete."""


def supported_binance_timeframes() -> frozenset[Timeframe]:
    """Los timeframes de Binance que ESTE sistema usa."""
    return _SUPPORTED


def _requerido(origen: dict[str, Any], clave: str, contexto: str) -> Any:  # noqa: ANN401
    if clave not in origen:
        msg = (
            f"mensaje de Binance sin la clave {clave!r} en {contexto}: no se traduce "
            "a medias."
        )
        raise BinanceTranslationError(msg)
    return origen[clave]


def raw_candle_from_binance(
    msg: dict[str, Any], canonical_symbol: str, market_type: str
) -> RawCandle:
    """Un mensaje kline de Binance -> RawCandle (dato CRUDO, sin validar).

    canonical_symbol lo resuelve el LLAMADOR consultando el catalogo (native_symbol):
    aqui no se adivina, porque de 'BTCUSDT' no se puede deducir donde parte.
    """
    kline = _requerido(msg, "k", "el mensaje")
    if not isinstance(kline, dict):
        msg_error = f"el campo 'k' no es un objeto: {type(kline)!r}."
        raise BinanceTranslationError(msg_error)

    intervalo = str(_requerido(kline, "i", "k"))
    try:
        timeframe = Timeframe(intervalo)
    except ValueError as exc:
        # No es basura: es un intervalo REAL de Binance que nosotros no pedimos. Aun
        # asi se descarta, porque abrir la puerta a lo que nadie pidio es abrirla.
        msg_error = f"intervalo {intervalo!r} no soportado por el sistema."
        raise BinanceTranslationError(msg_error) from exc
    if timeframe not in _SUPPORTED:
        msg_error = f"intervalo {intervalo!r} no declarado como soportado."
        raise BinanceTranslationError(msg_error)

    return RawCandle(
        exchange="binance",
        market_type=market_type,
        symbol=canonical_symbol,
        timeframe=timeframe.value,
        open_time_ms=int(_requerido(kline, "t", "k")),
        close_time_ms=int(_requerido(kline, "T", "k")),
        # TEXTO TAL CUAL: ni float, ni redondeo, ni limpieza.
        open=str(_requerido(kline, "o", "k")),
        high=str(_requerido(kline, "h", "k")),
        low=str(_requerido(kline, "l", "k")),
        close=str(_requerido(kline, "c", "k")),
        volume=str(_requerido(kline, "v", "k")),
        # 'x' lo dice el EXCHANGE: si la vela esta cerrada o aun se esta formando.
        is_closed=bool(_requerido(kline, "x", "k")),
        # 'E' es el event_time del EXCHANGE (ADR-007: lo fija el ORIGEN del hecho,
        # jamas nuestro reloj).
        event_time_ms=int(_requerido(msg, "E", "el mensaje")),
        # 'L' (last trade id) es monotono: sirve de secuencia de origen.
        source_sequence=(
            int(kline["L"]) if isinstance(kline.get("L"), int | str) else None
        ),
    )
