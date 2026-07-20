"""Escritura del historico de velas: historico + outbox, ATOMICO (ADR-013, 5.20).

Solo el rol de INGESTA puede escribir aqui (regla 5.20). Si lo intentara la API, la
rechazaria PostgreSQL, no un if de este fichero.

Cumple CandleWriterPort de ce_v5.platform.market por FORMA (Protocol estructural):
este modulo NO importa platform, ni platform importa infra.

LECTURA (P08 D1): read_close_window sirve la ventana de cierres que consume el
evaluador de reglas. Es SOLO LECTURA y la ejecuta ce_v5_rules con el GRANT SELECT de la
0016; la escritura sigue siendo exclusiva del rol de ingesta.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from ce_v5.infra.db.ports import Database, Session
from source.families.market import CandlePayload, MarketType, StoredCandle

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

# VENTANA DE CIERRES para el evaluador (P08 D1). Tres decisiones, cada una necesaria:
#
# 1. SOLO VELAS CERRADAS. La tabla ya lo garantiza por CHECK (solo admite 'closed' y
#    'correction': lo provisional NO se persiste, 0012), asi que el invariante D3 "jamas
#    se evalua sobre candle_updated" no depende de este WHERE: lo impone el esquema. El
#    filtro explicito queda igualmente para que la intencion se lea aqui.
#
# 2. UNA FILA POR VENTANA, LA MAS RECIENTE. Una vela corregida NO muta el original: la
#    correccion es una fila NUEVA con el mismo open_time y su correction_revision
#    (append-only, ADR-007). Sin DISTINCT ON, una ventana con correcciones devolveria
#    DOS cierres para el mismo open_time y desplazaria toda la serie: las funciones
#    continuas (average/change/previous_value) operan por POSICION, asi que una barra
#    duplicada corrompe en silencio todos los valores. Se toma la revision MAS ALTA
#    (DESC NULLS LAST: las correcciones primero, el 'closed' original al final), que es
#    el hecho vigente de esa ventana.
#
# 3. LAS ULTIMAS `bars` HASTA up_to_open_time. Se ordena DESC para que el LIMIT recorte
#    por el extremo ANTIGUO (quedarse con las N mas recientes) y la consulta externa
#    reordena a oldest->newest, que es como las funciones canonicas leen la serie.
#
# market_candle es public_market (0012): sin tenant_id y sin RLS, asi que esta lectura
# NO lleva filtro de tenant. No hay frontera de tenant que cruzar porque el dato de
# mercado no es dato de sujeto.
_CLOSE_WINDOW_SQL = """
SELECT w.close
FROM (
    SELECT DISTINCT ON (open_time) open_time, close
    FROM market_candle
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

# La ULTIMA vela madura del flujo (L). Es la vela sobre la que la regla tiene su estado
# VIGENTE: una correccion solo puede cambiar el estado actual si L cae dentro de la
# ventana que la correccion invalida (CA-P08-08). Mismo filtro de madurez que la
# ventana: lo provisional no es historia y no fija estado.
_LAST_CLOSED_SQL = """
SELECT max(open_time)
FROM market_candle
WHERE exchange = %s
  AND market_type = %s
  AND symbol = %s
  AND timeframe = %s
  AND maturity_state IN ('closed', 'correction')
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


def read_close_window(
    session: Session,
    exchange: str,
    symbol: str,
    timeframe: str,
    up_to_open_time: int,
    bars: int,
) -> tuple[Decimal, ...]:
    """La ventana de cierres de un flujo hasta una vela dada, oldest->newest.

    Es la serie que consume el evaluador (platform.rules.evaluator, Series por
    source_id): las `bars` velas CERRADAS mas recientes del (exchange, symbol,
    timeframe) con open_time <= up_to_open_time, una por ventana (la revision vigente si
    hubo correcciones) y en orden creciente de open_time.

    Devuelve menos de `bars` elementos si el historico no da para mas -- e incluso la
    tupla VACIA. No es un error y NO se rellena con nada: el evaluador ya distingue
    "historia insuficiente" como NOT_EVALUABLE (K3), que es distinto de FALSE. Inventar
    barras aqui convertiria un dato ausente en un hecho falso.

    market_type esta FIJADO a spot porque v5.0 solo tiene spot (MarketType). No se
    parametriza "por si acaso": cuando entren derivados, el parametro entra con su uso
    real y este pin -- que es visible y tipado, no una cadena magica -- lo delata.
    """
    rows = session.fetchall(
        _CLOSE_WINDOW_SQL,
        (
            exchange,
            MarketType.SPOT.value,
            symbol,
            timeframe,
            up_to_open_time,
            bars,
        ),
    )
    return tuple(_decimal(row[0]) for row in rows)


def read_last_closed_open_time(
    session: Session, exchange: str, symbol: str, timeframe: str
) -> int | None:
    """El open_time de la ULTIMA vela madura del flujo, o None si no hay ninguna.

    Es "L" en el analisis de correccion (CA-P08-08): la vela sobre la que la regla tiene
    su estado VIGENTE. Una correccion de la vela T solo puede alterar ese estado si L
    cae dentro de la ventana que T invalida; si L ya quedo fuera, el estado actual no se
    calculo con el dato corregido y no hay nada que rehacer.
    """
    row = session.fetchone(
        _LAST_CLOSED_SQL, (exchange, MarketType.SPOT.value, symbol, timeframe)
    )
    if row is None or row[0] is None:
        return None
    return _entero(row[0])
