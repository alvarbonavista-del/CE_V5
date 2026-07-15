"""Escritura del historico de velas: historico + outbox, ATOMICO (ADR-013, 5.20).

Solo el rol de INGESTA puede escribir aqui (regla 5.20). Si lo intentara la API, la
rechazaria PostgreSQL, no un if de este fichero.

Cumple CandleWriterPort de ce_v5.platform.market por FORMA (Protocol estructural):
este modulo NO importa platform, ni platform importa infra.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from ce_v5.infra.db.ports import Database
from source.families.market import CandlePayload, StoredCandle

# La vela ORIGINAL de esa ventana (la cerrada), mas el numero de revision mas alto
# entre sus correcciones. Con esos dos datos se decide si lo que llega es un DUPLICADO
# o una CORRECCION, y que revision le toca.
_EXISTING_SQL = """
SELECT c.idempotency_key, c.open, c.high, c.low, c.close, c.volume,
       coalesce((
           SELECT max(k.correction_revision)
           FROM market_candle k
           WHERE k.stream_key = c.stream_key
             AND k.open_time = c.open_time
             AND k.maturity_state = 'correction'
       ), 0)
FROM market_candle c
WHERE c.stream_key = %s AND c.open_time = %s AND c.maturity_state = 'closed'
"""

# ON CONFLICT DO NOTHING: si la clave ya existe, no se duplica y no se falla. El
# RETURNING delata si la fila entro de verdad (dedup honesto).
_INSERT_CANDLE_SQL = """
INSERT INTO market_candle (
    idempotency_key, stream_key, exchange, market_type, symbol, timeframe,
    open_time, close_time, open, high, low, close, volume,
    maturity_state, correction_revision, corrects_idempotency_key
) VALUES (
    %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s
)
ON CONFLICT (idempotency_key) DO NOTHING
RETURNING idempotency_key
"""

# El envelope viaja como TEXTO y se castea a jsonb, igual que en outbox.py (P02b).
_INSERT_OUTBOX_SQL = """
INSERT INTO outbox (event_id, idempotency_key, stream_key, event_type, envelope)
VALUES (%s, %s, %s, %s, %s::jsonb)
ON CONFLICT (idempotency_key) DO NOTHING
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


class PostgresCandleWriter:
    """Historico de velas sobre PostgreSQL, con el rol de INGESTA."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def existing(self, stream_key: str, open_time_ms: int) -> StoredCandle | None:
        """La vela ORIGINAL guardada para esa ventana, con su revision mas alta."""
        with self._database.transaction() as session:
            row = session.fetchone(_EXISTING_SQL, (stream_key, open_time_ms))
        if row is None:
            return None
        return StoredCandle(
            idempotency_key=str(row[0]),
            open=_decimal(row[1]),
            high=_decimal(row[2]),
            low=_decimal(row[3]),
            close=_decimal(row[4]),
            volume=_decimal(row[5]),
            max_correction_revision=_entero(row[6]),
        )

    def persist_and_enqueue(
        self,
        envelope_json: bytes,
        payload: CandlePayload,
        event_type: str,
        stream_key: str,
        idempotency_key: str,
    ) -> bool:
        """El historico y la outbox, en LA MISMA TRANSACCION.

        LOS DOS INSERT VAN JUNTOS PORQUE ADR-013 EXIGE QUE NO PUEDA HABER DIVERGENCIA
        entre lo persistido y lo publicado. Separarlos en dos transacciones
        reintroduciria exactamente el fallo que el outbox existe para impedir: una
        vela guardada que nadie publico nunca (el grafico la tiene, las reglas no se
        enteraron), o un evento publicado sin vela detras (las reglas dispararon sobre
        un hecho que el historico no puede demostrar).

        Devuelve False si la vela ya estaba (dedup por idempotency_key): ni se duplica
        ni se vuelve a encolar.
        """
        timeframe = payload.timeframe.value
        with self._database.transaction() as session:
            escrita = session.fetchall(
                _INSERT_CANDLE_SQL,
                (
                    idempotency_key,
                    stream_key,
                    payload.exchange,
                    payload.market_type.value,
                    payload.symbol,
                    timeframe,
                    payload.open_time,
                    payload.close_time,
                    payload.open,
                    payload.high,
                    payload.low,
                    payload.close,
                    payload.volume,
                    payload.maturity_state.value,
                    payload.correction_revision,
                    payload.corrects_idempotency_key,
                ),
            )
            if not escrita:
                # Ya existia: no se duplica, y NO se encola (encolar sin insertar
                # publicaria dos veces el mismo hecho).
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
