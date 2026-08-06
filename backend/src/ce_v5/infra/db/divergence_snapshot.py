"""Store del ULTIMO PIVOTE CONFIRMADO DE CADA LADO, por barra (0028).

El motor de reglas (rol ce_v5_rules) ESCRIBE y LEE aqui su propio estado de
materializacion del RECURSIVE divergence.*: el ultimo pivote de maximos y el ultimo de
minimos -- barra, precio y RSI de cada uno -- para SEMBRAR el replay de la ventana
siguiente sin recomputar desde el origen. Append-only: un snapshot por (flujo, strength,
rsi_period, open_time); reevaluar la misma barra reproduce el MISMO estado (la cadena de
pivotes es determinista) y ON CONFLICT DO NOTHING lo absorbe. NO es dato de mercado
(scope=system, 7.8): es estado propio del motor, por eso el rol de reglas SI escribe
aqui
(5.20, 0028).

EL PIVOTE ES EL ESTADO, Y ES MEMORIA SIN COTA. divergence compara cada pivote con el
ANTERIOR DEL MISMO LADO, y entre dos pivotes consecutivos pueden mediar cinco barras o
quinientas: lo decide el mercado, no un parametro. Por eso hay snapshot y no ventana.

Se guarda el PIVOTE, no los eventos: un evento es la comparacion de dos pivotes
consecutivos y se deriva de la cadena. Y basta UNO por lado porque la formula
(detect_divergences) solo mira el par consecutivo.

NULLABLE HASTA EL PRIMER PIVOTE DE CADA LADO, y el RSI aparte de su pivote: durante el
warm-up de Wilder el pivote existe y su RSI no. Ese par sin RSI no produce evento -- lo
dice la formula --, pero SI avanza la cadena, asi que tiene que viajar.

strength y rsi_period ENTRAN EN LA IDENTIDAD: con otra fuerza los pivotes son otros, y
con otro periodo el RSI que se lee en ellos es otro. cadena(2,14) != cadena(7,21).

market_type esta FIJADO a spot (v5.0 solo tiene spot), igual que en los lectores de
mercado; cuando entren derivados, el parametro entra con su uso real.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from source.families.market import MarketType

if TYPE_CHECKING:
    from ce_v5.infra.db.ports import Session

_INSERT_SNAPSHOT_SQL = """
INSERT INTO divergence_snapshot (
    exchange, market_type, symbol, timeframe, strength, rsi_period, open_time,
    last_high_open_time, last_high_price, last_high_rsi,
    last_low_open_time, last_low_price, last_low_rsi
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (exchange, market_type, symbol, timeframe, strength, rsi_period, open_time)
DO NOTHING
"""

# El ancla del replay: el snapshot vigente MAS RECIENTE ANTERIOR a la ventana pedida
# (open_time < el inicio de ventana). Sembrar de el y recorrer los pivotes posteriores
# reproduce la cola exacta de la serie (ADR-007), acotando el replay.
_LATEST_BEFORE_SQL = """
SELECT open_time,
       last_high_open_time, last_high_price, last_high_rsi,
       last_low_open_time, last_low_price, last_low_rsi
FROM divergence_snapshot
WHERE exchange = %s AND market_type = %s AND symbol = %s
  AND timeframe = %s AND strength = %s AND rsi_period = %s AND open_time < %s
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


def _entero_opt(valor: object) -> int | None:
    return None if valor is None else _entero(valor)


def _decimal_opt(valor: object) -> Decimal | None:
    return None if valor is None else _decimal(valor)


@dataclass(frozen=True, slots=True)
class PivotSnapshot:
    """El ultimo pivote confirmado de un lado: su barra, su precio y su RSI.

    rsi es opcional PORQUE puede faltar sin que falte el pivote (warm-up de Wilder). El
    par que lo incluya no produce evento, pero la cadena avanza igual.
    """

    open_time: int
    price: Decimal
    rsi: Decimal | None


@dataclass(frozen=True, slots=True)
class DivergenceSnapshot:
    """El estado de replay de divergence.* en una barra: un pivote por lado.

    Cualquiera de los dos puede ser None al principio del historico: todavia no hay
    maximo (o minimo) confirmado, y esa ausencia es un HECHO, no un hueco.
    """

    open_time: int
    last_high: PivotSnapshot | None
    last_low: PivotSnapshot | None


def write_divergence_snapshot(
    session: Session,
    exchange: str,
    symbol: str,
    timeframe: str,
    strength: int,
    rsi_period: int,
    open_time: int,
    last_high: PivotSnapshot | None,
    last_low: PivotSnapshot | None,
) -> None:
    """Persiste el estado de una barra (idempotente, append-only).

    ON CONFLICT DO NOTHING: reevaluar la misma barra recomputa el MISMO estado
    determinista, asi que reinsertar no duplica ni falla. Es lo que hace inofensivo que
    las CINCO fuentes (kind y los cuatro flags) materialicen la misma barra por
    separado: escriben la misma fila. No muta un snapshot previo (append-only): una
    correccion se resolveria con una barra/snapshot nuevos.

    Los DOS lados viajan juntos porque son UN solo estado: el replay reanuda las dos
    cadenas a la vez y un lado sin el otro no permitiria continuar la que falta.
    """
    session.execute(
        _INSERT_SNAPSHOT_SQL,
        (
            exchange,
            MarketType.SPOT.value,
            symbol,
            timeframe,
            strength,
            rsi_period,
            open_time,
            None if last_high is None else last_high.open_time,
            None if last_high is None else last_high.price,
            None if last_high is None else last_high.rsi,
            None if last_low is None else last_low.open_time,
            None if last_low is None else last_low.price,
            None if last_low is None else last_low.rsi,
        ),
    )


def read_divergence_snapshot_before(
    session: Session,
    exchange: str,
    symbol: str,
    timeframe: str,
    strength: int,
    rsi_period: int,
    before_open_time: int,
) -> DivergenceSnapshot | None:
    """El estado vigente mas reciente con open_time < before_open_time, o None.

    Es el ANCLA del replay: se reanudan las dos cadenas desde esos pivotes y se recorren
    los pivotes POSTERIORES hasta la barra pedida. El limite es ESTRICTO ("<", misma
    convencion que cvd_snapshot, ema_snapshot, rsi_snapshot, macd_snapshot y
    fib_range_snapshot) porque el snapshot de la primera barra de la ventana ya
    incorpora
    los pivotes confirmados en ella: usarlo como ancla los contaria dos veces.

    None -> no hay ancla: el materializador arranca el BOOTSTRAP desde el ORIGEN. La
    cadena de pivotes es anchor-dependiente de toda la historia (el pivote previo de hoy
    puede ser de hace cientos de barras), asi que el bootstrap no puede recortarse a la
    ventana.
    """
    row = session.fetchone(
        _LATEST_BEFORE_SQL,
        (
            exchange,
            MarketType.SPOT.value,
            symbol,
            timeframe,
            strength,
            rsi_period,
            before_open_time,
        ),
    )
    if row is None:
        return None
    high_open_time = _entero_opt(row[1])
    low_open_time = _entero_opt(row[4])
    return DivergenceSnapshot(
        open_time=_entero(row[0]),
        last_high=(
            None
            if high_open_time is None
            else PivotSnapshot(
                open_time=high_open_time,
                price=_decimal(row[2]),
                rsi=_decimal_opt(row[3]),
            )
        ),
        last_low=(
            None
            if low_open_time is None
            else PivotSnapshot(
                open_time=low_open_time,
                price=_decimal(row[5]),
                rsi=_decimal_opt(row[6]),
            )
        ),
    )
