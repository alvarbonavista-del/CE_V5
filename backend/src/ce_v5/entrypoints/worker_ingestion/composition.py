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

from ce_v5.components.market_ingestor_public import PublicMarketIngestorComponent
from ce_v5.core.bus import EventBus
from ce_v5.core.clock import Clock, SystemClock
from ce_v5.core.component import ComponentDefinition, LifecycleScope, Supervisor
from ce_v5.core.discovery import discover, import_entrypoint
from ce_v5.infra.bus_redis import RedisBusConfig, RedisEventBus, create_client
from ce_v5.infra.connectors.binance.connector import BinanceSpotConnector
from ce_v5.infra.connectors.fake_market import FakeMarketDataSource
from ce_v5.infra.db.config import DbConfig, IngestionDbConfig
from ce_v5.infra.db.market_candles import PostgresCandleWriter
from ce_v5.infra.db.market_store import (
    PostgresInstrumentCatalog,
    PostgresPublicDemand,
)
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.platform.market.datasource import MarketDataSourcePort
from ce_v5.platform.market.ingestor import IngestionEngine
from ce_v5.platform.market.subscriptions import SubscriptionManager

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
    subscription_manager: SubscriptionManager
    datasource: MarketDataSourcePort
    catalog: _CatalogOnDb
    bus: EventBus
    database: PsycopgDatabase

    def close(self) -> None:
        """Cierra las conexiones. Idempotente: se puede llamar en el apagado limpio."""
        self.database.close()


def _build_datasource(
    catalog: _CatalogOnDb,
    injected: MarketDataSourcePort | None,
) -> MarketDataSourcePort:
    """El datasource, SELECCIONABLE POR ENTORNO (CE_V5_MARKET_DATASOURCE).

    - 'binance' (por defecto): el connector REAL. Se le pasa el mapa nativo->canonico
      construido desde el catalogo ya sincronizado; sin el, descartaria todo.
    - 'fake': un FakeMarketDataSource vacio, SOLO para arrancar el proceso SIN RED
      (humo, observar el arranque). JAMAS produce datos reales.

    Los tests inyectan su propio fake controlado; el proceso real no inyecta nada.
    """
    if injected is not None:
        return injected

    del catalog  # el mapa nativo->canonico lo puebla __main__ tras sync_catalog.
    kind = os.environ.get(_DATASOURCE_ENV, "binance")
    if kind == "fake":
        # Arranque local sin red. No trae datos: es para ver que el proceso levanta.
        return FakeMarketDataSource()
    if kind == "binance":
        return BinanceSpotConnector()
    msg = (
        f"{_DATASOURCE_ENV}={kind!r} no reconocido. Validos: 'binance' (real) o "
        "'fake' (arranque local sin red)."
    )
    raise ValueError(msg)


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
        subscription_manager=subscription_manager,
        datasource=source,
        catalog=catalog_adapter,
        bus=bus,
        database=database,
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
