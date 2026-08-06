"""Store del estado de replay de macd.*: snapshot de ESTADO por barra (0026).

El motor de reglas (rol ce_v5_rules) ESCRIBE y LEE aqui su propio estado de
materializacion del RECURSIVE macd: las TRES EMAs internas, para SEMBRAR el replay de la
ventana siguiente sin recomputar desde el origen. Append-only: un snapshot por (flujo,
fast, slow, signal, open_time); reevaluar la misma barra reproduce el MISMO estado
(determinista, contexto Decimal pinneado en indicators/macd.py) y ON CONFLICT DO NOTHING
lo absorbe. NO es dato de mercado (scope=system, 7.8): es estado propio del motor, por
eso el rol de reglas SI escribe aqui (5.20, 0026).

EL ESTADO SON LAS EMAs, NO LAS SALIDAS. line = ema_fast - ema_slow, signal = ema_signal
e histogram = line - signal son DERIVADOS: guardarlos dejaria que la fila se
contradijera a si misma, y ademas no bastarian para continuar (de line y signal no se
recuperan ema_fast y ema_slow por separado). Las TRES fuentes publicas comparten este
UNICO estado.

SIN last_close, a diferencia de rsi_snapshot (0025): las EMAs del MACD se alimentan del
cierre DIRECTO, no de un diferencial entre cierres consecutivos, asi que el estado no
necesita recordar la barra anterior.

Los TRES params ENTRAN EN LA IDENTIDAD porque macd(12,26,9) y macd(5,35,5) son SERIES
DISTINTAS (viajan en la cache_key de macd.*), asi que el ancla de una parametrizacion
nunca siembra otra.

market_type esta FIJADO a spot (v5.0 solo tiene spot), igual que en los lectores de
mercado; cuando entren derivados, el parametro entra con su uso real.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from source.families.market import MarketType

if TYPE_CHECKING:
    from ce_v5.infra.db.ports import Session

_INSERT_SNAPSHOT_SQL = """
INSERT INTO macd_snapshot (
    exchange, market_type, symbol, timeframe, fast, slow, signal, open_time,
    ema_fast, ema_slow, ema_signal
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (exchange, market_type, symbol, timeframe, fast, slow, signal, open_time)
DO NOTHING
"""

# El ancla del replay: el snapshot vigente MAS RECIENTE ANTERIOR a la ventana pedida
# (open_time < el inicio de ventana). Sembrar de el y dar un macd_step por cada cierre
# posterior reproduce la cola exacta de las TRES series (ADR-007), acotando el replay.
_LATEST_BEFORE_SQL = """
SELECT open_time, ema_fast, ema_slow, ema_signal
FROM macd_snapshot
WHERE exchange = %s AND market_type = %s AND symbol = %s
  AND timeframe = %s AND fast = %s AND slow = %s AND signal = %s
  AND open_time < %s
ORDER BY open_time DESC
LIMIT 1
"""


def _entero(valor: object) -> int:
    if not isinstance(valor, int):
        msg = f"Se esperaba un entero de la base y llego {type(valor)!r}."
        raise TypeError(msg)
    return valor


def _decimal(valor: object) -> Decimal:
    if not isinstance(valor, Decimal):
        msg = f"Se esperaba un Decimal de la base y llego {type(valor)!r}."
        raise TypeError(msg)
    return valor


def write_macd_snapshot(
    session: Session,
    exchange: str,
    symbol: str,
    timeframe: str,
    fast: int,
    slow: int,
    signal: int,
    open_time: int,
    ema_fast: Decimal,
    ema_slow: Decimal,
    ema_signal: Decimal,
) -> None:
    """Persiste el estado de macd de una barra vigente (idempotente, append-only).

    ON CONFLICT DO NOTHING: reevaluar la misma barra recomputa el MISMO estado
    determinista, asi que reinsertar no duplica ni falla. Es lo que hace inofensivo que
    las TRES fuentes (line, signal, histogram) materialicen la misma barra por separado:
    escriben la misma fila. No muta un snapshot previo (append-only): una correccion se
    resolveria con una barra/snapshot nuevos.

    Las TRES EMAs viajan juntas porque son UN solo estado: una sin las otras dejaria un
    ancla desde la que el replay no puede continuar.
    """
    session.execute(
        _INSERT_SNAPSHOT_SQL,
        (
            exchange,
            MarketType.SPOT.value,
            symbol,
            timeframe,
            fast,
            slow,
            signal,
            open_time,
            ema_fast,
            ema_slow,
            ema_signal,
        ),
    )


def read_macd_snapshot_before(
    session: Session,
    exchange: str,
    symbol: str,
    timeframe: str,
    fast: int,
    slow: int,
    signal: int,
    before_open_time: int,
) -> tuple[int, Decimal, Decimal, Decimal] | None:
    """El estado vigente mas reciente con open_time < before_open_time, o None.

    Devuelve (open_time, ema_fast, ema_slow, ema_signal). Es el ANCLA del replay: se
    siembra macd_step con esas tres EMAs y se avanza con los cierres de las barras
    posteriores hasta la barra pedida. El limite es ESTRICTO ("<", misma convencion que
    cvd_snapshot, ema_snapshot y rsi_snapshot) porque el snapshot de la primera barra de
    la ventana ya incorpora el cierre de esa barra: usarlo como ancla la contaria dos
    veces.

    None -> no hay ancla: el materializador arranca el BOOTSTRAP desde el ORIGEN,
    sembrando con macd_seed en el primer cierre (EMA[0] == src[0], invariante P08b-08,
    de donde sale macd[0] == 0). El MACD NO es anchor-independiente, asi que el
    bootstrap no puede recortarse a la ventana.
    """
    row = session.fetchone(
        _LATEST_BEFORE_SQL,
        (
            exchange,
            MarketType.SPOT.value,
            symbol,
            timeframe,
            fast,
            slow,
            signal,
            before_open_time,
        ),
    )
    if row is None:
        return None
    return (
        _entero(row[0]),
        _decimal(row[1]),
        _decimal(row[2]),
        _decimal(row[3]),
    )
