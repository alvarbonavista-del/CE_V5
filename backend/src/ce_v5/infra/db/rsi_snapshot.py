"""Store del estado de replay de rsi.value: snapshot de ESTADO por barra (0025).

El motor de reglas (rol ce_v5_rules) ESCRIBE y LEE aqui su propio estado de
materializacion del RECURSIVE rsi (Wilder/RMA): las dos medias suavizadas y el cierre de
la barra ancla, para SEMBRAR el replay de la ventana siguiente sin recomputar desde el
origen. Append-only: un snapshot por (flujo, period, open_time); reevaluar la misma
barra reproduce el MISMO estado (determinista, contexto Decimal pinneado en
indicators/rsi.py) y ON CONFLICT DO NOTHING lo absorbe. NO es dato de mercado
(scope=system, 7.8): es
estado propio del motor, por eso el rol de reglas SI escribe aqui (5.20, 0025).

GEMELO de ema_snapshot.py (0023) salvo en el TAMANO del estado: donde el EMA guarda UN
valor, Wilder necesita TRES -- avg_gain, avg_loss y last_close. La tercera no es
comodidad: gain[T] = close[T] - close[T-1] es un DIFERENCIAL, asi que sin el cierre de
la barra ancla el primer paso del replay no se puede calcular y el ancla no anclaria
nada. El RSI de la barra NO se guarda: es DERIVADO de (avg_gain, avg_loss), y
persistirlo dejaria que la fila se contradijera a si misma.

period ENTRA EN LA IDENTIDAD porque rsi(7) y rsi(14) son SERIES DISTINTAS (viaja en la
cache_key de rsi.value), asi que el ancla de un RSI de 7 nunca siembra el de 14.

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
INSERT INTO rsi_snapshot (
    exchange, market_type, symbol, timeframe, period, open_time,
    avg_gain, avg_loss, last_close
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (exchange, market_type, symbol, timeframe, period, open_time)
DO NOTHING
"""

# El ancla del replay: el snapshot vigente MAS RECIENTE ANTERIOR a la ventana pedida
# (open_time < el inicio de ventana). Sembrar de el y dar un rsi_step por cada cierre
# posterior reproduce la cola exacta de la serie (ADR-007), acotando el replay.
_LATEST_BEFORE_SQL = """
SELECT open_time, avg_gain, avg_loss, last_close
FROM rsi_snapshot
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


def write_rsi_snapshot(
    session: Session,
    exchange: str,
    symbol: str,
    timeframe: str,
    period: int,
    open_time: int,
    avg_gain: Decimal,
    avg_loss: Decimal,
    last_close: Decimal,
) -> None:
    """Persiste el estado de rsi de una barra vigente (idempotente, append-only).

    ON CONFLICT DO NOTHING: reevaluar la misma barra recomputa el MISMO estado
    determinista, asi que reinsertar no duplica ni falla. No muta un snapshot previo
    (append-only): una correccion se resolveria con una barra/snapshot nuevos.

    Los TRES valores viajan juntos porque son UN solo estado: escribir avg_gain sin su
    last_close dejaria un ancla desde la que el replay no puede arrancar.
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
            avg_gain,
            avg_loss,
            last_close,
        ),
    )


def read_rsi_snapshot_before(
    session: Session,
    exchange: str,
    symbol: str,
    timeframe: str,
    period: int,
    before_open_time: int,
) -> tuple[int, Decimal, Decimal, Decimal] | None:
    """El estado vigente mas reciente con open_time < before_open_time, o None.

    Devuelve (open_time, avg_gain, avg_loss, last_close). Es el ANCLA del replay: se
    siembra rsi_step con esos tres valores y se avanza con los cierres de las barras
    posteriores hasta la barra pedida. El limite es ESTRICTO ("<", misma convencion que
    cvd_snapshot y ema_snapshot, dictamen Q5) porque el snapshot de la primera barra de
    la ventana ya incorpora el cierre de esa barra: usarlo como ancla la contaria dos
    veces.

    None -> no hay ancla: el materializador arranca el BOOTSTRAP desde el ORIGEN,
    sembrando con rsi_seed en la barra `period` (media simple de los primeros `period`
    gains/losses, convencion Wilder firmada en P08b-02). El RSI NO es
    anchor-independiente, asi que el bootstrap no puede recortarse a la ventana.
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
    return (
        _entero(row[0]),
        _decimal(row[1]),
        _decimal(row[2]),
        _decimal(row[3]),
    )
