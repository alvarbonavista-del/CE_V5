"""Composition root del worker de ingesta (ADR-002, DOC_ESTRUCTURA sec.6).

El worker de ingesta es un PROCESO PROPIO: aqui, y solo aqui, se cablean los adapters
concretos (PostgreSQL con el rol de INGESTA, Redis, el connector del exchange) y se
inyecta el cerebro en el Componente descubierto por carpeta. El resto del codigo
depende de puertos.

GUARDIA DE ARRANQUE (regla 5.20): IngestionDbConfig.from_env ABORTA si en el entorno
aparece el DSN de la APLICACION o el del OPERADOR. Un worker de ingesta no porta
credenciales que su funcion no necesita, y quien lo hace cumplir es el CODIGO.

NADA DE BUCLES EN LA CONSTRUCCION: como la API, los bucles se arrancan en __main__, no
en build_context. Un hilo de fondo escondido en la construccion es un hilo que los
tests no controlan.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ce_v5.components.market_ingestor_public import PublicMarketIngestorComponent
from ce_v5.core.bus import EventBus
from ce_v5.core.clock import Clock, SystemClock
from ce_v5.core.component import ComponentDefinition, LifecycleScope, Supervisor
from ce_v5.core.discovery import discover, import_entrypoint
from ce_v5.entrypoints.worker_ingestion.connector_registry import build_default_registry
from ce_v5.infra.bus_redis import RedisBusConfig, RedisEventBus, create_client
from ce_v5.infra.db.config import DbConfig, IngestionDbConfig
from ce_v5.infra.db.market_candles import PostgresCandleWriter
from ce_v5.infra.db.market_orderbook import PostgresOrderbookWriter
from ce_v5.infra.db.market_store import (
    PostgresInstrumentCatalog,
    PostgresPublicDemand,
)
from ce_v5.infra.db.market_trades import PostgresTradeWriter
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.platform.market.datasource import MarketDataSourcePort
from ce_v5.platform.market.ingestor import IngestionEngine
from ce_v5.platform.market.orderbook_ingestor import OrderbookIngestionEngine
from ce_v5.platform.market.orderbook_snapshot import OrderbookSnapshotEngine
from ce_v5.platform.market.orderbook_source import OrderbookDataSourcePort
from ce_v5.platform.market.subscriptions import SubscriptionManager
from ce_v5.platform.market.trade_ingestor import TradeIngestionEngine
from ce_v5.platform.market.trade_source import TradeDataSourcePort

_COMPONENTS_ROOT = Path(__file__).resolve().parents[2] / "components"
_INGESTOR_ID = "market_ingestor_public"
_DATASOURCE_ENV = "CE_V5_MARKET_DATASOURCE"


class ComponentNotFoundError(RuntimeError):
    """El discovery no encontro el componente que el worker necesita cablear."""


@dataclass(frozen=True, slots=True)
class IngestionContext:
    """Todo lo que el bucle del proceso necesita, y lo necesario para apagarlo."""

    supervisor: Supervisor
    instance_id: str
    component: PublicMarketIngestorComponent
    engine: IngestionEngine
    # None cuando el feed cableado NO sirve trades (ver _as_trade_source). El bucle lo
    # comprueba y lo DICE al arrancar: una degradacion declarada, no un motor mudo.
    trade_engine: TradeIngestionEngine | None
    # None cuando el feed NO sirve libro (ver _as_orderbook_source), como trades: el
    # arranque lo DICE. El motor de snapshot solo se construye si hay motor de libro.
    orderbook_engine: OrderbookIngestionEngine | None
    orderbook_snapshot: OrderbookSnapshotEngine | None
    subscription_manager: SubscriptionManager
    datasource: MarketDataSourcePort
    catalog: _CatalogOnDb
    bus: EventBus
    database: PsycopgDatabase
    # El MISMO reloj inyectado en los motores (SystemClock en produccion). El bucle lo
    # usa para el trigger de FRONTERA por reloj de barra (opcion 3) y la cadencia de
    # MUESTRA: disparo determinista y reproducible (un SimulatedClock lo reproduce).
    clock: Clock

    def close(self) -> None:
        """Cierra las conexiones. Idempotente: se puede llamar en el apagado limpio."""
        self.database.close()


def _build_datasource(
    catalog: _CatalogOnDb,
    injected: MarketDataSourcePort | None,
) -> MarketDataSourcePort:
    """El datasource, SELECCIONABLE POR ENTORNO (CE_V5_MARKET_DATASOURCE).

    Los tests inyectan su propio fake CONTROLADO; el proceso real no inyecta nada y la
    seleccion se resuelve por el ConnectorRegistry (registro minimo por convencion,
    T-03-A): cada adaptador aporta su 'kind' y su factory en su propia carpeta, y aqui
    NO se conoce ninguna clase concreta ni se ramifica por exchange. Un 'kind'
    desconocido FALLA FUERTE (jamas un default silencioso).
    """
    if injected is not None:
        return injected
    del catalog  # el mapa nativo->canonico lo puebla __main__ tras sync_catalog.
    kind = os.environ.get(_DATASOURCE_ENV, "binance")
    return build_default_registry().resolve(kind)


# Los metodos que un feed debe servir para que se le pueda pedir trades. open/close/
# active/drain_reconnected ya los exige el puerto de velas: aqui solo van los propios.
_TRADE_PORT_METHODS = ("poll_trades", "backfill_after_reconnect")
# Idem para el libro: seed (la foto) y poll_deltas son los propios; el resto
# del puerto (open/close/active/drain_reconnected) ya lo exige el de velas.
_ORDERBOOK_PORT_METHODS = ("seed", "poll_deltas")


def _as_orderbook_source(
    source: MarketDataSourcePort,
) -> OrderbookDataSourcePort | None:
    """EL MISMO feed visto por su cara de LIBRO, si de verdad lo sirve.

    Mismo criterio que _as_trade_source y por el mismo motivo (Central b-i): el conector
    real multiplexa velas, trades y libro sobre la MISMA conexion, asi que el motor del
    libro recibe el objeto que ya existe -- NO un segundo feed ni un segundo socket. Un
    feed que no sirve libro (el fake CONTROLADO de los tests, o un adaptador sin libro)
    devuelve None y el worker corre sin ese motor, DICHO en el arranque; fingir un motor
    sobre un feed mudo daria un stream con pinta de sano sin ingerir un solo delta.
    """
    if not all(
        callable(getattr(source, nombre, None)) for nombre in _ORDERBOOK_PORT_METHODS
    ):
        return None
    return cast(OrderbookDataSourcePort, source)


def _as_trade_source(source: MarketDataSourcePort) -> TradeDataSourcePort | None:
    """EL MISMO feed visto por su otra cara, si de verdad sirve trades.

    Aqui NO se construye un segundo feed, y ese es el punto de la tanda (Central Q3):
    el conector real multiplexa velas y trades sobre la MISMA conexion, asi que el
    motor de trades recibe el objeto que ya existe. Dos feeds serian dos sockets contra
    el mismo par y el doble de gasto contra el limite de conexiones por IP.

    Un feed que solo sirve velas -- el fake CONTROLADO de los tests, o un exchange cuyo
    adaptador de trades aun no existe -- devuelve None, y el worker corre sin motor de
    trades. NO es un default silencioso: es la unica respuesta honesta a "este feed no
    tiene trades", y el arranque lo imprime. Fingir un motor sobre un feed que no los
    sirve daria un stream mudo con pinta de sano, que es justo lo que
    TradeDataSourcePort existe para evitar.
    """
    if not all(
        callable(getattr(source, nombre, None)) for nombre in _TRADE_PORT_METHODS
    ):
        return None
    # El puerto se satisface por FORMA (Protocol estructural); mypy no puede probarlo
    # desde el tipo declarado del feed de velas, y la comprobacion de arriba es la que
    # lo garantiza en ejecucion.
    return cast(TradeDataSourcePort, source)


def build_context(
    *,
    datasource: MarketDataSourcePort | None = None,
    environ: Mapping[str, str] | None = None,
) -> IngestionContext:
    """Cablea todo lo de P07 y devuelve el contexto. NO arranca ningun bucle.

    ``datasource`` permite a los tests inyectar un fake CONTROLADO sin tocar la red;
    el proceso real lo deja en None y se selecciona por entorno.

    ``environ`` se inyecta para que un test pueda dar el entorno LIMPIO que tendria el
    worker real (SOLO su DSN de ingesta, nunca el de app u operador). El proceso real
    lo deja en None -> os.environ, y sigue protegido por la misma guardia 5.20: no es
    una puerta trasera, es el mismo entorno restringido que el worker de verdad porta.
    """
    # GUARDIA 5.20: aborta (ForeignDsnInIngestionError) si el entorno trae el DSN de la
    # aplicacion o el del operador. Un worker de ingesta no porta lo que no necesita.
    ingestion_dsn = IngestionDbConfig.from_env(environ).dsn
    database = PsycopgDatabase(DbConfig(dsn=ingestion_dsn))
    bus_config = RedisBusConfig.from_env(environ)
    bus = RedisEventBus(create_client(bus_config), bus_config)
    clock: Clock = SystemClock()

    catalog_adapter = _CatalogOnDb(database)
    source = _build_datasource(catalog_adapter, datasource)

    demand = _PublicDemandOnDb(database)
    writer = PostgresCandleWriter(database)
    subscription_manager = SubscriptionManager(
        demand=demand, controller=source, clock=clock
    )
    engine = IngestionEngine(
        source=source,
        writer=writer,
        bus=bus,
        clock=clock,
        component_source=_INGESTOR_ID,
    )

    # Motor de TRADES sobre el MISMO conector y la MISMA conexion a la base, con el
    # MISMO rol ce_v5_ingestion (regla 5.20): un solo proceso, una sola credencial.
    # Sin bus: los trades NO se publican (I-02); lo que se publicara por barra es el
    # footprint.
    trade_source = _as_trade_source(source)
    trade_engine = (
        None
        if trade_source is None
        else TradeIngestionEngine(
            source=trade_source,
            writer=PostgresTradeWriter(database),
        )
    )

    # Motor del LIBRO L2 y motor de SNAPSHOT sobre el MISMO conector, la MISMA base y el
    # MISMO rol ce_v5_ingestion (regla 5.20), como el de trades (Central b-i). El writer
    # de infra cumple a la vez el puerto de escritura y el de lectura; ambos
    # None si el feed no sirve libro: una degradacion DECLARADA, no un motor mudo.
    orderbook_source = _as_orderbook_source(source)
    orderbook_engine: OrderbookIngestionEngine | None = None
    orderbook_snapshot: OrderbookSnapshotEngine | None = None
    if orderbook_source is not None:
        orderbook_writer = PostgresOrderbookWriter(database)
        orderbook_engine = OrderbookIngestionEngine(
            orderbook_source,
            orderbook_writer,
            clock,
            component_source=_INGESTOR_ID,
        )
        orderbook_snapshot = OrderbookSnapshotEngine(
            orderbook_writer,
            orderbook_writer,
            clock,
            component_source=_INGESTOR_ID,
        )

    definition, component = _discover_and_wire(subscription_manager, engine, source)

    # El feed PUBLICO no tiene sujeto: la instancia es GLOBAL (ADR-011). El connector
    # PRIVADO por-usuario NO se instancia aqui: en P07 no hay usuarios BYOC, e
    # instanciarlo con un usuario inventado seria fabricar un sujeto que no existe. Su
    # instanciacion por-usuario llega con P10b.
    supervisor = Supervisor(bus, clock, source="worker_ingestion")
    instance = supervisor.register(
        definition,
        component,
        scope=LifecycleScope.GLOBAL,
        instance_id=_INGESTOR_ID,
    )

    return IngestionContext(
        supervisor=supervisor,
        instance_id=instance.instance_id,
        component=component,
        engine=engine,
        trade_engine=trade_engine,
        orderbook_engine=orderbook_engine,
        orderbook_snapshot=orderbook_snapshot,
        subscription_manager=subscription_manager,
        datasource=source,
        catalog=catalog_adapter,
        bus=bus,
        database=database,
        clock=clock,
    )


def _discover_and_wire(
    subscription_manager: SubscriptionManager,
    engine: IngestionEngine,
    source: MarketDataSourcePort,
) -> tuple[ComponentDefinition, PublicMarketIngestorComponent]:
    """Discovery REAL: encuentra el ingestor publico e inyecta su cerebro.

    El discovery valida el manifest y RESUELVE el entrypoint (no lo invoca); quien
    construye, con las dependencias, es este composition root. Devuelve la definicion
    (para registrarla con su manifest real) y el componente ya cableado.
    """
    result = discover(_COMPONENTS_ROOT, import_entrypoint)
    for definition in result.registered:
        if definition.manifest.id != _INGESTOR_ID:
            continue
        entrypoint = definition.manifest.entrypoint
        assert entrypoint is not None  # discovery ya lo exigio
        build = import_entrypoint(entrypoint)
        component = build(  # type: ignore[operator]
            subscription_manager=subscription_manager,
            engine=engine,
            source=source,
        )
        assert isinstance(component, PublicMarketIngestorComponent)
        return definition, component
    msg = (
        f"el discovery no encontro el componente {_INGESTOR_ID!r} bajo "
        f"{_COMPONENTS_ROOT}: no se puede cablear el worker de ingesta."
    )
    raise ComponentNotFoundError(msg)


class _CatalogOnDb:
    """PostgresInstrumentCatalog abre su propia transaccion por llamada; este envoltorio
    la abre y delega. Mantiene el connector desacoplado del ciclo de vida de la sesion.
    """

    def __init__(self, database: PsycopgDatabase) -> None:
        self._database = database

    def has_exchange(self, exchange: str) -> bool:
        with self._database.transaction() as session:
            return PostgresInstrumentCatalog(session).has_exchange(exchange)

    def exists(self, exchange: str, market_type: str, symbol: str) -> bool:
        with self._database.transaction() as session:
            return PostgresInstrumentCatalog(session).exists(
                exchange, market_type, symbol
            )

    def is_tradable(self, exchange: str, market_type: str, symbol: str) -> bool:
        with self._database.transaction() as session:
            return PostgresInstrumentCatalog(session).is_tradable(
                exchange, market_type, symbol
            )

    def native_symbol(self, exchange: str, market_type: str, symbol: str) -> str | None:
        with self._database.transaction() as session:
            return PostgresInstrumentCatalog(session).native_symbol(
                exchange, market_type, symbol
            )

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


class _PublicDemandOnDb:
    """PostgresPublicDemand con su propia transaccion por snapshot."""

    def __init__(self, database: PsycopgDatabase) -> None:
        self._database = database

    def snapshot(self) -> dict[str, int]:
        with self._database.transaction() as session:
            return PostgresPublicDemand(session).snapshot()
