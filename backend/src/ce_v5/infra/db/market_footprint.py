"""Escritura del footprint por barra: historico + outbox, ATOMICO (ADR-013, 5.20).

Espejo EXACTO de PostgresCandleWriter.persist_and_enqueue (market_candles.py): los dos
INSERT -- market_footprint y outbox -- van en LA MISMA transaccion porque ADR-013 exige
que no pueda haber divergencia entre lo persistido y lo publicado. Idempotente por
footprint_idempotency_key (PK de market_footprint + UNIQUE de la outbox): reprocesar el
mismo candle_closed reconstruye la MISMA clave y no duplica ni re-encola.

Solo el rol de INGESTA escribe aqui (regla 5.20, 0017): si lo intentara la API, la
rechazaria PostgreSQL, no un if de este fichero.

Cumple FootprintWriterPort de ce_v5.platform.market por FORMA (Protocol estructural):
modulo NO importa platform, ni platform importa infra.

Las CELDAS viajan a la columna jsonb como lista de objetos con los Decimal EN TEXTO: un
float binario no representa 0.1 exacto, y el footprint es la suma de volumenes trade a
trade. El contrato ya las valido en el borde (ADR-006); aqui solo se serializan.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

from ce_v5.infra.db.ports import Database, Session
from source.families.footprint import FootprintPayload
from source.families.market import MarketType

# ON CONFLICT DO NOTHING ... RETURNING: si la clave ya existe no se duplica ni falla, y
# el RETURNING delata si la fila entro DE VERDAD (dedup honesto), como en las velas.
_INSERT_FOOTPRINT_SQL = """
INSERT INTO market_footprint (
    idempotency_key, stream_key, exchange, market_type, symbol, timeframe,
    open_time, close_time, cells, bar_buy_volume, bar_sell_volume, bar_delta,
    trade_count, is_complete, maturity_state, correction_revision,
    corrects_idempotency_key
) VALUES (
    %s, %s, %s, %s, %s, %s,
    %s, %s, %s::jsonb, %s, %s, %s,
    %s, %s, %s, %s,
    %s
)
ON CONFLICT (idempotency_key) DO NOTHING
RETURNING idempotency_key
"""

# El envelope viaja como TEXTO y se castea a jsonb, como en market_candles.py (P02b).
_INSERT_OUTBOX_SQL = """
INSERT INTO outbox (event_id, idempotency_key, stream_key, event_type, envelope)
VALUES (%s, %s, %s, %s, %s::jsonb)
ON CONFLICT (idempotency_key) DO NOTHING
"""


def _cells_json(payload: FootprintPayload) -> str:
    """Las celdas del footprint como JSON, con Decimal EN TEXTO (precision intacta)."""
    return json.dumps(
        [
            {
                "price": str(cell.price),
                "buy_volume": str(cell.buy_volume),
                "sell_volume": str(cell.sell_volume),
                "delta": str(cell.delta),
            }
            for cell in payload.cells
        ]
    )


class PostgresFootprintWriter:
    """Historico de footprint sobre PostgreSQL, con el rol de INGESTA."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def persist_and_enqueue(
        self,
        envelope_json: bytes,
        payload: FootprintPayload,
        event_type: str,
        stream_key: str,
        idempotency_key: str,
    ) -> bool:
        """El footprint y la outbox, en LA MISMA TRANSACCION (ADR-013).

        Devuelve False si el footprint ya estaba (dedup por idempotency_key): ni duplica
        ni vuelve a encolar. UN solo metodo, como en las velas: encolar sin insertar
        publicaria un footprint que el historico no puede demostrar; insertar sin
        encolar dejaria una barra que nadie publico.
        """
        with self._database.transaction() as session:
            escrita = session.fetchall(
                _INSERT_FOOTPRINT_SQL,
                (
                    idempotency_key,
                    stream_key,
                    payload.exchange,
                    payload.market_type.value,
                    payload.symbol,
                    payload.timeframe.value,
                    payload.open_time,
                    payload.close_time,
                    _cells_json(payload),
                    payload.bar_buy_volume,
                    payload.bar_sell_volume,
                    payload.bar_delta,
                    payload.trade_count,
                    payload.is_complete,
                    payload.maturity_state.value,
                    payload.correction_revision,
                    payload.corrects_idempotency_key,
                ),
            )
            if not escrita:
                return False
            session.execute(
                _INSERT_OUTBOX_SQL,
                (
                    str(uuid.uuid4()),
                    idempotency_key,
                    stream_key,
                    event_type,
                    envelope_json.decode(),
                ),
            )
        return True


_FOOTPRINT_WINDOW_SQL = """
SELECT
    w.exchange, w.market_type, w.symbol, w.timeframe,
    w.open_time, w.close_time, w.cells,
    w.bar_buy_volume, w.bar_sell_volume, w.bar_delta,
    w.trade_count, w.is_complete, w.maturity_state,
    w.correction_revision, w.corrects_idempotency_key
FROM (
    SELECT DISTINCT ON (open_time)
        exchange, market_type, symbol, timeframe,
        open_time, close_time, cells,
        bar_buy_volume, bar_sell_volume, bar_delta,
        trade_count, is_complete, maturity_state,
        correction_revision, corrects_idempotency_key
    FROM market_footprint
    WHERE exchange = %s
      AND market_type = %s
      AND symbol = %s
      AND timeframe = %s
      AND maturity_state IN ('closed', 'correction')
      AND open_time <= %s
    ORDER BY open_time DESC, correction_revision DESC NULLS LAST
    LIMIT %s
) AS w
ORDER BY w.open_time
"""

_FOOTPRINT_COLUMNS = (
    "exchange",
    "market_type",
    "symbol",
    "timeframe",
    "open_time",
    "close_time",
    "cells",
    "bar_buy_volume",
    "bar_sell_volume",
    "bar_delta",
    "trade_count",
    "is_complete",
    "maturity_state",
    "correction_revision",
    "corrects_idempotency_key",
)


def read_footprint_window(
    session: Session,
    exchange: str,
    symbol: str,
    timeframe: str,
    up_to_open_time: int,
    bars: int,
) -> tuple[FootprintPayload, ...]:
    """La ventana de footprints de un flujo hasta una barra, oldest->newest.

    Es la BASE que consume la materializacion WINDOWED (vp.* en CE-14): las `bars`
    barras de footprint mas recientes del (exchange, symbol, timeframe) con
    open_time <= up_to_open_time, UNA por ventana (la revision VIGENTE si hubo
    correcciones, por el DISTINCT ON + correction_revision DESC NULLS LAST) y en
    orden creciente de open_time. Hermana de read_ohlcv_window: MISMO esqueleto de
    dedup y recorte, solo cambian la tabla y las columnas.

    Devuelve FootprintPayload (la BASE del contrato), no un tipo de lectura propio,
    porque las funciones PURAS de vp.* (compute_volume_profile / compute_volume_nodes)
    consumen Sequence[FootprintPayload]: reconstruir a otra forma obligaria a
    reescribir ese nucleo ya cerrado. La reconstruccion pasa por model_validate, igual
    que composition.py rehidrata CandleClosedPayload del sobre: pydantic COACCIONA los
    tipos (Decimal desde el texto de las celdas jsonb, enums desde su value, EpochMillis
    desde el entero) y RE-EJECUTA los validadores del contrato -- una fila corrupta
    (celdas desordenadas, totales que no cuadran) LANZA aqui, en vez de servir un
    footprint mentiroso al perfil.

    IMPORTANTE sobre el tamano: `bars` es el numero de footprints BASE devueltos, no el
    de valores de salida del perfil. Quien materializa un WINDOWED (composition, MAT-02
    punto 3) pide history_bars + window_bars - 1 footprints para producir history_bars
    valores; ese dimensionado es del caller, no de este lector.

    Devuelve menos de `bars` (hasta la tupla VACIA) si el historico no da para mas,
    y NO rellena nada: un hueco es un hecho ausente y el evaluador ya lo distingue como
    NOT_EVALUABLE (K3), distinto de FALSE. market_type esta FIJADO a spot (v5.0 solo
    tiene spot), igual que en read_close_window.
    """
    rows = session.fetchall(
        _FOOTPRINT_WINDOW_SQL,
        (
            exchange,
            MarketType.SPOT.value,
            symbol,
            timeframe,
            up_to_open_time,
            bars,
        ),
    )
    return tuple(
        FootprintPayload.model_validate(dict(zip(_FOOTPRINT_COLUMNS, row, strict=True)))
        for row in rows
    )


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


# Deltas de barra (bar_delta) del footprint en un RANGO (after, up_to], oldest->newest,
# una fila por barra (revision vigente). Es la entrada del replay del INTEGRATOR cvd:
# los deltas POSTERIORES al ancla de snapshot. Mismo dedup por revision que
# read_footprint_window; bar_delta es la MISMA columna que sirve orderflow.delta (una
# sola definicion de "delta desde footprint", MAT-07), aqui leida por rango para el
# replay acotado en vez de por ventana ultima-N.
_FOOTPRINT_DELTA_RANGE_SQL = """
SELECT w.open_time, w.bar_delta
FROM (
    SELECT DISTINCT ON (open_time) open_time, bar_delta
    FROM market_footprint
    WHERE exchange = %s
      AND market_type = %s
      AND symbol = %s
      AND timeframe = %s
      AND maturity_state IN ('closed', 'correction')
      AND open_time > %s
      AND open_time <= %s
    ORDER BY open_time DESC, correction_revision DESC NULLS LAST
) AS w
ORDER BY w.open_time
"""


def read_footprint_delta_range(
    session: Session,
    exchange: str,
    symbol: str,
    timeframe: str,
    after_open_time: int,
    up_to_open_time: int,
) -> tuple[tuple[int, Decimal], ...]:
    """Los (open_time, bar_delta) del footprint en (after_open_time, up_to_open_time].

    oldest->newest, una fila por barra (la revision vigente si hubo correcciones, por el
    DISTINCT ON + correction_revision DESC NULLS LAST). Es la secuencia de deltas que el
    materializador de cvd (INTEGRATOR) acumula sobre el valor del snapshot ancla. El
    limite inferior es ABIERTO (after excluido: el ancla ya aporta su valor) y el
    superior CERRADO (up_to incluido: la barra pedida). Rango vacio -> ().

    market_type FIJADO a spot (v5.0), como en read_footprint_window.
    """
    rows = session.fetchall(
        _FOOTPRINT_DELTA_RANGE_SQL,
        (
            exchange,
            MarketType.SPOT.value,
            symbol,
            timeframe,
            after_open_time,
            up_to_open_time,
        ),
    )
    return tuple((_entero(row[0]), _decimal(row[1])) for row in rows)
