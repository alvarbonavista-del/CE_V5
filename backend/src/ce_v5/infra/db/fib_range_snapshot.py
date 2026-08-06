"""Store del RANGO vigente del grid Fibonacci con histeresis, por barra (0027).

El motor de reglas (rol ce_v5_rules) ESCRIBE y LEE aqui su propio estado de
materializacion del RECURSIVE fib.*: el rango [range_low, range_high] sobre el que se
tienden los niveles, para SEMBRAR el replay de la ventana siguiente sin recomputar desde
el origen. Append-only: un snapshot por (flujo, strength, open_time); reevaluar la misma
barra reproduce el MISMO rango (determinista, contexto Decimal pinneado en
indicators/fib.py) y ON CONFLICT DO NOTHING lo absorbe. NO es dato de mercado
(scope=system, 7.8): es estado propio del motor, por eso el rol de reglas SI escribe
aqui (5.20, 0027).

EL RANGO ES EL ESTADO, Y ES MEMORIA ILIMITADA. No es el swing de la barra: es un rango
PEGAJOSO que solo se mueve cuando el swing lo supera por al menos 0.414 veces su tamano
(histeresis L2, paridad v4). Una barra que no mueve el rango lo CONSERVA, asi que el
rango de hoy puede venir de un pivote de hace cientos de barras. Por eso hay snapshot:
no se puede acotar el bootstrap a una ventana.

Se guarda el RANGO, no los niveles: los 17 niveles del grid, el mas cercano y el
porcentaje son DERIVADOS de (range_high, range_low) mas el cierre de la barra.

strength ENTRA EN LA IDENTIDAD porque el rango se decanta de swing.high/swing.low, que
son parametricas en la fuerza simetrica del pivote: rango(7) != rango(2). Los ratios
Fibonacci -- el 0.414 incluido -- NO son parametros: son constantes definitorias del
indicador (dictamen P08b-13 D6) y no entran aqui.

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
INSERT INTO fib_range_snapshot (
    exchange, market_type, symbol, timeframe, strength, open_time,
    range_high, range_low
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (exchange, market_type, symbol, timeframe, strength, open_time)
DO NOTHING
"""

# El ancla del replay: el snapshot vigente MAS RECIENTE ANTERIOR a la ventana pedida
# (open_time < el inicio de ventana). Sembrar de el y aplicar la histeresis con los
# swing de las barras posteriores reproduce la cola exacta de la serie (ADR-007),
# acotando el replay.
_LATEST_BEFORE_SQL = """
SELECT open_time, range_high, range_low
FROM fib_range_snapshot
WHERE exchange = %s AND market_type = %s AND symbol = %s
  AND timeframe = %s AND strength = %s AND open_time < %s
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


def write_fib_range_snapshot(
    session: Session,
    exchange: str,
    symbol: str,
    timeframe: str,
    strength: int,
    open_time: int,
    range_high: Decimal,
    range_low: Decimal,
) -> None:
    """Persiste el rango vigente de una barra (idempotente, append-only).

    ON CONFLICT DO NOTHING: reevaluar la misma barra recomputa el MISMO rango
    determinista, asi que reinsertar no duplica ni falla. Es lo que hace inofensivo que
    las DOS fuentes (fib.nearest_level y fib.level_pct) materialicen la misma barra por
    separado: escriben la misma fila. No muta un snapshot previo (append-only): una
    correccion se resolveria con una barra/snapshot nuevos.

    Los DOS valores viajan juntos porque son UN solo estado: un extremo sin el otro no
    define rango y el replay no podria continuar.
    """
    session.execute(
        _INSERT_SNAPSHOT_SQL,
        (
            exchange,
            MarketType.SPOT.value,
            symbol,
            timeframe,
            strength,
            open_time,
            range_high,
            range_low,
        ),
    )


def read_fib_range_snapshot_before(
    session: Session,
    exchange: str,
    symbol: str,
    timeframe: str,
    strength: int,
    before_open_time: int,
) -> tuple[int, Decimal, Decimal] | None:
    """El rango vigente mas reciente con open_time < before_open_time, o None.

    Devuelve (open_time, range_high, range_low). Es el ANCLA del replay: se siembra la
    histeresis con ese rango y se aplican los swing de las barras posteriores hasta la
    barra pedida. El limite es ESTRICTO ("<", misma convencion que cvd_snapshot,
    ema_snapshot, rsi_snapshot y macd_snapshot) porque el snapshot de la primera barra
    de la ventana ya incorpora el swing de esa barra: usarlo como ancla la contaria dos
    veces.

    None -> no hay ancla: el materializador arranca el BOOTSTRAP desde el ORIGEN,
    sembrando el rango en el primer par (swing_high, swing_low) que exista. El rango es
    pegajoso y por tanto anchor-dependiente de toda la historia: el bootstrap no puede
    recortarse a la ventana.
    """
    row = session.fetchone(
        _LATEST_BEFORE_SQL,
        (
            exchange,
            MarketType.SPOT.value,
            symbol,
            timeframe,
            strength,
            before_open_time,
        ),
    )
    if row is None:
        return None
    return (_entero(row[0]), _decimal(row[1]), _decimal(row[2]))
