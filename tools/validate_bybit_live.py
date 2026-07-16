"""Validacion en caliente REAL de Bybit v5 (T-03): streaming, reconexion y dedup.

Conecta el WebSocket REAL de Bybit, recibe velas REALES y las convierte en hechos del
sistema contra la base LOCAL de juguete. Es la MISMA maquinaria de P07 (motor, dedup,
bootstrap) validando un TERCER exchange: parte del veredicto de CE-14. El connector se
construye POR EL REGISTRO (resolve("bybit")).

SANDBOX LOCAL, MARKET DATA PUBLICA REAL, JAMAS DINERO. Usuario de demo con email fijo;
se limpian sus intents y las velas del stream al terminar.

REGLA DURA: el connector usa hilos daemon. Este arnes cierra el datasource
(connector.shutdown()) en un finally PASE LO QUE PASE, y es ACOTADO en el tiempo
(CE_V5_LIVE_WINDOW_S, 75 s por defecto). Nada de bucle infinito.

BOOTSTRAP: el auto-bootstrap tras reconexion esta CABLEADO (P07-R1). Este arnes NO
reinyecta nada: fuerza una reconexion REAL (force_reconnect_all) y el MOTOR se
rebootstrapea SOLO (drain_reconnected + drain_once -> fetch_recent por el mismo camino
de dedup). Se comprueba con dato REAL que rellena el hueco sin duplicar.

GUARDIA 5.20: un solo proceso porta varios roles (app, migraciones, ingesta). Cada
cargador ve el sub-entorno con SOLO su DSN (_solo). Limpiar velas exige el rol OWNER
(migraciones): market_candle es append-only.

Uso: python tools/validate_bybit_live.py
Requiere CE_V5_DATABASE_URL, CE_V5_MIGRATIONS_DATABASE_URL, CE_V5_INGESTION_DATABASE_URL
y CE_V5_REDIS_URL.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))
sys.path.insert(0, str(REPO_ROOT / "contracts"))

from ce_v5.core.clock import Clock, SystemClock  # noqa: E402
from ce_v5.entrypoints.worker_ingestion.catalog_sync import sync_catalog  # noqa: E402
from ce_v5.entrypoints.worker_ingestion.connector_registry import (  # noqa: E402
    build_default_registry,
)
from ce_v5.infra.bus_redis import (  # noqa: E402
    RedisBusConfig,
    RedisEventBus,
    create_client,
)
from ce_v5.infra.bus_redis.config import REDIS_URL_ENV_VAR  # noqa: E402
from ce_v5.infra.connectors.bybit.connector import BybitSpotConnector  # noqa: E402
from ce_v5.infra.db.config import (  # noqa: E402
    DSN_ENV_VAR,
    INGESTION_DSN_ENV_VAR,
    MIGRATIONS_DSN_ENV_VAR,
    DbConfig,
    IngestionDbConfig,
)
from ce_v5.infra.db.identity import register_user  # noqa: E402
from ce_v5.infra.db.market_candles import PostgresCandleWriter  # noqa: E402
from ce_v5.infra.db.market_store import (  # noqa: E402
    PostgresInstrumentCatalog,
    PostgresIntentStore,
)
from ce_v5.infra.db.ports import Database  # noqa: E402
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase  # noqa: E402
from ce_v5.infra.db.tenancy import (  # noqa: E402
    TenantScopedDatabase,
    provision_tenant_for_user,
)
from ce_v5.platform.market.ingestor import IngestionEngine  # noqa: E402
from source.families.market import (  # noqa: E402
    Instrument,
    IntentSourceType,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    RawCandle,
    StreamScope,
    SubscriptionIntent,
    Timeframe,
)

_EMAIL = "hot-t03-bybit-live@ejemplo.test"
_PASSWORD_HASH = "hash-de-prueba-no-es-argon2"
_SOURCE = "worker_ingestion"

_CLAVE = MarketStreamKey(
    exchange="bybit",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.CANDLES,
    timeframe=Timeframe.M1,
)
_CLAVE_STR = _CLAVE.as_stream_key()

_WINDOW_ENV = "CE_V5_LIVE_WINDOW_S"
_DEFAULT_WINDOW_S = 75
_PAUSA_S = 2.0
_METRICS_EVERY_S = 5.0
_RECONNECT_DRAIN_S = 15


def _solo(*claves: str) -> dict[str, str]:
    """El sub-entorno con SOLO esas variables (guardia 5.20 hecha explicita)."""
    return {clave: os.environ[clave] for clave in claves if clave in os.environ}


def _exigir_env() -> None:
    """Falla RUIDOSO si falta un DSN/URL obligatorio. No se salta (regla 5.18)."""
    faltan = [
        var
        for var in (
            DSN_ENV_VAR,
            MIGRATIONS_DSN_ENV_VAR,
            INGESTION_DSN_ENV_VAR,
            REDIS_URL_ENV_VAR,
        )
        if not os.environ.get(var, "").strip()
    ]
    if faltan:
        print(
            "FALLO: faltan variables obligatorias para la validacion en caliente de "
            f"Bybit: {', '.join(faltan)}. Exige el sistema completo (app + migraciones "
            "+ ingesta + redis): no se salta, se configura el entorno.",
            file=sys.stderr,
        )
        raise SystemExit(2)


class _ObservingSource:
    """Decorador TRANSPARENTE sobre el connector real: delega TODO y recuerda la ultima
    vela vista en poll(), para imprimir un precio real y vivo.
    """

    def __init__(self, inner: BybitSpotConnector) -> None:
        self._inner = inner
        self.ultima: RawCandle | None = None
        self.total_vistas = 0

    def open(self, key: MarketStreamKey) -> None:
        self._inner.open(key)

    def close(self, key: MarketStreamKey) -> None:
        self._inner.close(key)

    def active(self) -> set[str]:
        return set(self._inner.active())

    def poll(self, timeout_ms: int) -> Sequence[RawCandle]:
        lote = self._inner.poll(timeout_ms)
        if lote:
            self.ultima = lote[-1]
            self.total_vistas += len(lote)
        return lote

    def fetch_recent(self, key: MarketStreamKey, limit: int) -> Sequence[RawCandle]:
        return self._inner.fetch_recent(key, limit)

    def list_instruments(self, market_type: str) -> Sequence[Instrument]:
        return self._inner.list_instruments(market_type)

    def supported_timeframes(self) -> frozenset[Timeframe]:
        return self._inner.supported_timeframes()

    def drain_reconnected(self) -> set[str]:
        return set(self._inner.drain_reconnected())


class _CatalogoEnIngesta:
    """Catalogo real por-sesion con el rol de INGESTA (regla 5.20). Satisface
    CatalogWriterPort (upsert + deactivate).
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    def has_exchange(self, exchange: str) -> bool:
        with self._database.transaction() as session:
            return PostgresInstrumentCatalog(session).has_exchange(exchange)

    def upsert(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        native_symbol: str,
        status: str = "active",
    ) -> None:
        with self._database.transaction() as session:
            PostgresInstrumentCatalog(session).upsert(
                exchange, market_type, symbol, native_symbol, status
            )

    def deactivate_missing(
        self, exchange: str, market_type: str, present_symbols: list[str]
    ) -> int:
        with self._database.transaction() as session:
            return PostgresInstrumentCatalog(session).deactivate_missing(
                exchange, market_type, present_symbols
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


def _usuario(migrations_db: Database, app_db: Database) -> UUID:
    with migrations_db.transaction() as session:
        row = session.fetchone(
            "SELECT user_id FROM app_user WHERE email = %s", (_EMAIL,)
        )
    if row is not None:
        return UUID(str(row[0]))
    return register_user(app_db, _EMAIL, _PASSWORD_HASH)


def _tenant(migrations_db: Database, app_db: Database, user_id: UUID) -> UUID:
    with migrations_db.transaction() as session:
        row = session.fetchone(
            "SELECT tenant_id FROM user_tenant_membership WHERE user_id = %s",
            (str(user_id),),
        )
    if row is not None:
        return UUID(str(row[0]))
    return provision_tenant_for_user(app_db, user_id)


def _sembrar_intent(
    scoped_db: TenantScopedDatabase, tenant_id: UUID, user_id: UUID, clock: Clock
) -> None:
    now = clock.now_ms()
    with scoped_db.transaction(user_id) as scoped:
        PostgresIntentStore(scoped).insert(
            SubscriptionIntent(
                intent_id=uuid4(),
                tenant_id=tenant_id,
                user_id=user_id,
                stream_scope=StreamScope.PUBLIC_MARKET,
                stream_key=_CLAVE,
                source_type=IntentSourceType.WIDGET,
                source_ref="hot-live",
                created_at=now,
                updated_at=now,
            )
        )


def _limpiar_intent(
    scoped_db: TenantScopedDatabase, tenant_id: UUID, user_id: UUID
) -> None:
    with scoped_db.transaction(user_id) as scoped:
        scoped.session.execute(
            "DELETE FROM market_subscription_intent "
            "WHERE tenant_id = %s AND user_id = %s",
            (str(tenant_id), str(user_id)),
        )


def _limpiar_velas(owner_db: Database, stream_key: str) -> None:
    """Borra outbox y velas del stream de demo. EXIGE el rol OWNER (migraciones):
    market_candle es append-only y el rol de ingesta no puede borrar.
    """
    with owner_db.transaction() as session:
        session.execute("DELETE FROM outbox WHERE stream_key = %s", (stream_key,))
        session.execute(
            "DELETE FROM market_candle WHERE stream_key = %s", (stream_key,)
        )


def _contar_cerradas(reader_db: Database, stream_key: str) -> int:
    with reader_db.transaction() as session:
        row = session.fetchone(
            "SELECT count(*) FROM market_candle "
            "WHERE stream_key = %s AND maturity_state = 'closed'",
            (stream_key,),
        )
    return 0 if row is None else _entero(row[0])


def _contar_outbox(reader_db: Database, stream_key: str) -> int:
    with reader_db.transaction() as session:
        row = session.fetchone(
            "SELECT count(*) FROM outbox "
            "WHERE stream_key = %s AND event_type = 'market.candle_closed'",
            (stream_key,),
        )
    return 0 if row is None else _entero(row[0])


def _filas_y_claves_distintas(reader_db: Database, stream_key: str) -> tuple[int, int]:
    """(filas closed, idempotency_key DISTINTAS). La prueba FUERTE de no duplicacion."""
    with reader_db.transaction() as session:
        row = session.fetchone(
            "SELECT count(*), count(DISTINCT idempotency_key) FROM market_candle "
            "WHERE stream_key = %s AND maturity_state = 'closed'",
            (stream_key,),
        )
    if row is None:
        return 0, 0
    return _entero(row[0]), _entero(row[1])


def _muestra_cerradas(
    reader_db: Database, stream_key: str, limite: int
) -> list[tuple[int, Decimal]]:
    with reader_db.transaction() as session:
        rows = session.fetchall(
            "SELECT open_time, close FROM market_candle "
            "WHERE stream_key = %s AND maturity_state = 'closed' "
            "ORDER BY open_time DESC LIMIT %s",
            (stream_key, limite),
        )
    return [(_entero(r[0]), _decimal(r[1])) for r in rows]


def _imprimir_metricas(engine: IngestionEngine, observing: _ObservingSource) -> None:
    m = engine.metrics
    ultima = observing.ultima
    precio = "-" if ultima is None else ultima.close
    print(
        f"  [metricas] provisionales={m.provisional_published} "
        f"cerradas={m.closed_persisted} correcciones={m.corrections_emitted} "
        f"duplicados={m.duplicates_skipped} rechazos={sum(m.rejected.values())} "
        f"degradados={sorted(m.degraded_streams)} ultimo_precio={precio}"
    )


def _drenar_durante(
    engine: IngestionEngine,
    observing: _ObservingSource,
    segundos: float,
    etiqueta: str,
) -> None:
    print(f"\n=== {etiqueta} (ventana {segundos:.0f}s) ===")
    fin = time.monotonic() + segundos
    ultimo_print = 0.0
    while time.monotonic() < fin:
        engine.drain_once()
        ahora = time.monotonic()
        if ahora - ultimo_print >= _METRICS_EVERY_S:
            _imprimir_metricas(engine, observing)
            ultimo_print = ahora
        time.sleep(_PAUSA_S)
    _imprimir_metricas(engine, observing)


def _fase2(
    connector: BybitSpotConnector,
    engine: IngestionEngine,
    observing: _ObservingSource,
    reader_db: Database,
) -> bool:
    print("\n=== FASE 2: reconexion REAL + bootstrap AUTONOMO del motor ===")

    base = _contar_cerradas(reader_db, _CLAVE_STR)
    reconn_antes = connector.metrics.reconnections
    boot_antes = engine.metrics.bootstrap_candles
    dup_antes = engine.metrics.duplicates_skipped
    print(f"  velas cerradas en historico ANTES: {base}")

    cerradas = connector.force_reconnect_all()
    print(f"  force_reconnect_all: cerro {cerradas} conexion(es)")

    _drenar_durante(
        engine, observing, _RECONNECT_DRAIN_S, "FASE 2: drenaje tras reconexion"
    )

    despues = _contar_cerradas(reader_db, _CLAVE_STR)
    reconn = connector.metrics.reconnections - reconn_antes
    boot = engine.metrics.bootstrap_candles - boot_antes
    dup = engine.metrics.duplicates_skipped - dup_antes
    filas, claves = _filas_y_claves_distintas(reader_db, _CLAVE_STR)
    hueco = despues - base

    print(f"  reconnections={reconn} bootstrap_candles={boot} duplicates_skipped={dup}")
    print(
        f"  velas cerradas en historico ANTES={base} DESPUES={despues} "
        f"(relleno del hueco: +{hueco} velas nuevas)"
    )

    sin_duplicados = filas == claves
    ok = boot > 0 and reconn >= 1 and dup >= 1 and sin_duplicados
    marca = "[OK]" if ok else "[FALLO]"
    print(
        f"  {marca} reconexion real (reconnections={reconn}); el motor rebootstrapeo "
        f"solo (bootstrap_candles={boot}); dedupo {dup} solapes; el historico NO tiene "
        f"ninguna vela duplicada (filas={filas} == claves distintas={claves}); relleno "
        f"{hueco} velas de hueco"
    )
    return ok


def _fase3(reader_db: Database) -> None:
    print("\n=== FASE 3: integridad ===")
    muestra = _muestra_cerradas(reader_db, _CLAVE_STR, 3)
    print("  hasta 3 velas cerradas del historico (open_time, close):")
    for open_time, close in muestra:
        print(f"    - open_time={open_time} close={close}")
    outbox = _contar_outbox(reader_db, _CLAVE_STR)
    print(f"  filas market.candle_closed en outbox para el stream: {outbox}")


def main() -> None:
    _exigir_env()
    ventana_s = float(os.environ.get(_WINDOW_ENV, str(_DEFAULT_WINDOW_S)))

    app_db = PsycopgDatabase(DbConfig.from_env(_solo(DSN_ENV_VAR)))
    migrations_db = PsycopgDatabase(DbConfig.migrations_from_env())
    ingestion_db = PsycopgDatabase(
        DbConfig(dsn=IngestionDbConfig.from_env(_solo(INGESTION_DSN_ENV_VAR)).dsn)
    )
    bus_config = RedisBusConfig.from_env(_solo(REDIS_URL_ENV_VAR))
    client = create_client(bus_config)
    bus = RedisEventBus(client, bus_config)

    # El connector se construye POR EL REGISTRO: resolve("bybit"). El isinstance da
    # acceso a las primitivas de operacion (force_reconnect_all, shutdown, metrics), que
    # no estan en el puerto. Que la maquinaria resuelva Bybit es parte del veredicto.
    connector = build_default_registry().resolve("bybit")
    if not isinstance(connector, BybitSpotConnector):
        msg = "el registro no devolvio un BybitSpotConnector para kind='bybit'."
        raise SystemExit(msg)

    observing = _ObservingSource(connector)
    writer = PostgresCandleWriter(ingestion_db)
    clock: Clock = SystemClock()
    engine = IngestionEngine(
        source=observing,
        writer=writer,
        bus=bus,
        clock=clock,
        component_source=_SOURCE,
    )
    scoped_db = TenantScopedDatabase(app_db)

    stream_ok = False
    fase2_ok = False
    tenant_id: UUID | None = None
    user_id: UUID | None = None
    try:
        print("Validacion en caliente REAL de Bybit v5 (T-03). Sandbox local.")
        user_id = _usuario(migrations_db, app_db)
        tenant_id = _tenant(migrations_db, app_db, user_id)
        _limpiar_intent(scoped_db, tenant_id, user_id)
        _limpiar_velas(migrations_db, _CLAVE_STR)

        catalogo = _CatalogoEnIngesta(ingestion_db)
        # Bybit usa el simbolo PEGADO: native_symbol=BTCUSDT (no identidad como OKX).
        catalogo.upsert("bybit", "spot", "BTC-USDT", "BTCUSDT", "active")
        _sembrar_intent(scoped_db, tenant_id, user_id, clock)
        print(f"  preparado: usuario {_EMAIL}, tenant {tenant_id}, interes en BTC-USDT")

        # sync_catalog con el connector REAL: upsert al catalogo Y set_symbol_map al
        # connector. Bybit SI implementa SymbolMapSink (simbolo pegado, como Binance):
        # sin ese mapa nativo->canonico el connector descartaria todas las velas.
        resultado = sync_catalog(connector, catalogo)
        print(
            f"  catalogo sincronizado: {resultado.active} activos, "
            f"{resultado.deactivated} delistados, "
            f"{resultado.not_representable} no representables"
        )

        observing.open(_CLAVE)
        stream_ok = _CLAVE_STR in observing.active()
        if not stream_ok:
            print("FALLO: no se pudo abrir el stream de BTC-USDT.", file=sys.stderr)
        else:
            print("stream abierto: BTC-USDT 1m")
            _drenar_durante(engine, observing, ventana_s, "FASE 1: streaming real")
            if observing.total_vistas == 0:
                print(
                    "FALLO: no llego NI UNA vela real en toda la ventana. El feed no "
                    "esta vivo (revisa red/geo/mapa de simbolos).",
                    file=sys.stderr,
                )
                stream_ok = False
            else:
                ultima = observing.ultima
                precio = "-" if ultima is None else ultima.close
                print(
                    f"  velas vistas en la ventana: {observing.total_vistas}; ultimo "
                    f"precio REAL de BTC-USDT: {precio}"
                )
                fase2_ok = _fase2(connector, engine, observing, migrations_db)
                _fase3(migrations_db)
    finally:
        connector.shutdown()
        print("\nCONECTOR DETENIDO (hilo de fondo parado).")
        try:
            if tenant_id is not None and user_id is not None:
                _limpiar_intent(scoped_db, tenant_id, user_id)
            _limpiar_velas(migrations_db, _CLAVE_STR)
            print("LIMPIEZA OK")
        finally:
            client.close()
            app_db.close()
            migrations_db.close()
            ingestion_db.close()
            print("CONEXIONES CERRADAS")

    if not (stream_ok and fase2_ok):
        print(
            "\nVALIDACION EN CALIENTE BYBIT: FALLIDA. Una validacion que miente "
            "es peor que ninguna.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print("\nVALIDACION EN CALIENTE BYBIT: OK. Streaming, reconexion y dedup, vivos.")


if __name__ == "__main__":
    main()
