"""Tests de integracion de los adapters SQL de market (ADR-014, ADR-011, 5.20).

Contra PostgreSQL REAL: el catalogo, los intereses bajo RLS y la ventanilla agregada
vista por el rol de INGESTA. Aqui se demuestra a nivel de DATOS lo que ADR-014 exige:
dos tenants que piden el MISMO flujo suman UNA sola clave, y el stream sigue vivo
mientras quede alguien interesado.

Base de JUGUETE: nunca datos reales (DOC_ENTREGABLES sec.5).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from uuid import UUID, uuid4

import pytest

from ce_v5.infra.db.market_store import (
    PostgresInstrumentCatalog,
    PostgresIntentStore,
    PostgresPublicDemand,
)
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.infra.db.tenancy import TenantScopedDatabase, provision_tenant_for_user
from source.families.market import (
    IntentSourceType,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    StreamScope,
    SubscriptionIntent,
    Timeframe,
)

_DSN = os.environ.get("CE_V5_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    _DSN is None, reason="requiere CE_V5_DATABASE_URL (PostgreSQL local)"
)

# Instante del reloj SIMULADO: gobierna lo que produce NUESTRO codigo (created_at,
# updated_at). Es determinista y por eso las comparaciones campo a campo funcionan.
_AHORA = 1_784_073_600_000

# Instantes de tiempo REAL, para lo que decide el now() de PostgreSQL. La caducidad
# NO la juzga nuestro reloj: la juzga el motor dentro de la ventanilla. Por eso un
# "caducado" hay que fabricarlo con una fecha pasada DE VERDAD, no restandole 1 ms al
# reloj simulado (que apunta a 2026 y por tanto esta en el FUTURO real).
_PASADO_REAL = 1_700_000_000_000  # noviembre de 2023
_FUTURO_REAL = 4_102_444_800_000  # enero de 2100


@pytest.fixture
def limpiar_market(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """Catalogo y velas se limpian aqui; los intereses caen por cascada al borrar
    app_user/tenant en la fixture autouse de identidad.

    market_instrument y market_candle NO tienen FK a nadie: sin esta limpieza se
    acumularian entre ejecuciones. Es el mismo defecto que dejo 837 tenants huerfanos
    en P06b; no se repite.
    """

    def _wipe() -> None:
        with migrator_db.transaction() as session:
            session.execute("DELETE FROM market_candle")
            session.execute("DELETE FROM market_instrument")

    _wipe()
    yield
    _wipe()


def _clave(
    symbol: str = "BTC-USDT", timeframe: Timeframe = Timeframe.M1
) -> MarketStreamKey:
    return MarketStreamKey(
        exchange="binance",
        market_type=MarketType.SPOT,
        symbol=symbol,
        data_kind=MarketDataKind.CANDLES,
        timeframe=timeframe,
    )


def _intent(
    tenant_id: UUID,
    user_id: UUID,
    *,
    stream_key: MarketStreamKey | None = None,
    stream_scope: StreamScope = StreamScope.PUBLIC_MARKET,
    source_ref: str = "widget-1",
    priority: int = 100,
    expires_at: int | None = None,
) -> SubscriptionIntent:
    return SubscriptionIntent(
        intent_id=uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        stream_scope=stream_scope,
        stream_key=_clave() if stream_key is None else stream_key,
        source_type=IntentSourceType.WIDGET,
        source_ref=source_ref,
        priority=priority,
        expires_at=expires_at,
        created_at=_AHORA,
        updated_at=_AHORA,
    )


def _guardar(
    app_db: PsycopgDatabase,
    user_id: UUID,
    hacer: Callable[[PostgresIntentStore], object],
) -> None:
    """Ejecuta algo con el store bajo el contexto de tenant del usuario (P05)."""
    scoped_db = TenantScopedDatabase(app_db)
    with scoped_db.transaction(user_id) as scoped:
        hacer(PostgresIntentStore(scoped))


def _tenant_de(app_db: PsycopgDatabase, user_id: UUID) -> UUID:
    scoped_db = TenantScopedDatabase(app_db)
    with scoped_db.transaction(user_id) as scoped:
        return scoped.context.tenant_id


def _demanda(ingestion_db: PsycopgDatabase) -> dict[str, int]:
    with ingestion_db.transaction() as session:
        return PostgresPublicDemand(session).snapshot()


class TestCatalogoDeInstrumentos:
    def test_upsert_y_lecturas(
        self, ingestion_db: PsycopgDatabase, limpiar_market: None
    ) -> None:
        with ingestion_db.transaction() as session:
            catalog = PostgresInstrumentCatalog(session)
            catalog.upsert("binance", "spot", "BTC-USDT", "BTCUSDT")

            assert catalog.has_exchange("binance") is True
            assert catalog.has_exchange("exchange_fantasma") is False
            assert catalog.exists("binance", "spot", "BTC-USDT") is True
            assert catalog.is_tradable("binance", "spot", "BTC-USDT") is True
            # El simbolo NATIVO no es el canonico: sin esa traduccion el mismo
            # mercado tendria dos identidades.
            assert catalog.native_symbol("binance", "spot", "BTC-USDT") == "BTCUSDT"

    def test_un_instrumento_inactivo_deja_de_ser_tradable_pero_sigue_existiendo(
        self, ingestion_db: PsycopgDatabase, limpiar_market: None
    ) -> None:
        # Un par delistado CONSERVA SU PASADO: no admite intereses nuevos, pero su
        # historico sigue siendo consultable. Por eso exists() sigue diciendo True.
        with ingestion_db.transaction() as session:
            catalog = PostgresInstrumentCatalog(session)
            catalog.upsert("binance", "spot", "DOGE-USDT", "DOGEUSDT")
            catalog.upsert(
                "binance", "spot", "DOGE-USDT", "DOGEUSDT", status="inactive"
            )

            assert catalog.exists("binance", "spot", "DOGE-USDT") is True
            assert catalog.is_tradable("binance", "spot", "DOGE-USDT") is False

    def test_deactivate_missing_marca_inactivos_y_no_borra_a_nadie(
        self, ingestion_db: PsycopgDatabase, limpiar_market: None
    ) -> None:
        with ingestion_db.transaction() as session:
            catalog = PostgresInstrumentCatalog(session)
            catalog.upsert("binance", "spot", "BTC-USDT", "BTCUSDT")
            catalog.upsert("binance", "spot", "DOGE-USDT", "DOGEUSDT")
            catalog.upsert("binance", "spot", "ETH-USDT", "ETHUSDT")

            # El catalogo remoto ya solo trae BTC: los otros dos se delistan.
            desactivados = catalog.deactivate_missing("binance", "spot", ["BTC-USDT"])

            assert desactivados == 2
            assert catalog.is_tradable("binance", "spot", "BTC-USDT") is True
            assert catalog.is_tradable("binance", "spot", "DOGE-USDT") is False
            # NO se borran: borrarlos dejaria velas huerfanas.
            assert catalog.exists("binance", "spot", "DOGE-USDT") is True
            assert catalog.exists("binance", "spot", "ETH-USDT") is True


class TestIntentStore:
    def test_ida_y_vuelta_completa(
        self,
        app_db: PsycopgDatabase,
        crear_usuario: Callable[[], UUID],
        limpiar_market: None,
    ) -> None:
        user = crear_usuario()
        provision_tenant_for_user(app_db, user)
        tenant = _tenant_de(app_db, user)
        original = _intent(
            tenant, user, priority=7, expires_at=_AHORA + 30_000, source_ref="w1"
        )

        _guardar(app_db, user, lambda store: store.insert(original))

        scoped_db = TenantScopedDatabase(app_db)
        with scoped_db.transaction(user) as scoped:
            recuperados = PostgresIntentStore(scoped).list_for_subject(tenant, user)

        assert len(recuperados) == 1
        vuelta = recuperados[0]
        # El objeto de contrato se reconstruye IGUAL que se guardo, campo a campo.
        assert vuelta == original

    def test_baja_de_un_interes(
        self,
        app_db: PsycopgDatabase,
        crear_usuario: Callable[[], UUID],
        limpiar_market: None,
    ) -> None:
        user = crear_usuario()
        provision_tenant_for_user(app_db, user)
        tenant = _tenant_de(app_db, user)
        _guardar(
            app_db, user, lambda s: s.insert(_intent(tenant, user, source_ref="w1"))
        )

        scoped_db = TenantScopedDatabase(app_db)
        with scoped_db.transaction(user) as scoped:
            store = PostgresIntentStore(scoped)
            borrados = store.delete(
                tenant,
                user,
                IntentSourceType.WIDGET,
                "w1",
                _clave().as_stream_key(),
            )
            assert borrados == 1
            assert store.count_for_subject(tenant, user) == 0

    def test_el_mismo_origen_no_duplica_su_interes_por_el_mismo_flujo(
        self,
        app_db: PsycopgDatabase,
        crear_usuario: Callable[[], UUID],
        limpiar_market: None,
    ) -> None:
        # Lo impide el UNIQUE de la tabla: es el MOTOR, no un if de Python.
        user = crear_usuario()
        provision_tenant_for_user(app_db, user)
        tenant = _tenant_de(app_db, user)
        _guardar(
            app_db, user, lambda s: s.insert(_intent(tenant, user, source_ref="w1"))
        )

        with pytest.raises(Exception) as excinfo:
            _guardar(
                app_db, user, lambda s: s.insert(_intent(tenant, user, source_ref="w1"))
            )
        assert "unique" in str(excinfo.value).lower()

    def test_aislamiento_entre_tenants(
        self,
        app_db: PsycopgDatabase,
        crear_usuario: Callable[[], UUID],
        limpiar_market: None,
    ) -> None:
        # Patron de test_tenancy_isolation: A no ve, ni borra, lo de B.
        user_a, user_b = crear_usuario(), crear_usuario()
        provision_tenant_for_user(app_db, user_a)
        provision_tenant_for_user(app_db, user_b)
        tenant_a, tenant_b = _tenant_de(app_db, user_a), _tenant_de(app_db, user_b)
        _guardar(app_db, user_a, lambda s: s.insert(_intent(tenant_a, user_a)))
        _guardar(app_db, user_b, lambda s: s.insert(_intent(tenant_b, user_b)))

        scoped_db = TenantScopedDatabase(app_db)
        with scoped_db.transaction(user_a) as scoped:
            store = PostgresIntentStore(scoped)
            # No ve los de B.
            assert store.count_for_subject(tenant_b, user_b) == 0
            assert store.list_for_subject(tenant_b, user_b) == []
            # Y aunque pida borrar los de B con sus ids exactos, borra CERO filas.
            borrados = store.delete(
                tenant_b,
                user_b,
                IntentSourceType.WIDGET,
                "widget-1",
                _clave().as_stream_key(),
            )
            assert borrados == 0

        # Lo de B sigue intacto, comprobado bajo el contexto de B.
        with scoped_db.transaction(user_b) as scoped:
            assert PostgresIntentStore(scoped).count_for_subject(tenant_b, user_b) == 1


class TestVentanillaDeDemanda:
    def test_dos_tenants_mismo_flujo_un_solo_stream_que_sigue_vivo(
        self,
        app_db: PsycopgDatabase,
        ingestion_db: PsycopgDatabase,
        crear_usuario: Callable[[], UUID],
        limpiar_market: None,
    ) -> None:
        # LA PRUEBA QUE IMPORTA (ADR-014). Dos tenants piden el MISMO flujo: UNA sola
        # clave con contador 2. Cuando uno se va, EL STREAM SIGUE VIVO (queda 1). Solo
        # cuando se va el ultimo desaparece la clave. Ese contador es exactamente lo
        # que decide si el worker abre o cierra la conexion al exchange.
        user_a, user_b = crear_usuario(), crear_usuario()
        provision_tenant_for_user(app_db, user_a)
        provision_tenant_for_user(app_db, user_b)
        tenant_a, tenant_b = _tenant_de(app_db, user_a), _tenant_de(app_db, user_b)
        clave = _clave().as_stream_key()

        _guardar(app_db, user_a, lambda s: s.insert(_intent(tenant_a, user_a)))
        _guardar(app_db, user_b, lambda s: s.insert(_intent(tenant_b, user_b)))

        assert _demanda(ingestion_db) == {clave: 2}

        # Se va A: el stream NO se cierra, porque B lo sigue queriendo.
        _guardar(
            app_db,
            user_a,
            lambda s: s.delete(
                tenant_a, user_a, IntentSourceType.WIDGET, "widget-1", clave
            ),
        )
        assert _demanda(ingestion_db) == {clave: 1}

        # Se va B: ya no lo quiere nadie y la clave desaparece del mapa.
        _guardar(
            app_db,
            user_b,
            lambda s: s.delete(
                tenant_b, user_b, IntentSourceType.WIDGET, "widget-1", clave
            ),
        )
        assert _demanda(ingestion_db) == {}

    def test_un_interes_caducado_no_cuenta(
        self,
        app_db: PsycopgDatabase,
        ingestion_db: PsycopgDatabase,
        crear_usuario: Callable[[], UUID],
        limpiar_market: None,
    ) -> None:
        # Sin esto quedarian suscripciones ZOMBIS: un widget que el usuario cerro hace
        # semanas seguiria gastando una conexion al exchange. La ventanilla lo ignora
        # comparando expires_at con now() EN EL MOTOR.
        #
        # OJO: aqui conviven DOS relojes. El SimulatedClock gobierna lo que produce
        # NUESTRO codigo (created_at/updated_at deterministas). Pero la caducidad la
        # decide el now() REAL de PostgreSQL dentro de la ventanilla. Un "caducado"
        # calculado con el reloj simulado puede caer en el FUTURO real. Para probar
        # la caducidad hay que usar un instante pasado DE VERDAD.
        user = crear_usuario()
        provision_tenant_for_user(app_db, user)
        tenant = _tenant_de(app_db, user)
        caducado = _intent(tenant, user, expires_at=_PASADO_REAL)

        _guardar(app_db, user, lambda s: s.insert(caducado))

        assert _demanda(ingestion_db) == {}

    def test_un_interes_efimero_aun_vivo_si_cuenta(
        self,
        app_db: PsycopgDatabase,
        ingestion_db: PsycopgDatabase,
        crear_usuario: Callable[[], UUID],
        limpiar_market: None,
    ) -> None:
        # La otra mitad: la ventanilla IGNORA lo caducado, pero NO descarta lo efimero
        # por el mero hecho de tener fecha de caducidad. Sin este caso, el test de
        # arriba pasaria igual aunque la ventanilla filtrase TODOS los que tienen
        # expires_at, y no nos enterariamos.
        user = crear_usuario()
        provision_tenant_for_user(app_db, user)
        tenant = _tenant_de(app_db, user)
        vivo = _intent(tenant, user, expires_at=_FUTURO_REAL)

        _guardar(app_db, user, lambda s: s.insert(vivo))

        assert _demanda(ingestion_db) == {_clave().as_stream_key(): 1}

    def test_un_interes_privado_no_suma_en_la_demanda_publica(
        self,
        app_db: PsycopgDatabase,
        ingestion_db: PsycopgDatabase,
        crear_usuario: Callable[[], UUID],
        limpiar_market: None,
    ) -> None:
        # Los intereses privados/BYOC JAMAS pasan por la ventanilla publica: los filtra
        # la funcion y, sobre todo, la policy del dueno (CA-P07-G).
        user_a, user_b = crear_usuario(), crear_usuario()
        provision_tenant_for_user(app_db, user_a)
        provision_tenant_for_user(app_db, user_b)
        tenant_a, tenant_b = _tenant_de(app_db, user_a), _tenant_de(app_db, user_b)
        clave = _clave().as_stream_key()

        _guardar(app_db, user_a, lambda s: s.insert(_intent(tenant_a, user_a)))
        _guardar(
            app_db,
            user_b,
            lambda s: s.insert(
                _intent(tenant_b, user_b, stream_scope=StreamScope.USER)
            ),
        )

        assert _demanda(ingestion_db) == {clave: 1}
