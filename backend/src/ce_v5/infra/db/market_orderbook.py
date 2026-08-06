"""Escritura del libro L2: snapshot top-K + resync (P07c; ADR-013, regla 5.20).

Espejo de market_footprint.py (persist+outbox ATOMICO) y de market_trades.py (persist
SIN outbox para lo que no se publica). Los dos caminos:

- persist_and_enqueue: el frontier (a market_orderbook_snapshot) y el resync (a
  market_orderbook_discontinuity) van con su outbox en LA MISMA transaccion (ADR-013):
  no puede haber divergencia entre lo persistido y lo publicado. Idempotente por la
  clave del hecho (PK / UNIQUE): reprocesar no duplica ni reencola.

- persist_sample: la muestra intra-ventana va SIN outbox, como los trades.

- read_orderbook_frontier_window: la LECTURA que estrena el motor de reglas
  (P08c-CONF-05, grant 0029). Hermana de read_footprint_window: mismo esqueleto de
  recorte y orden, filtrada a kind='frontier'.

Solo el rol de INGESTA escribe aqui (regla 5.20, 0020): si lo intentara la API, la
rechazaria PostgreSQL, no un if de este fichero. El rol de REGLAS solo LEE, y solo
el snapshot (0029).

Cumple OrderbookWriterPort de ce_v5.platform.market por FORMA (Protocol estructural):
este modulo NO importa platform, ni platform importa infra.

Los niveles viajan a la columna jsonb como lista de objetos con los Decimal EN TEXTO: un
float binario no representa 0.1 exacto, y el libro es la base del precio de ejecucion.
El contrato ya los valido en el borde (ADR-006); aqui solo se serializan.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

from ce_v5.infra.db.ports import Database
from source.families.market import MarketType
from source.families.orderbook import (
    OrderbookResyncedPayload,
    OrderbookSnapshotPayload,
)

if TYPE_CHECKING:
    from ce_v5.infra.db.ports import Session

# ON CONFLICT DO NOTHING ... RETURNING: si la clave ya existe no se duplica ni falla,
# y el RETURNING delata si la fila entro DE VERDAD (dedup honesto), como el footprint.
_INSERT_SNAPSHOT_SQL = """
INSERT INTO market_orderbook_snapshot (
    idempotency_key, stream_key, exchange, market_type, symbol, depth_k, sequence,
    kind, timeframe, open_time, close_time, sample_time, bids, asks, is_complete,
    cadence_ms, formula_version, event_time
) VALUES (
    %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s,
    %s, %s, %s
)
ON CONFLICT (idempotency_key) DO NOTHING
RETURNING idempotency_key
"""

# ON CONFLICT DO NOTHING ... RETURNING sobre el UNIQUE NULLS NOT DISTINCT (0020): el
# mismo hueco detectado dos veces es UN hecho. El RETURNING delata si la fila entro,
# igual que el INSERT de huecos de trades (0018).
_INSERT_DISCONTINUITY_SQL = """
INSERT INTO market_orderbook_discontinuity (
    exchange, market_type, symbol, from_sequence, to_sequence, event_time, reason
) VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
RETURNING exchange
"""

# El envelope viaja como TEXTO y se castea a jsonb, como en market_footprint.py (P02b).
_INSERT_OUTBOX_SQL = """
INSERT INTO outbox (event_id, idempotency_key, stream_key, event_type, envelope)
VALUES (%s, %s, %s, %s, %s::jsonb)
ON CONFLICT (idempotency_key) DO NOTHING
"""

# Las discontinuidades que SOLAPAN una ventana de barra, por event_time (ADR-007), como
# read_overlapping_gaps de los trades. Si la tupla NO esta vacia, hubo un resync dentro
# de [window_start, window_end) y el frontier de esa barra se marca is_complete=False
# (fail-safe uniforme, cond.3). Semiabierto por la derecha: un resync en la frontera cae
# en UNA sola barra.
_OVERLAPPING_DISCONTINUITIES_SQL = """
SELECT from_sequence, to_sequence, event_time
FROM market_orderbook_discontinuity
WHERE exchange = %s AND market_type = %s AND symbol = %s
  AND event_time >= %s AND event_time < %s
ORDER BY event_time
"""


def _int(valor: object) -> int:
    if not isinstance(valor, int):
        msg = f"Se esperaba un entero de la base y llego {type(valor)!r}."
        raise TypeError(msg)
    return valor


def _levels_json(payload: OrderbookSnapshotPayload) -> tuple[str, str]:
    """Los bids y asks como JSON, con Decimal EN TEXTO (precision intacta)."""
    bids = json.dumps(
        [{"price": str(level.price), "size": str(level.size)} for level in payload.bids]
    )
    asks = json.dumps(
        [{"price": str(level.price), "size": str(level.size)} for level in payload.asks]
    )
    return bids, asks


class PostgresOrderbookWriter:
    """Persistencia del libro L2 sobre PostgreSQL, con el rol de INGESTA."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def persist_and_enqueue(
        self,
        envelope_json: bytes,
        payload: OrderbookSnapshotPayload | OrderbookResyncedPayload,
        event_type: str,
        stream_key: str,
        idempotency_key: str,
        event_time: int,
    ) -> bool:
        """El hecho publicado (frontier o resync) y su outbox, en LA MISMA transaccion.

        Devuelve False si ya estaba (dedup por la clave del hecho): ni duplica ni
        reencola. UN solo metodo con outbox, como en el footprint: encolar sin persistir
        publicaria algo que el historico no puede demostrar; persistir sin encolar
        dejaria un hecho que nadie publico.
        """
        with self._database.transaction() as session:
            if isinstance(payload, OrderbookResyncedPayload):
                escrita = session.fetchall(
                    _INSERT_DISCONTINUITY_SQL,
                    (
                        payload.exchange,
                        payload.market_type.value,
                        payload.symbol,
                        payload.from_sequence,
                        payload.to_sequence,
                        payload.event_time,
                        payload.reason,
                    ),
                )
            else:
                bids_json, asks_json = _levels_json(payload)
                escrita = session.fetchall(
                    _INSERT_SNAPSHOT_SQL,
                    (
                        idempotency_key,
                        stream_key,
                        payload.exchange,
                        payload.market_type.value,
                        payload.symbol,
                        payload.depth_k,
                        payload.sequence,
                        payload.kind.value,
                        payload.timeframe.value,
                        payload.open_time,
                        payload.close_time,
                        payload.sample_time,
                        bids_json,
                        asks_json,
                        payload.is_complete,
                        payload.cadence_ms,
                        payload.formula_version,
                        event_time,
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

    def persist_sample(
        self,
        payload: OrderbookSnapshotPayload,
        event_time: int,
    ) -> bool:
        """Una muestra intra-ventana (kind='sample'), SIN outbox. False si ya estaba.

        Como PostgresTradeWriter.persist: la muestra no se publica, asi que no hay
        outbox. Idempotente por su idempotency_key (que incluye sample_time): reprocesar
        la misma muestra no la duplica. El INSERT lo comparte con el frontier salvo el
        outbox.
        """
        bids_json, asks_json = _levels_json(payload)
        with self._database.transaction() as session:
            escrita = session.fetchall(
                _INSERT_SNAPSHOT_SQL,
                (
                    payload.idempotency_key(payload.kind),
                    payload.stream_key(),
                    payload.exchange,
                    payload.market_type.value,
                    payload.symbol,
                    payload.depth_k,
                    payload.sequence,
                    payload.kind.value,
                    payload.timeframe.value,
                    payload.open_time,
                    payload.close_time,
                    payload.sample_time,
                    bids_json,
                    asks_json,
                    payload.is_complete,
                    payload.cadence_ms,
                    payload.formula_version,
                    event_time,
                ),
            )
        return bool(escrita)

    def record_discontinuity(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        from_sequence: int,
        to_sequence: int | None,
        event_time: int,
        reason: str,
    ) -> bool:
        """Apunta una discontinuidad del libro SIN publicarla. True si la fila entro.

        Espejo de PostgresTradeWriter.record_gap: registra la AUSENCIA de continuidad
        para que el frontier de las barras solapadas se marque incompleto (cond.3), sin
        encolar nada. Para el resync PUBLICADO (su propio hecho) el motor usa
        persist_and_enqueue, que persiste la discontinuidad Y la encola en LA MISMA
        transaccion (ADR-013): esto es solo el registro fail-safe de una reconexion, que
        se re-siembra en vez de encadenarse, y el motor no ve el hueco por un delta.

        IDEMPOTENTE por el UNIQUE NULLS NOT DISTINCT (0020): la misma discontinuidad
        apuntada dos veces no se duplica. El booleano distingue una NUEVA de una ya
        conocida, lo que permite contar perdida de dato real y no reconexiones.
        """
        with self._database.transaction() as session:
            escrita = session.fetchall(
                _INSERT_DISCONTINUITY_SQL,
                (
                    exchange,
                    market_type,
                    symbol,
                    from_sequence,
                    to_sequence,
                    event_time,
                    reason,
                ),
            )
        return bool(escrita)

    def overlapping_discontinuities(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        window_start: int,
        window_end: int,
    ) -> tuple[tuple[int, int | None, int], ...]:
        """Las discontinuidades cuyo event_time cae en [window_start, window_end).

        Espejo de read_overlapping_gaps: si la tupla NO esta vacia, hubo un resync
        dentro de la ventana y el frontier de esa barra se marca is_complete=False. Cada
        fila es (from_sequence, to_sequence, event_time); to_sequence puede ser NULL
        (extremo desconocido).
        """
        with self._database.transaction() as session:
            rows = session.fetchall(
                _OVERLAPPING_DISCONTINUITIES_SQL,
                (exchange, market_type, symbol, window_start, window_end),
            )
        return tuple(
            (
                _int(row[0]),
                None if row[1] is None else _int(row[1]),
                _int(row[2]),
            )
            for row in rows
        )


# La ventana de snapshots FRONTIER de un flujo hasta una barra, oldest->newest. Calcado
# de _FOOTPRINT_WINDOW_SQL salvo por dos cosas: el filtro kind='frontier' y la AUSENCIA
# de DISTINCT ON. El footprint lo necesita porque una barra puede tener varias
# revisiones (correcciones); el libro NO se corrige -- se REINICIA, y el reinicio es su
# propio hecho (market.orderbook_resynced) --, asi que la PK (idempotency_key, que ya
# incluye la ventana y la config) deja como mucho un frontier por open_time.
_FRONTIER_WINDOW_SQL = """
SELECT
    exchange, market_type, symbol, depth_k, bids, asks, sequence, kind,
    timeframe, open_time, close_time, sample_time, is_complete,
    cadence_ms, formula_version
FROM (
    SELECT
        exchange, market_type, symbol, depth_k, bids, asks, sequence, kind,
        timeframe, open_time, close_time, sample_time, is_complete,
        cadence_ms, formula_version
    FROM market_orderbook_snapshot
    WHERE exchange = %s
      AND market_type = %s
      AND symbol = %s
      AND timeframe = %s
      AND kind = 'frontier'
      AND open_time <= %s
    ORDER BY open_time DESC
    LIMIT %s
) AS w
ORDER BY w.open_time
"""

_FRONTIER_COLUMNS = (
    "exchange",
    "market_type",
    "symbol",
    "depth_k",
    "bids",
    "asks",
    "sequence",
    "kind",
    "timeframe",
    "open_time",
    "close_time",
    "sample_time",
    "is_complete",
    "cadence_ms",
    "formula_version",
)


def read_orderbook_frontier_window(
    session: Session,
    exchange: str,
    symbol: str,
    timeframe: str,
    up_to_open_time: int,
    bars: int,
) -> tuple[OrderbookSnapshotPayload, ...]:
    """La ventana de snapshots FRONTIER de un flujo hasta una barra, oldest->newest.

    Hermana de read_footprint_window y con su MISMA firma a proposito: quien materializa
    un WINDOWED pide una ventana igual de las dos tablas y las empareja por open_time.
    Devuelve OrderbookSnapshotPayload (la BASE del contrato) y no un tipo de lectura
    propio, por la misma razon que el footprint: las funciones puras que lo consumen ya
    hablan ese tipo.

    La reconstruccion pasa por model_validate, igual que el footprint: pydantic
    COACCIONA los tipos (Decimal desde el texto de los niveles jsonb, enums desde su
    value) y RE-EJECUTA los validadores del contrato -- una fila con niveles
    desordenados, de tamano no positivo o fuera del top-K LANZA aqui, en vez de servir
    un libro mentiroso a las features de L2.

    SOLO 'frontier'. La variante 'sample' comparte tabla pero es otra cosa: una muestra
    intra-ventana a cadencia, de la que puede haber MUCHAS por barra. Mezclarlas
    romperia el 1:1 con la barra del que depende el emparejamiento con el footprint.

    DEVUELVE MENOS DE `bars` (hasta la tupla VACIA) si el historico no da para mas, y NO
    RELLENA NADA. Aqui eso importa mas que en el footprint: una barra sin frontier es
    normal (el libro pudo no estar suscrito, o hubo resync) y el consumidor tiene que
    verlo como AUSENCIA para aplicar su fail-safe -- por eso el emparejamiento con el
    footprint se hace por open_time y no por posicion. market_type FIJADO a spot (v5.0
    solo tiene spot), igual que en read_footprint_window.
    """
    rows = session.fetchall(
        _FRONTIER_WINDOW_SQL,
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
        OrderbookSnapshotPayload.model_validate(
            dict(zip(_FRONTIER_COLUMNS, row, strict=True))
        )
        for row in rows
    )


def frontier_by_open_time(
    frontiers: tuple[OrderbookSnapshotPayload, ...],
) -> dict[int, OrderbookSnapshotPayload]:
    """Indice open_time -> frontier, para emparejar con la ventana de footprint.

    El emparejamiento va por CLAVE y no por posicion porque las dos ventanas pueden no
    cubrir las mismas barras: el footprint existe siempre que hubo trades, el frontier
    solo si el libro estaba vivo. Un zip posicional emparejaria el libro de una barra
    con el footprint de otra -- el mismo fallo MUDO que _read_detector_window comprueba
    entre footprint y vela --, y aqui no se puede exigir igualdad porque la ausencia es
    un caso LEGITIMO con fail-safe propio.
    """
    return {snapshot.open_time: snapshot for snapshot in frontiers}
