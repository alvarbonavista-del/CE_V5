"""Validacion en caliente REAL de P07 (B12b): streaming, reconexion, bootstrap, dedup.

Conecta el WebSocket REAL de Binance, recibe velas REALES y las convierte en hechos del
sistema contra la base LOCAL de juguete. Demuestra lo que un fake NO puede: velas vivas
llegando por el socket y la deduplicacion del bootstrap REST tras una reconexion.

SANDBOX LOCAL, MARKET DATA PUBLICA REAL, JAMAS DINERO. La regla "nunca dinero real" es
de ejecucion/ordenes (M5, P10b); esto es dato de mercado publico. Aun asi: usuario de
demo con email fijo, y se limpian sus intents y las velas de ese stream al terminar.

REGLA DURA (leccion del bloqueo de terminal): el connector real usa un HILO DE FONDO.
Este arnes cierra el datasource (connector.shutdown()) en un finally, PASE LO QUE PASE,
y es ACOTADO EN EL TIEMPO: una ventana fija (CE_V5_LIVE_WINDOW_S, 75 s por defecto) y
termina SOLO. Nada de bucle infinito. Los lectores del connector son daemon: shutdown()
les senala el fin y, por ser daemon, no pueden colgar el proceso al salir aunque uno
quede bloqueado en un recv; por eso no hace falta join con timeout.

SOBRE EL BOOTSTRAP: el auto-bootstrap tras reconexion esta CABLEADO (P07-R1). Este arnes
NO reinyecta nada: fuerza una reconexion REAL (connector.force_reconnect_all cierra los
sockets) y el MOTOR se rebootstrapea SOLO -- el conector marca los streams reconectados
(drain_reconnected) y el motor, en su drain_once, dispara el fetch_recent por el mismo
camino de normalizacion+dedup. Se comprueba con dato REAL que rellena el hueco (velas
nuevas) sin duplicar (los solapes se dedupan; el historico no tiene ninguna vela
repetida).

GUARDIA 5.20 (leccion de B9): un solo proceso porta varios roles (app, migraciones,
ingesta) porque esta validacion exige el sistema entero. Cada cargador ve el sub-entorno
con SOLO su DSN (_solo), el mismo entorno que portaria el proceso real de ese rol.
Limpiar las velas exige el rol OWNER (migraciones): market_candle es append-only y el
rol de ingesta no puede borrar (regla dura del historico).

Uso: python tools/validate_p07_binance_live.py
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
from ce_v5.infra.bus_redis import (  # noqa: E402
    RedisBusConfig,
    RedisEventBus,
    create_client,
)
from ce_v5.infra.bus_redis.config import REDIS_URL_ENV_VAR  # noqa: E402
from ce_v5.infra.connectors.binance.connector import BinanceSpotConnector  # noqa: E402
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

_EMAIL = "hot-p07-live@ejemplo.test"
_PASSWORD_HASH = "hash-de-prueba-no-es-argon2"
_SOURCE = "worker_ingestion"

_CLAVE = MarketStreamKey(
    exchange="binance",
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
# Ventana de drenaje tras forzar la reconexion: da tiempo a que el lector reconecte con
# backoff, marque el stream y el motor dispare su bootstrap en un drain_once.
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
            "FALLO: faltan variables obligatorias para la validacion en caliente B12b: "
            f"{', '.join(faltan)}. Esta validacion exige el sistema completo (app + "
            "migraciones + ingesta + redis): no se salta, se configura el entorno.",
            file=sys.stderr,
        )
        raise SystemExit(2)


# -- Fuentes de datos para el engine -------------------------------------------


class _ObservingSource:
    """Decorador TRANSPARENTE sobre el connector real: delega TODO y ademas recuerda la
    ultima vela vista en poll(), para imprimir un precio real y vivo. No cambia el
    comportamiento: el engine sigue hablando con el connector real.
    """

    def __init__(self, inner: BinanceSpotConnector) -> None:
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


# -- Catalogo con el rol de INGESTA (mismo patron que _CatalogOnDb) ------------


class _CatalogoEnIngesta:
    """Catalogo real por-sesion con el rol de INGESTA. La ESCRITURA del catalogo solo
    la permite ese rol (regla 5.20). Satisface CatalogWriterPort (upsert + deactivate).
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


# -- Seed / consultas / limpieza ----------------------------------------------


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
    market_candle es append-only y el rol de ingesta no puede borrar (historico).
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
    """(filas closed, idempotency_key DISTINTAS) del stream. La prueba FUERTE de no
    duplicacion: si son iguales, no hay ninguna vela repetida en el historico.
    """
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


# -- Impresion de metricas -----------------------------------------------------


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


# -- Fases ---------------------------------------------------------------------


def _fase2(
    connector: BinanceSpotConnector,
    engine: IngestionEngine,
    observing: _ObservingSource,
    reader_db: Database,
) -> bool:
    print("\n=== FASE 2: reconexion REAL + bootstrap AUTONOMO del motor ===")

    # El arnes NO reinyecta nada: fuerza una reconexion REAL y el MOTOR se rebootstrapea
    # solo. El conector cierra sus sockets, los lectores reconectan con backoff y marcan
    # sus streams (drain_reconnected); el motor, en su drain_once, dispara el
    # fetch_recent por el mismo camino de dedup. El historico no crece por duplicados
    # (los del bootstrap se dedupan contra lo ya persistido en la Fase 1).
    base = _contar_cerradas(reader_db, _CLAVE_STR)
    reconn_antes = connector.metrics.reconnections
    boot_antes = engine.metrics.bootstrap_candles
    dup_antes = engine.metrics.duplicates_skipped
    print(f"  velas cerradas en historico ANTES: {base}")

    cerradas = connector.force_reconnect_all()
    print(f"  force_reconnect_all: cerro {cerradas} conexion(es)")

    # Drenaje acotado: el lector reconecta (backoff) y el motor bootstrapea SOLO.
    _drenar_durante(
        engine, observing, _RECONNECT_DRAIN_S, "FASE 2: drenaje tras reconexion"
    )

    despues = _contar_cerradas(reader_db, _CLAVE_STR)
    reconn = connector.metrics.reconnections - reconn_antes
    boot = engine.metrics.bootstrap_candles - boot_antes
    dup = engine.metrics.duplicates_skipped - dup_antes
    filas, claves = _filas_y_claves_distintas(reader_db, _CLAVE_STR)
    hueco = despues - base  # velas NUEVAS de relleno: ESPERADO y correcto (ADR-014).

    print(f"  reconnections={reconn} bootstrap_candles={boot} duplicates_skipped={dup}")
    # Crecer por velas NUEVAS es el RELLENO DEL HUECO, no un fallo: se dice asi.
    print(
        f"  velas cerradas en historico ANTES={base} DESPUES={despues} "
        f"(relleno del hueco: +{hueco} velas nuevas)"
    )

    # NO DUPLICACION (la prueba fuerte): cada vela closed tiene su idempotency_key
    # unica; filas == claves distintas => ninguna vela repetida en el historico.
    sin_duplicados = filas == claves
    ok = (
        boot > 0  # el MOTOR disparo el bootstrap solo (el arnes no llamo fetch_recent)
        and reconn >= 1  # hubo una reconexion real (contador honesto tras FIX 1)
        and dup >= 1  # el bootstrap absorbio al menos un solape (dedup real)
        and sin_duplicados  # el historico no tiene ninguna vela duplicada
    )
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


# -- Orquestacion --------------------------------------------------------------


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

    connector = BinanceSpotConnector()
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
        # PREPARACION (idempotente, juguete).
        print("Validacion en caliente REAL de Binance (B12b). Sandbox local.")
        user_id = _usuario(migrations_db, app_db)
        tenant_id = _tenant(migrations_db, app_db, user_id)
        _limpiar_intent(scoped_db, tenant_id, user_id)
        _limpiar_velas(migrations_db, _CLAVE_STR)

        catalogo = _CatalogoEnIngesta(ingestion_db)
        catalogo.upsert("binance", "spot", "BTC-USDT", "BTCUSDT", "active")
        _sembrar_intent(scoped_db, tenant_id, user_id, clock)
        print(f"  preparado: usuario {_EMAIL}, tenant {tenant_id}, interes en BTC-USDT")

        # sync_catalog con el connector REAL: upsert al catalogo Y set_symbol_map al
        # connector (el cableado de B12b-1). Sin el mapa, el connector descartaria todo.
        resultado = sync_catalog(connector, catalogo)
        print(
            f"  catalogo sincronizado: {resultado.active} activos, "
            f"{resultado.deactivated} delistados, "
            f"{resultado.not_representable} no representables"
        )

        # FASE 1: streaming real.
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
                    "esta vivo (revisa red/geo).",
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
        # REGLA DURA: parar el hilo de fondo del connector SIEMPRE. Los lectores son
        # daemon: shutdown() les senala el fin y, por ser daemon, no cuelgan el proceso
        # al salir aunque uno quede en un recv. No hace falta join con timeout.
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
            "\nVALIDACION EN CALIENTE B12b: FALLIDA. Una validacion que miente es peor "
            "que ninguna.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print("\nVALIDACION EN CALIENTE B12b: OK. Streaming, reconexion y dedup, vivos.")


if __name__ == "__main__":
    main()
