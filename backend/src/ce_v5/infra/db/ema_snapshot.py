"""Store del estado de replay de ema.value: snapshot de VALOR por barra (0023).

El motor de reglas (rol ce_v5_rules) ESCRIBE y LEE aqui su propio estado de
materializacion del RECURSIVE ema: el valor del EMA en una barra vigente, para SEMBRAR
el replay de la ventana siguiente sin recomputar desde el origen (EMA[T] depende de
EMA[T-1], asi que sin ancla cada tick seria O(historia)). Append-only: un snapshot por
(flujo, period, open_time); reevaluar la misma barra reproduce el MISMO valor
(determinista, contexto Decimal pinneado en indicators/ema.py) y ON CONFLICT DO NOTHING
lo absorbe. NO es dato de mercado (scope=system, 7.8): es estado propio del motor, por
eso el rol de reglas SI escribe aqui (5.20, 0023).

GEMELO de cvd_snapshot.py, con period donde aquel lleva reset_policy: period ENTRA EN LA
IDENTIDAD porque ema(9) y ema(21) son SERIES DISTINTAS (viaja en la cache_key de
ema.value), asi que el ancla de un EMA de 9 nunca siembra el de 21 ni al reves.

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
INSERT INTO ema_snapshot (
    exchange, market_type, symbol, timeframe, period, open_time, value
) VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (exchange, market_type, symbol, timeframe, period, open_time)
DO NOTHING
"""

# El ancla del replay: el snapshot vigente MAS RECIENTE ANTERIOR a la ventana pedida
# (open_time < el inicio de ventana). Sembrar de el y plegar los cierres posteriores
# reproduce la cola exacta de la serie (ADR-007), acotando el replay.
_LATEST_BEFORE_SQL = """
SELECT open_time, value
FROM ema_snapshot
WHERE exchange = %s AND market_type = %s AND symbol = %s
  AND timeframe = %s AND period = %s AND open_time < %s
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


def write_ema_snapshot(
    session: Session,
    exchange: str,
    symbol: str,
    timeframe: str,
    period: int,
    open_time: int,
    value: Decimal,
) -> None:
    """Persiste el snapshot de ema de una barra vigente (idempotente, append-only).

    ON CONFLICT DO NOTHING: reevaluar la misma barra recomputa el MISMO valor
    determinista, asi que reinsertar no duplica ni falla. No muta un snapshot previo
    (append-only): una correccion se resolveria con una barra/snapshot nuevos.
    """
    session.execute(
        _INSERT_SNAPSHOT_SQL,
        (
            exchange,
            MarketType.SPOT.value,
            symbol,
            timeframe,
            period,
            open_time,
            value,
        ),
    )


def read_ema_snapshot_before(
    session: Session,
    exchange: str,
    symbol: str,
    timeframe: str,
    period: int,
    before_open_time: int,
) -> tuple[int, Decimal] | None:
    """El snapshot vigente mas reciente con open_time < before_open_time, o None.

    Es el ANCLA del replay: se siembra ema_from_anchor con su valor y se pliegan los
    cierres de las barras posteriores hasta la barra pedida. El limite es ESTRICTO ("<",
    misma convencion que cvd_snapshot, dictamen Q5) porque el snapshot de la primera
    barra de la ventana ya incorpora el cierre de esa barra: usarlo como ancla la
    contaria dos veces. None -> no hay ancla: el materializador arranca el BOOTSTRAP
    desde el ORIGEN, sembrando en el primer cierre de la serie (EMA[0] == close[0],
    invariante P08b-08); el EMA NO es anchor-independiente, asi que el bootstrap no
    puede recortarse a la ventana.
    """
    row = session.fetchone(
        _LATEST_BEFORE_SQL,
        (
            exchange,
            MarketType.SPOT.value,
            symbol,
            timeframe,
            period,
            before_open_time,
        ),
    )
    if row is None:
        return None
    return (_entero(row[0]), _decimal(row[1]))
