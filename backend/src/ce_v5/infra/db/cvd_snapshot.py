"""Store del estado de replay de cvd.value: snapshot de VALOR por barra (MAT-07).

El motor de reglas (rol ce_v5_rules) ESCRIBE y LEE aqui su propio estado de
materializacion del INTEGRATOR cvd: el valor acumulado en una barra vigente, para
SEMBRAR el replay de la ventana siguiente sin re-acumular desde el origen (rolling no
resetea, seria ilimitado). Append-only: un snapshot por (flujo, reset_policy,
open_time); reevaluar la misma barra reproduce el MISMO valor (determinista) y
ON CONFLICT DO NOTHING lo absorbe. NO es dato de mercado (scope=system, 7.8): es
estado propio del motor, por eso el rol de reglas SI escribe aqui (5.20, T5b-2a).

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
INSERT INTO cvd_snapshot (
    exchange, market_type, symbol, timeframe, reset_policy, open_time, value
) VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (exchange, market_type, symbol, timeframe, reset_policy, open_time)
DO NOTHING
"""

# El ancla del replay: el snapshot vigente MAS RECIENTE ANTERIOR a la ventana pedida
# (open_time < el inicio de ventana). Sembrar de el y acumular los deltas posteriores
# reproduce la cola exacta de la serie (ADR-007), acotando el replay.
_LATEST_BEFORE_SQL = """
SELECT open_time, value
FROM cvd_snapshot
WHERE exchange = %s AND market_type = %s AND symbol = %s
  AND timeframe = %s AND reset_policy = %s AND open_time < %s
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


def write_cvd_snapshot(
    session: Session,
    exchange: str,
    symbol: str,
    timeframe: str,
    reset_policy: str,
    open_time: int,
    value: Decimal,
) -> None:
    """Persiste el snapshot de cvd de una barra vigente (idempotente, append-only).

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
            reset_policy,
            open_time,
            value,
        ),
    )


def read_cvd_snapshot_before(
    session: Session,
    exchange: str,
    symbol: str,
    timeframe: str,
    reset_policy: str,
    before_open_time: int,
) -> tuple[int, Decimal] | None:
    """El snapshot vigente mas reciente con open_time < before_open_time, o None.

    Es el ANCLA del replay: se siembra materialize_recursive con su valor y se acumulan
    los deltas de las barras posteriores hasta la barra pedida. None -> no hay ancla:
    el materializador arranca el acumulado en el inicio de la ventana (bootstrap; el
    valor absoluto del rolling es anchor-dependiente, la divergencia no, cvd.py).
    """
    row = session.fetchone(
        _LATEST_BEFORE_SQL,
        (
            exchange,
            MarketType.SPOT.value,
            symbol,
            timeframe,
            reset_policy,
            before_open_time,
        ),
    )
    if row is None:
        return None
    return (_entero(row[0]), _decimal(row[1]))
