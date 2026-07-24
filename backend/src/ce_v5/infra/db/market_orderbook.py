"""Escritura del libro L2: snapshot top-K + resync (P07c; ADR-013, regla 5.20).

Espejo de market_footprint.py (persist+outbox ATOMICO) y de market_trades.py (persist
SIN outbox para lo que no se publica). Los dos caminos:

- persist_and_enqueue: el frontier (a market_orderbook_snapshot) y el resync (a
  market_orderbook_discontinuity) van con su outbox en LA MISMA transaccion (ADR-013):
  no puede haber divergencia entre lo persistido y lo publicado. Idempotente por la
  clave del hecho (PK / UNIQUE): reprocesar no duplica ni reencola.

- persist_sample: la muestra intra-ventana va SIN outbox, como los trades.

Solo el rol de INGESTA escribe aqui (regla 5.20, 0020): si lo intentara la API, la
rechazaria PostgreSQL, no un if de este fichero.

Cumple OrderbookWriterPort de ce_v5.platform.market por FORMA (Protocol estructural):
este modulo NO importa platform, ni platform importa infra.

Los niveles viajan a la columna jsonb como lista de objetos con los Decimal EN TEXTO: un
float binario no representa 0.1 exacto, y el libro es la base del precio de ejecucion.
El contrato ya los valido en el borde (ADR-006); aqui solo se serializan.
"""

from __future__ import annotations

import json
import uuid

from ce_v5.infra.db.ports import Database
from source.families.orderbook import (
    OrderbookResyncedPayload,
    OrderbookSnapshotPayload,
)

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
