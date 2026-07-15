"""Adapters SQL de market data (ADR-014, ADR-011, regla 5.20).

Satisfacen los puertos de ce_v5.platform.market de forma ESTRUCTURAL (son Protocol):
este modulo NO importa platform, ni platform importa infra. Son hermanos
independientes en el contrato de capas; el cableado ocurre en entrypoints.

Disciplina de P05 (regla dura): lo TENANT-SCOPED (los intereses) opera SIEMPRE bajo
el contexto transaccional del resolver (TenantScopedSession), jamas con conexion
cruda. Y DEFENSA EN PROFUNDIDAD (ADR-011): las consultas filtran por tenant_id y
user_id ADEMAS de estar protegidas por RLS. Si un dia fallara una policy, el filtro
de aplicacion sigue en pie; si fallara el filtro, sigue la policy. Ninguna de las dos
capas se apoya en la otra.

El catalogo y las velas son dato PUBLICO (isolation_scope=public_market): no llevan
contexto de tenant. Solo el rol de INGESTA puede escribirlos (regla 5.20); si la API
lo intentara, la rechazaria PostgreSQL, no un if de este fichero.

El driver solo lo conoce el adapter (REST-15).
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from ce_v5.infra.db.ports import Session
from ce_v5.infra.db.tenancy import TenantScopedSession
from source.families.market import (
    IntentSourceType,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    StreamScope,
    SubscriptionIntent,
    Timeframe,
)

_HAS_EXCHANGE_SQL = "SELECT 1 FROM market_instrument WHERE exchange = %s LIMIT 1"

_EXISTS_SQL = """
SELECT 1 FROM market_instrument
WHERE exchange = %s AND market_type = %s AND symbol = %s
"""

_IS_TRADABLE_SQL = """
SELECT 1 FROM market_instrument
WHERE exchange = %s AND market_type = %s AND symbol = %s AND status = 'active'
"""

_NATIVE_SYMBOL_SQL = """
SELECT native_symbol FROM market_instrument
WHERE exchange = %s AND market_type = %s AND symbol = %s
"""

_UPSERT_INSTRUMENT_SQL = """
INSERT INTO market_instrument (exchange, market_type, symbol, native_symbol, status)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (exchange, market_type, symbol) DO UPDATE
SET native_symbol = excluded.native_symbol,
    status = excluded.status,
    updated_at = now()
"""

# NO borra: un par delistado conserva su historico, y borrarlo dejaria velas
# huerfanas apuntando a un instrumento que ya no existe. RETURNING para saber
# CUANTOS se desactivaron de verdad (no cuantos hay inactivos en total).
_DEACTIVATE_MISSING_SQL = """
UPDATE market_instrument
SET status = 'inactive', updated_at = now()
WHERE exchange = %s AND market_type = %s
  AND status = 'active'
  AND NOT (symbol = ANY(%s))
RETURNING symbol
"""

_COUNT_INTENTS_SQL = """
SELECT count(*) FROM market_subscription_intent
WHERE tenant_id = %s AND user_id = %s
"""

# expires_at viaja como EpochMillis (ADR-007) y se guarda como timestamptz porque la
# ventanilla lo compara con now() EN EL MOTOR.
#
# El TIPO del parametro de expires_at se DECLARA (::double precision) porque es el
# unico que puede llegar como NULL, y un NULL sin cualificar es INDETERMINABLE para
# el motor: PostgreSQL no sabe de que tipo es y aborta. No hace falta un CASE WHEN
# ... IS NULL: to_timestamp(NULL) ya devuelve NULL por si solo.
#
# created_at y updated_at NO llevan cast: nunca son NULL (el Clock siempre da un
# entero), asi que el motor infiere su tipo sin ayuda. No se toca lo que funciona.
_INSERT_INTENT_SQL = """
INSERT INTO market_subscription_intent (
    intent_id, tenant_id, user_id, stream_scope, market_stream_key,
    exchange, market_type, symbol, data_kind, timeframe,
    source_type, source_ref, priority, expires_at, created_at, updated_at
) VALUES (
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s,
    %s, %s, %s,
    to_timestamp(%s::double precision / 1000.0),
    to_timestamp(%s / 1000.0), to_timestamp(%s / 1000.0)
)
"""

# RETURNING para contar las filas REALMENTE borradas. Con la RLS activa, un delete
# dirigido a otro tenant no borra nada y devuelve 0: no falla, simplemente no ve la
# fila (mismo comportamiento que P05 demostro en su validacion en caliente).
_DELETE_INTENT_SQL = """
DELETE FROM market_subscription_intent
WHERE tenant_id = %s AND user_id = %s
  AND source_type = %s AND source_ref = %s AND market_stream_key = %s
RETURNING intent_id
"""

# timestamptz -> epoch ms int (ADR-007); NULL se conserva como NULL.
_LIST_INTENTS_SQL = """
SELECT intent_id, tenant_id, user_id, stream_scope,
       exchange, market_type, symbol, data_kind, timeframe,
       source_type, source_ref, priority,
       (extract(epoch from expires_at) * 1000)::bigint,
       (extract(epoch from created_at) * 1000)::bigint,
       (extract(epoch from updated_at) * 1000)::bigint
FROM market_subscription_intent
WHERE tenant_id = %s AND user_id = %s
ORDER BY created_at
"""

_PUBLIC_DEMAND_SQL = (
    "SELECT out_market_stream_key, out_intent_count FROM market_public_demand()"
)


def _entero(valor: object) -> int:
    if not isinstance(valor, int):
        msg = f"Se esperaba un entero de la base y llego {type(valor)!r}."
        raise TypeError(msg)
    return valor


class PostgresInstrumentCatalog:
    """Catalogo de instrumentos sobre PostgreSQL (satisface InstrumentCatalogPort).

    Dato PUBLICO (isolation_scope=public_market): sin contexto de tenant. La lectura
    la hace cualquier rol; la ESCRITURA solo el rol de ingesta (regla 5.20).
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def has_exchange(self, exchange: str) -> bool:
        return self._session.fetchone(_HAS_EXCHANGE_SQL, (exchange,)) is not None

    def exists(self, exchange: str, market_type: str, symbol: str) -> bool:
        row = self._session.fetchone(_EXISTS_SQL, (exchange, market_type, symbol))
        return row is not None

    def is_tradable(self, exchange: str, market_type: str, symbol: str) -> bool:
        row = self._session.fetchone(_IS_TRADABLE_SQL, (exchange, market_type, symbol))
        return row is not None

    def native_symbol(self, exchange: str, market_type: str, symbol: str) -> str | None:
        row = self._session.fetchone(
            _NATIVE_SYMBOL_SQL, (exchange, market_type, symbol)
        )
        return None if row is None else str(row[0])

    def upsert(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        native_symbol: str,
        status: str = "active",
    ) -> None:
        """Alta o actualizacion de un instrumento. SOLO el rol de ingesta (5.20)."""
        self._session.execute(
            _UPSERT_INSTRUMENT_SQL,
            (exchange, market_type, symbol, native_symbol, status),
        )

    def deactivate_missing(
        self, exchange: str, market_type: str, present_symbols: Sequence[str]
    ) -> int:
        """Marca inactivos los que ya no estan en el catalogo remoto. NO los borra.

        Un par delistado sigue teniendo historico: borrarlo dejaria velas huerfanas.
        Un instrumento inactivo no admite intereses nuevos (INSTRUMENT_INACTIVE) pero
        CONSERVA SU PASADO. Devuelve cuantos se desactivaron en ESTA llamada.
        """
        rows = self._session.fetchall(
            _DEACTIVATE_MISSING_SQL, (exchange, market_type, list(present_symbols))
        )
        return len(rows)


class PostgresIntentStore:
    """Intereses sobre PostgreSQL (satisface IntentStorePort).

    Opera SIEMPRE bajo TenantScopedSession: el contexto de tenant/usuario lo fija el
    resolver del backend (P05), nunca el cliente. Ademas filtra por tenant_id y
    user_id en cada consulta (defensa en profundidad, ADR-011).
    """

    def __init__(self, scoped: TenantScopedSession) -> None:
        self._scoped = scoped

    @property
    def _session(self) -> Session:
        return self._scoped.session

    def count_for_subject(self, tenant_id: UUID, user_id: UUID) -> int:
        row = self._session.fetchone(_COUNT_INTENTS_SQL, (str(tenant_id), str(user_id)))
        return 0 if row is None else _entero(row[0])

    def insert(self, intent: SubscriptionIntent) -> None:
        key = intent.stream_key
        timeframe = None if key.timeframe is None else key.timeframe.value
        self._session.execute(
            _INSERT_INTENT_SQL,
            (
                str(intent.intent_id),
                str(intent.tenant_id),
                str(intent.user_id),
                intent.stream_scope.value,
                intent.market_stream_key(),
                key.exchange,
                key.market_type.value,
                key.symbol,
                key.data_kind.value,
                timeframe,
                intent.source_type.value,
                intent.source_ref,
                intent.priority,
                intent.expires_at,
                intent.created_at,
                intent.updated_at,
            ),
        )

    def delete(
        self,
        tenant_id: UUID,
        user_id: UUID,
        source_type: IntentSourceType,
        source_ref: str,
        market_stream_key: str,
    ) -> int:
        rows = self._session.fetchall(
            _DELETE_INTENT_SQL,
            (
                str(tenant_id),
                str(user_id),
                source_type.value,
                source_ref,
                market_stream_key,
            ),
        )
        return len(rows)

    def list_for_subject(
        self, tenant_id: UUID, user_id: UUID
    ) -> list[SubscriptionIntent]:
        rows = self._session.fetchall(_LIST_INTENTS_SQL, (str(tenant_id), str(user_id)))
        return [self._reconstruir(row) for row in rows]

    def _reconstruir(self, row: tuple[object, ...]) -> SubscriptionIntent:
        timeframe = None if row[8] is None else Timeframe(str(row[8]))
        return SubscriptionIntent(
            intent_id=UUID(str(row[0])),
            tenant_id=UUID(str(row[1])),
            user_id=UUID(str(row[2])),
            stream_scope=StreamScope(str(row[3])),
            stream_key=MarketStreamKey(
                exchange=str(row[4]),
                market_type=MarketType(str(row[5])),
                symbol=str(row[6]),
                data_kind=MarketDataKind(str(row[7])),
                timeframe=timeframe,
            ),
            source_type=IntentSourceType(str(row[9])),
            source_ref=str(row[10]),
            priority=_entero(row[11]),
            expires_at=None if row[12] is None else _entero(row[12]),
            created_at=_entero(row[13]),
            updated_at=_entero(row[14]),
        )


class PostgresPublicDemand:
    """La demanda agregada, vista por el WORKER DE INGESTA (CA-P07-D).

    Esta es la UNICA via por la que el worker conoce la demanda. NO puede leer
    market_subscription_intent: se lo impide el MOTOR (permission denied), demostrado
    en tests/integration/test_market_access.py. Por aqui sabe CUANTOS piden un
    stream; jamas QUIENES. La ventanilla ya ignora los caducados y los privados.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def snapshot(self) -> dict[str, int]:
        """Mapa market_stream_key -> cuantos intereses VIVOS lo piden."""
        rows = self._session.fetchall(_PUBLIC_DEMAND_SQL)
        return {str(row[0]): _entero(row[1]) for row in rows}
