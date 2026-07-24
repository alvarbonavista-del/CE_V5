"""Traduccion de los mensajes de Binance a los DTO crudos (RawCandle, RawTrade). SIN IO.

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

from source.families.market import (
    RawCandle,
    RawOrderbookDelta,
    RawOrderbookLevel,
    RawOrderbookSeed,
    RawTrade,
    Timeframe,
)

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


def raw_trade_from_binance(
    msg: dict[str, Any], canonical_symbol: str, market_type: str
) -> RawTrade:
    """Un mensaje @trade de Binance -> RawTrade (dato CRUDO, sin validar).

    canonical_symbol lo resuelve el LLAMADOR consultando el catalogo, igual que en las
    velas: de 'BTCUSDT' no se puede deducir donde parte.

    EL LADO AGRESOR NO SE ESTIMA: SE LEE. Binance publica 'm' = "is the buyer the
    market maker?". Si m es FALSE, el maker fue el vendedor y por tanto el COMPRADOR
    cruzo el spread: agresor 'buy'. Si m es TRUE, el comprador estaba en el libro y
    quien cruzo fue el VENDEDOR: agresor 'sell'. Es un HECHO que publica el exchange,
    y de ahi que el footprint salga reproducible bit a bit. La regla de tick (deducir
    el lado comparando con el precio anterior) queda SOLO como fallback degradado
    documentado para un exchange que no publicase el flag; aqui SIEMPRE viene.
    """
    trade_id = _requerido(msg, "t", "el mensaje")
    return RawTrade(
        exchange="binance",
        market_type=market_type,
        symbol=canonical_symbol,
        trade_id=str(trade_id),
        # TEXTO TAL CUAL: ni float, ni redondeo, ni limpieza. En M5 esto es dinero.
        price=str(_requerido(msg, "p", "el mensaje")),
        qty=str(_requerido(msg, "q", "el mensaje")),
        aggressor_side="sell" if bool(_requerido(msg, "m", "el mensaje")) else "buy",
        # 'T' es el instante del PROPIO TRADE en el exchange (ADR-007: el event_time lo
        # fija el ORIGEN del hecho, jamas nuestro reloj). NO se usa 'E', que es cuando
        # el exchange EMITIO el mensaje: parecido, pero no es el mismo hecho.
        event_time_ms=int(_requerido(msg, "T", "el mensaje")),
        # El trade id de Binance es monotono por simbolo: sirve de secuencia de origen.
        source_sequence=int(trade_id),
    )


def _entero(valor: object, campo: str, contexto: str) -> int:
    """Un campo de SECUENCIA a entero, o error de traduccion (no un libro a medias).

    Las secuencias de Binance (lastUpdateId, U, u) son enteros por contrato; si llega
    algo que no lo es, el mensaje esta malformado y se rechaza aqui, no se construye un
    libro con una secuencia inventada.
    """
    try:
        return int(str(valor))
    except (TypeError, ValueError) as exc:
        msg = f"campo {campo!r} de {contexto} no es un entero: {valor!r}."
        raise BinanceTranslationError(msg) from exc


def _niveles(arr: object, lado: str, contexto: str) -> tuple[RawOrderbookLevel, ...]:
    """Un array de niveles [precio, cantidad] -> tupla de (precio, cantidad) EN TEXTO.

    SOLO traduce forma: copia precio y cantidad TAL CUAL, como texto (nunca float). Una
    cantidad 0 se conserva -- el motor la interpreta como BORRAR el nivel --; que sea un
    numero valido lo decide la frontera de confianza, no este traductor. Un nivel que no
    es [precio, cantidad] es un mensaje malformado: se rechaza, no se traduce a medias.
    """
    if not isinstance(arr, (list, tuple)):
        msg = f"{lado} de {contexto} no es un array: {type(arr)!r}."
        raise BinanceTranslationError(msg)
    niveles: list[RawOrderbookLevel] = []
    for nivel in arr:
        if not isinstance(nivel, (list, tuple)) or len(nivel) < 2:
            msg = (
                f"nivel de {lado} malformado en {contexto}: {nivel!r} "
                "(se espera [precio, cantidad])."
            )
            raise BinanceTranslationError(msg)
        niveles.append((str(nivel[0]), str(nivel[1])))
    return tuple(niveles)


def raw_orderbook_seed_from_binance(
    msg: dict[str, Any], canonical_symbol: str, market_type: str
) -> RawOrderbookSeed:
    """La foto REST /api/v3/depth de Binance -> RawOrderbookSeed (CRUDO, sin validar).

    Forma: {"lastUpdateId": <int>, "bids": [[precio, cantidad], ...], "asks": [...]}.
    lastUpdateId es la SECUENCIA BASE contra la que el motor encadena los deltas (su U
    del primer delta debe ser lastUpdateId+1). canonical_symbol lo resuelve el LLAMADOR
    desde el catalogo: de 'BTCUSDT' no se puede deducir donde parte.
    """
    return RawOrderbookSeed(
        exchange="binance",
        market_type=market_type,
        symbol=canonical_symbol,
        bids=_niveles(_requerido(msg, "bids", "el snapshot"), "bids", "el snapshot"),
        asks=_niveles(_requerido(msg, "asks", "el snapshot"), "asks", "el snapshot"),
        base_sequence=_entero(
            _requerido(msg, "lastUpdateId", "el snapshot"),
            "lastUpdateId",
            "el snapshot",
        ),
    )


def raw_orderbook_delta_from_binance(
    msg: dict[str, Any], canonical_symbol: str, market_type: str
) -> RawOrderbookDelta:
    """Un depthUpdate WS de Binance -> RawOrderbookDelta (CRUDO, sin validar).

    Forma: {"e":"depthUpdate","E":..,"s":..,"U":<first_update_id>,"u":<final_update_id>,
    "b":[[precio,cantidad],..],"a":[..]}. U y u son las secuencias SIN INTERPRETAR que
    el motor usa para la continuidad (U == u_previo+1) y para descartar deltas viejos (u
    <= lastUpdateId de la semilla). Una cantidad 0 = borrar el nivel (lo aplica el
    motor). canonical_symbol lo resuelve el LLAMADOR desde el catalogo.
    """
    return RawOrderbookDelta(
        exchange="binance",
        market_type=market_type,
        symbol=canonical_symbol,
        bids=_niveles(_requerido(msg, "b", "el delta"), "bids", "el delta"),
        asks=_niveles(_requerido(msg, "a", "el delta"), "asks", "el delta"),
        first_update_id=_entero(_requerido(msg, "U", "el delta"), "U", "el delta"),
        final_update_id=_entero(_requerido(msg, "u", "el delta"), "u", "el delta"),
    )
