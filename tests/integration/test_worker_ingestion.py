"""El worker de ingesta, de punta a punta (P07, hito: primer market.* end-to-end).

Con un FakeMarketDataSource CONTROLADO por el test (inyectado en build_context), sin
tocar la red. Demuestra EL CABLEADO COMPLETO: intent publico -> catalogo -> tick del
componente -> vela cerrada PERSISTIDA y encolada (atomico) y drenable al bus; vela
provisional DIRECTA al bus sin fila en el historico.

Es el hito de la ficha P07: "primer market.* end-to-end con datasource FAKE".
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from uuid import UUID, uuid4

import pytest
import redis

from ce_v5.entrypoints.worker_ingestion.catalog_sync import sync_catalog
from ce_v5.entrypoints.worker_ingestion.composition import build_context
from ce_v5.infra.bus_redis import RedisBusConfig, create_client
from ce_v5.infra.connectors.fake_market import FakeMarketDataSource
from ce_v5.infra.db.config import ForeignDsnInIngestionError
from ce_v5.infra.db.outbox_publisher import OutboxPublisher, topic_for
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.infra.db.tenancy import TenantScopedDatabase, provision_tenant_for_user
from source.families.market import (
    Instrument,
    IntentSourceType,
    MarketCandleEventType,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    RawCandle,
    StreamScope,
    SubscriptionIntent,
    Timeframe,
)

_DSN = os.environ.get("CE_V5_DATABASE_URL")
_URL = os.environ.get("CE_V5_REDIS_URL")
pytestmark = pytest.mark.skipif(
    _DSN is None or _URL is None,
    reason="requiere CE_V5_DATABASE_URL y CE_V5_REDIS_URL",
)

_OPEN = 1_784_073_600_000
_CLOSE = _OPEN + 59_999
_EVENT_TIME = _OPEN + 42
_AHORA = 1_784_073_600_000

_CLAVE = MarketStreamKey(
    exchange="binance",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.CANDLES,
    timeframe=Timeframe.M1,
)


def _environ_de_worker() -> dict[str, str]:
    """El entorno EXACTO que portaria un worker de ingesta real: SOLO su DSN de
    ingesta y Redis. NADA del de aplicacion ni del operador (la guardia 5.20 los
    rechazaria, y con razon).

    FALLA si falta el DSN de ingesta (regla 5.18): un test que se salta en silencio
    por una variable ausente es un test que no existe. El proceso de pytest lleva
    TODOS los DSN; aqui se filtra a mano lo que el worker portaria.
    """
    ingestion_dsn = os.environ.get("CE_V5_INGESTION_DATABASE_URL")
    redis_url = os.environ.get("CE_V5_REDIS_URL")
    if not ingestion_dsn:
        pytest.fail(
            "Falta CE_V5_INGESTION_DATABASE_URL: el worker de ingesta no puede "
            "cablearse sin su DSN. No se salta (regla 5.18): ponla en el entorno."
        )
    if not redis_url:
        pytest.fail("Falta CE_V5_REDIS_URL para el worker de ingesta (regla 5.18).")
    entorno = {
        "CE_V5_INGESTION_DATABASE_URL": ingestion_dsn,
        "CE_V5_REDIS_URL": redis_url,
    }
    password = os.environ.get("CE_V5_INGESTION_DB_PASSWORD")
    if password:
        entorno["CE_V5_INGESTION_DB_PASSWORD"] = password
    return entorno


@pytest.fixture
def limpiar_market(migrator_db: PsycopgDatabase) -> Iterator[None]:
    def _wipe() -> None:
        with migrator_db.transaction() as session:
            session.execute("DELETE FROM market_candle")
            session.execute("DELETE FROM market_instrument")
            session.execute("DELETE FROM outbox")

    _wipe()
    yield
    _wipe()


def _fake_con_btc() -> FakeMarketDataSource:
    return FakeMarketDataSource(
        instruments=[
            Instrument("binance", "spot", "BTC-USDT", "BTCUSDT", active=True),
        ],
        timeframes=[Timeframe.M1],
    )


def _vela(**overrides: object) -> RawCandle:
    base: dict[str, object] = {
        "exchange": "binance",
        "market_type": "spot",
        "symbol": "BTC-USDT",
        "timeframe": "1m",
        "open_time_ms": _OPEN,
        "close_time_ms": _CLOSE,
        "open": "100",
        "high": "110",
        "low": "95",
        "close": "105",
        "volume": "1",
        "is_closed": True,
        "event_time_ms": _EVENT_TIME,
    }
    base.update(overrides)
    return RawCandle(**base)  # type: ignore[arg-type]


def _sembrar_intent_publico(
    app_db: PsycopgDatabase, user_id: UUID, tenant_id: UUID
) -> None:
    scoped_db = TenantScopedDatabase(app_db)
    with scoped_db.transaction(user_id) as scoped:
        from ce_v5.infra.db.market_store import PostgresIntentStore

        PostgresIntentStore(scoped).insert(
            SubscriptionIntent(
                intent_id=uuid4(),
                tenant_id=tenant_id,
                user_id=user_id,
                stream_scope=StreamScope.PUBLIC_MARKET,
                stream_key=_CLAVE,
                source_type=IntentSourceType.WIDGET,
                source_ref="w1",
                created_at=_AHORA,
                updated_at=_AHORA,
            )
        )


def _tenant_de(app_db: PsycopgDatabase, user_id: UUID) -> UUID:
    scoped_db = TenantScopedDatabase(app_db)
    with scoped_db.transaction(user_id) as scoped:
        return scoped.context.tenant_id


def _contar(db: PsycopgDatabase, sql: str) -> int:
    with db.transaction() as session:
        row = session.fetchone(sql)
    assert row is not None
    valor = row[0]
    assert isinstance(valor, int)
    return valor


def test_primer_market_end_to_end_con_datasource_fake(
    app_db: PsycopgDatabase,
    ingestion_db: PsycopgDatabase,
    crear_usuario: Callable[[], UUID],
    limpiar_market: None,
) -> None:
    # 1) Un sujeto pide BTC-USDT 1m (interes PUBLICO, rol de aplicacion).
    user = crear_usuario()
    provision_tenant_for_user(app_db, user)
    tenant = _tenant_de(app_db, user)
    _sembrar_intent_publico(app_db, user, tenant)

    # 2) El worker se cablea con el fake CONTROLADO por el test (sin red) y con el
    #    entorno LIMPIO que tendria el worker real: solo su DSN de ingesta y Redis. El
    #    proceso de pytest lleva tambien el DSN de app y el de operador, y la guardia
    #    5.20 los rechazaria (con razon); por eso se le da el entorno restringido.
    fake = _fake_con_btc()
    context = build_context(datasource=fake, environ=_environ_de_worker())
    try:
        # 3) sync_catalog ANTES del primer reconcile: sin catalogo, el connector real
        #    descartaria todo. El fake ya trae BTC-USDT; se sube a market_instrument.
        resultado = sync_catalog(context.datasource, context.catalog)
        assert resultado.active == 1

        context.supervisor.initialize(context.instance_id)
        context.supervisor.start(context.instance_id)

        # 4a) Una vela CERRADA emitida por el fake. Tras un tick (reconcile abre el
        #     stream segun la demanda; drain procesa la vela), acaba PERSISTIDA y
        #     encolada en la MISMA transaccion.
        fake.emit(_vela(is_closed=True))
        context.component.tick()

        assert _contar(ingestion_db, "SELECT count(*) FROM market_candle") == 1
        assert (
            _contar(
                ingestion_db,
                "SELECT count(*) FROM outbox WHERE event_type = 'market.candle_closed'",
            )
            == 1
        )

        # 4b) Una vela PROVISIONAL. Va DIRECTA al bus, NO se persiste (no es historia).
        antes = _contar(ingestion_db, "SELECT count(*) FROM market_candle")
        fake.emit(_vela(is_closed=False, open_time_ms=_OPEN + 60_000))
        context.component.tick()
        assert _contar(ingestion_db, "SELECT count(*) FROM market_candle") == antes

        # 5) PRIMER market.* END TO END: la cerrada, drenada de la outbox, llega al bus
        #    y pasa la validacion de contrato (CA-06).
        assert _URL is not None
        config = RedisBusConfig(url=_URL, namespace="test-" + uuid4().hex)
        client: redis.Redis = create_client(config)
        try:
            from ce_v5.infra.bus_redis import RedisEventBus

            bus = RedisEventBus(client, config)
            publicados = OutboxPublisher(db=ingestion_db, bus=bus).drain_once()
            assert publicados == 1

            topic = topic_for(MarketCandleEventType.CANDLE_CLOSED.value)
            bus.ensure_group(topic, "g1")
            recibidos = bus.poll(topic, "g1", "c1", max_messages=10, block_ms=0)
            assert len(recibidos) == 1
            envelope = json.loads(recibidos[0].message.envelope)
            assert envelope["event_type"] == "market.candle_closed"
            assert envelope["scope"] == "public_market"
            assert envelope["tenant_id"] is None
            assert envelope["event_time"] == _EVENT_TIME  # del exchange
            print(
                "\n[HITO P07] primer market.* end-to-end: "
                "vela cerrada de un datasource FAKE, persistida + encolada (atomico) "
                "y publicada al bus, sin tocar la red.",
                flush=True,
            )
        finally:
            for key in client.scan_iter(match=f"{config.namespace}:*"):
                client.delete(key)
            client.close()
    finally:
        context.supervisor.stop(context.instance_id)
        context.supervisor.unload(context.instance_id)
        context.close()


def test_la_guardia_5_20_aborta_con_un_dsn_ajeno() -> None:
    # Convierte el susto de B9 en una GARANTIA verificada: si el entorno del worker
    # trae el DSN de OPERADOR, build_context NO ARRANCA. Un worker de ingesta no porta
    # la credencial capaz de operar kill switches; la separacion la hace cumplir el
    # CODIGO, no un documento. Este test no toca la red ni la base: aborta antes.
    entorno_contaminado = {
        "CE_V5_INGESTION_DATABASE_URL": "postgresql://ce_v5_ingestion:x@localhost/ce_v5",
        "CE_V5_OPERATOR_DATABASE_URL": "postgresql://ce_v5_operator:x@localhost/ce_v5",
        "CE_V5_REDIS_URL": "redis://localhost:6379/0",
    }
    with pytest.raises(ForeignDsnInIngestionError):
        build_context(datasource=FakeMarketDataSource(), environ=entorno_contaminado)


def test_la_guardia_5_20_aborta_con_el_dsn_de_aplicacion() -> None:
    # La otra mitad de la guardia BIDIRECCIONAL: tampoco porta el DSN de la aplicacion.
    entorno_contaminado = {
        "CE_V5_INGESTION_DATABASE_URL": "postgresql://ce_v5_ingestion:x@localhost/ce_v5",
        "CE_V5_DATABASE_URL": "postgresql://ce_v5_app:x@localhost/ce_v5",
        "CE_V5_REDIS_URL": "redis://localhost:6379/0",
    }
    with pytest.raises(ForeignDsnInIngestionError):
        build_context(datasource=FakeMarketDataSource(), environ=entorno_contaminado)
