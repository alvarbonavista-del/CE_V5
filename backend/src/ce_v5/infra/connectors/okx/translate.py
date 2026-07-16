"""Traduccion de un array de vela de OKX a RawCandle. SIN IO.

Como el translate.py de Binance: SOLO traduce formato. No valida rango, ni coherencia,
ni dominio: de eso se encarga la unica frontera de confianza
(platform/market/normalize.py), igual para los tres exchanges. Los precios se copian
TAL CUAL, como TEXTO (nunca float).

Un array malformado (menos campos de los que la doc de OKX promete, o un timeframe que
no pedimos) lanza OkxTranslationError. NUNCA se devuelve un RawCandle a medias.

FORMA DEL ARRAY (canal 'candle', OKX v5, verificado contra la doc vigente):
  [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
  ts      : hora de APERTURA de la vela (ms). OKX no da cierre ni push-time.
  confirm : '1' vela cerrada, '0' en curso.

DECISIONES DE NORMALIZACION PROPIAS DE OKX (OKX no da lo que Binance si da):
- close_time_ms se DERIVA: open_time + intervalo - 1 (misma convencion que Binance T).
- event_time_ms = ts (apertura): unico timestamp que OKX pone en el mensaje de vela.
  ADR-007 exige que el event_time lo fije el ORIGEN; el reloj propio esta prohibido.
- source_sequence = None: el canal de velas de OKX no trae id de ultimo trade.
"""

from __future__ import annotations

from collections.abc import Sequence

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

# Campos minimos del array de vela de OKX v5 (canal 'candle'):
# [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm].
_MIN_FIELDS = 9
_IDX_CONFIRM = 8


class OkxTranslationError(ValueError):
    """El array de vela de OKX no tiene la forma que su documentacion promete."""


def supported_okx_timeframes() -> frozenset[Timeframe]:
    """Los timeframes que ESTE sistema usa de OKX."""
    return _SUPPORTED


def raw_candle_from_okx(
    row: Sequence[object],
    canonical_symbol: str,
    market_type: str,
    timeframe: str,
) -> RawCandle:
    """Un array de vela de OKX -> RawCandle (dato CRUDO, sin validar).

    canonical_symbol y timeframe los resuelve el LLAMADOR desde el 'arg' del mensaje
    (instId y channel): en OKX el instId ya es canonico y el timeframe sale del canal.
    """
    if not isinstance(row, (list, tuple)):
        msg = f"vela de OKX no es un array: {type(row)!r}."
        raise OkxTranslationError(msg)
    if len(row) < _MIN_FIELDS:
        msg = (
            f"array de vela de OKX con {len(row)} campos; se esperan al menos "
            f"{_MIN_FIELDS} [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]."
        )
        raise OkxTranslationError(msg)

    try:
        tf = Timeframe(timeframe)
    except ValueError as exc:
        msg = f"timeframe {timeframe!r} no soportado por el sistema."
        raise OkxTranslationError(msg) from exc
    if tf not in _SUPPORTED:
        msg = f"timeframe {timeframe!r} no declarado como soportado para OKX."
        raise OkxTranslationError(msg)

    open_time_ms = int(str(row[0]))
    confirm = str(row[_IDX_CONFIRM])

    return RawCandle(
        exchange="okx",
        market_type=market_type,
        symbol=canonical_symbol,
        timeframe=tf.value,
        open_time_ms=open_time_ms,
        # OKX no da hora de cierre: se deriva (misma convencion que Binance 'T').
        close_time_ms=open_time_ms + tf.duration_ms - 1,
        # TEXTO TAL CUAL: ni float, ni redondeo, ni limpieza.
        open=str(row[1]),
        high=str(row[2]),
        low=str(row[3]),
        close=str(row[4]),
        volume=str(row[5]),
        # confirm lo dice el EXCHANGE: '1' cerrada, '0' en formacion.
        is_closed=(confirm == "1"),
        # OKX no manda push-time: el unico timestamp de origen es ts (apertura).
        event_time_ms=open_time_ms,
        # OKX no trae id de ultimo trade en el canal de velas.
        source_sequence=None,
    )
