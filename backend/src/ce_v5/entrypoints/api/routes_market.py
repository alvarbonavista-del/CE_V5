"""Lectura publica del historico de velas (T-05).

ES UNA HERRAMIENTA DE LECTURA, NO UN CONTRATO DE PRODUCTO. Sirve el historico canonico
tal cual esta (ADR-014) para que se pueda dibujar; no decide nada, no autoriza nada y no
escribe nada. Por eso su modelo de respuesta vive AQUI y no en contracts/source: lo que
entra en el contrato es lo que el producto promete, y esto es una ventana al almacen.

PUBLICO A PROPOSITO, Y SIN SUPERFICIE DE TENANT. El precio de BTC-USDT en Binance no es
dato de nadie: market_candle es public_market (0012), no tiene tenant_id y no lleva RLS.
Pedir identidad aqui no protegeria ningun secreto y solo anadiria un camino mas por el
que la sesion viaja. Este endpoint NO declara Principal: no hay a quien atribuir la
lectura porque no hay nada que atribuir.

SOLO LECTURA, Y LO IMPONE EL MOTOR. La API corre con ce_v5_app, que sobre market_candle
tiene exactamente un privilegio: SELECT (0012). Aunque este fichero quisiera fabricar
una vela, PostgreSQL lo rechazaria (regla 5.20).

SIN RELOJ (ADR-007). Paginar hacia atras necesita un TOPE, no la hora: up_to es ese tope
y lo pone quien llama. Cuando no viene, el tope es el centinela _SIN_TOPE, que no
excluye ninguna vela; asi "las mas recientes" se resuelve con el ORDER BY del historico
y no preguntandole a un reloj que hora es. Un now() aqui haria que la misma peticion
devolviera cosas distintas segun el reloj del servidor.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from ce_v5.entrypoints.api.security import Context
from ce_v5.infra.db.market_candles import read_ohlcv_window

router = APIRouter(prefix="/v1")

# Cuantas velas se sirven si el cliente no dice nada, y cuantas como MUCHO. Una ventana
# sin techo es una invitacion a pedir el historico entero de un flujo en una sola
# peticion, que es trabajo del servidor regalado a quien lo pida.
DEFAULT_LIMIT = 500
MAX_LIMIT = 1000

# CENTINELA "SIN TOPE": el mayor bigint que PostgreSQL admite en open_time. La consulta
# filtra por open_time <= tope, asi que este valor no excluye ninguna vela y el recorte
# lo hace entero el ORDER BY ... LIMIT del historico: salen las `limit` MAS RECIENTES.
# Es tambien el techo de up_to: un valor mayor no cabria en la columna.
_SIN_TOPE = 9_223_372_036_854_775_807


class MarketCandleRead(BaseModel):
    """Una vela del historico, tal como sale al cable.

    LOS PRECIOS VIAJAN COMO TEXTO. El JSON solo tiene numeros de coma flotante binaria:
    serializar un Decimal como number redondearia el dato en el cable y el cliente
    recibiria un precio que NO es el que el exchange publico. Como str llega intacto, y
    quien lo consuma decide con que precision quiere leerlo.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    open_time: int
    open: str
    high: str
    low: str
    close: str
    volume: str


@router.get("/public/market/candles", response_model=list[MarketCandleRead])
def market_candles(
    context: Context,
    exchange: Annotated[str, Query()],
    symbol: Annotated[str, Query()],
    timeframe: Annotated[str, Query()],
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    up_to: Annotated[int | None, Query(ge=0, le=_SIN_TOPE)] = None,
) -> list[MarketCandleRead]:
    """Las `limit` velas maduras mas recientes de un flujo, oldest->newest.

    Un flujo sin historico no es un error: devuelve la lista VACIA con 200. La ausencia
    de dato se dice diciendo que no hay dato, no fallando.
    """
    with context.market_db.transaction() as session:
        velas = read_ohlcv_window(
            session,
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            up_to_open_time=_SIN_TOPE if up_to is None else up_to,
            bars=limit,
        )
    return [
        MarketCandleRead(
            open_time=vela.open_time,
            open=str(vela.open),
            high=str(vela.high),
            low=str(vela.low),
            close=str(vela.close),
            volume=str(vela.volume),
        )
        for vela in velas
    ]
