"""Store del estado de replay de pivotphase: snapshot de ESTADO por barra (P08c P5 T1).

El motor de reglas (rol ce_v5_rules) ESCRIBE y LEE aqui su propio estado de
materializacion del RECURSIVE pivotphase: el estado de la FSM (serializado) y la
confianza en una barra vigente, para SEMBRAR el replay de la ventana siguiente sin
re-evaluar desde el origen. Append-only: un snapshot por (flujo, params_version,
open_time); reevaluar la misma barra reproduce el MISMO estado (determinista) y
ON CONFLICT DO NOTHING lo absorbe. NO es dato de mercado (scope=system, 7.8): es
estado propio del motor, por eso el rol de reglas SI escribe aqui (5.20, como
cvd_snapshot).

market_type esta FIJADO a spot (v5.0 solo tiene spot), igual que en cvd_snapshot.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from source.families.market import MarketType

if TYPE_CHECKING:
    from ce_v5.infra.db.ports import Session

_INSERT_SNAPSHOT_SQL = """
INSERT INTO pivotphase_snapshot (
    exchange, market_type, symbol, timeframe, params_version, open_time, state,
    confidence
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (exchange, market_type, symbol, timeframe, params_version, open_time)
DO NOTHING
"""

# El ancla del replay: el snapshot vigente MAS RECIENTE hasta la barra pedida
# (open_time <= la barra pedida). Sembrar de el y reevaluar las barras posteriores
# reproduce la cola exacta de la secuencia (ADR-007), acotando el replay.
_LATEST_BEFORE_SQL = """
SELECT open_time, state, confidence
FROM pivotphase_snapshot
WHERE exchange = %s AND market_type = %s AND symbol = %s
  AND timeframe = %s AND params_version = %s AND open_time <= %s
ORDER BY open_time DESC
LIMIT 1
"""


def _entero(valor: object) -> int:
    if not isinstance(valor, int):
        msg = f"Se esperaba un entero de la base y llego {type(valor)!r}."
        raise TypeError(msg)
    return valor


def _texto(valor: object) -> str:
    if not isinstance(valor, str):
        msg = f"Se esperaba un texto de la base y llego {type(valor)!r}."
        raise TypeError(msg)
    return valor


def _decimal_o_none(valor: object) -> Decimal | None:
    if valor is None:
        return None
    if not isinstance(valor, Decimal):
        msg = f"Se esperaba un Decimal o None de la base y llego {type(valor)!r}."
        raise TypeError(msg)
    return valor


def write_pivotphase_snapshot(
    session: Session,
    exchange: str,
    symbol: str,
    timeframe: str,
    params_version: str,
    open_time: int,
    state: str,
    confidence: Decimal | None,
) -> None:
    """Persiste el snapshot de pivotphase de una barra vigente (idempotente,
    append-only).

    ON CONFLICT DO NOTHING: reevaluar la misma barra recomputa el MISMO estado
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
            params_version,
            open_time,
            state,
            confidence,
        ),
    )


def read_pivotphase_snapshot_before(
    session: Session,
    exchange: str,
    symbol: str,
    timeframe: str,
    params_version: str,
    open_time: int,
) -> tuple[int, str, Decimal | None] | None:
    """El snapshot vigente mas reciente con open_time <= open_time pedido, o None.

    Es el ANCLA del replay: se siembra evaluate_bar con su estado y se reevaluan las
    barras posteriores hasta la barra pedida. None -> no hay ancla: el materializador
    arranca desde PivotState() (IDLE) en el inicio de la ventana (bootstrap).
    """
    row = session.fetchone(
        _LATEST_BEFORE_SQL,
        (
            exchange,
            MarketType.SPOT.value,
            symbol,
            timeframe,
            params_version,
            open_time,
        ),
    )
    if row is None:
        return None
    return (_entero(row[0]), _texto(row[1]), _decimal_o_none(row[2]))
