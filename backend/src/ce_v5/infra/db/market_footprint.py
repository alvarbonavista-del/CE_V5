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

from ce_v5.infra.db.ports import Database
from source.families.footprint import FootprintPayload

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
