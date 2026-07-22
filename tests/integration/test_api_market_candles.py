"""Tests del endpoint publico de velas contra PostgreSQL real (T-05).

CAMINO COMPLETO Y CON LOS DOS ROLES, que es justo lo que no puede probar un doble: las
velas las escribe el ROL DE INGESTA por el camino de produccion (PostgresCandleWriter,
historico + outbox atomicos) y la API las lee con el ROL DE APLICACION, que sobre
market_candle solo tiene SELECT (regla 5.20, migracion 0012). Si el escritor y el lector
dejaran de entenderse -- una columna, un dedup, un orden --, aqui se ve.

LA CORRECCION ES EL CASO QUE IMPORTA. Una vela corregida NO muta el original: es una
fila NUEVA con el mismo open_time (append-only, ADR-007). Sin el DISTINCT ON por
revision, ese open_time saldria DOS veces y el grafico dibujaria una barra fantasma que
desplazaria toda la serie. El endpoint tiene que servir la revision VIGENTE, la misma
que lee el evaluador de reglas.

Base de JUGUETE: nunca datos reales (DOC_ENTREGABLES sec.5).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ce_v5.core.auth.config import AuthConfig
from ce_v5.core.auth.passwords import Argon2PasswordHasher
from ce_v5.core.auth.rate_limit import RateLimitConfig
from ce_v5.core.auth.service import AuthService
from ce_v5.core.auth.tokens import AccessTokenService
from ce_v5.core.clock.system import SystemClock
from ce_v5.core.policy.cache import CapabilitySetCache
from ce_v5.core.policy.cached_evaluator import CachedPolicyEvaluator
from ce_v5.core.policy.evaluator import PolicyEvaluator
from ce_v5.core.policy.gate import PolicyGate
from ce_v5.core.policy.invalidation import PolicyCacheInvalidator
from ce_v5.core.policy.providers import (
    StaticIpGeoProvider,
    StaticKycProvider,
    StaticVpnDetector,
)
from ce_v5.entrypoints.api.app import create_app
from ce_v5.entrypoints.api.audit import ApiAuthAuditor
from ce_v5.entrypoints.api.composition import ApiContext
from ce_v5.entrypoints.api.config import ApiConfig
from ce_v5.infra.bus_redis import RedisBusConfig, RedisEventBus, create_client
from ce_v5.infra.db.identity import (
    PostgresCredentialReader,
    PostgresSessionStore,
    PostgresUserRegistrar,
)
from ce_v5.infra.db.outbox_publisher import OutboxPublisher
from ce_v5.infra.db.policy_store import PostgresPolicyStore
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.infra.db.sensitive_audit import PostgresSensitiveActionAudit
from ce_v5.infra.db.tenancy import TenantScopedDatabase
from ce_v5.infra.ratelimit.redis_limiter import RedisAuthRateLimiter
from source.families.market import (
    CandleClosedPayload,
    CandleCorrectedPayload,
    CandlePayload,
    MarketCandleEventType,
    MarketType,
    Timeframe,
)
from source.time import MaturityState

_DSN = os.environ.get("CE_V5_DATABASE_URL")
_REDIS_URL = os.environ.get("CE_V5_REDIS_URL")
pytestmark = pytest.mark.skipif(
    _DSN is None or _REDIS_URL is None,
    reason="requiere CE_V5_DATABASE_URL y CE_V5_REDIS_URL (PostgreSQL y Redis locales)",
)

_CONFIG = AuthConfig(jwt_secret="secreto-de-test-de-32-caracteres-o-mas")
_RATE_CONFIG = RateLimitConfig(digest_secret="secreto-de-huellas-de-32-caracteres")

_EXCHANGE = "binance"
_SYMBOL = "BTC-USDT"
_TIMEFRAME = "1m"
_RUTA = "/v1/public/market/candles"
_FLUJO = f"exchange={_EXCHANGE}&symbol={_SYMBOL}&timeframe={_TIMEFRAME}"

# Tres ventanas consecutivas de un minuto, sembradas A PROPOSITO EN DESORDEN: si el
# endpoint devolviera el orden de insercion en vez del orden temporal, el grafico
# dibujaria la serie al azar y el test lo cazaria.
_MINUTO = 60_000
_T0 = 1_784_073_600_000
_T1 = _T0 + _MINUTO
_T2 = _T0 + 2 * _MINUTO

Persistir = Callable[[CandlePayload, MarketCandleEventType, int], bool]


def _cerrada(open_time: int, close: str, volume: str = "12.5") -> CandleClosedPayload:
    return CandleClosedPayload(
        maturity_state=MaturityState.CLOSED,
        exchange=_EXCHANGE,
        market_type=MarketType.SPOT,
        symbol=_SYMBOL,
        timeframe=Timeframe.M1,
        open_time=open_time,
        close_time=open_time + 59_999,
        # Ocho decimales: si algun tramo del camino pasara por float, este numero se
        # rompe y el test lo dice.
        open=Decimal("100.12345678"),
        high=Decimal("110.5"),
        low=Decimal("95.25"),
        close=Decimal(close),
        volume=Decimal(volume),
    )


def _correccion(
    open_time: int, corrige: str, close: str, volume: str
) -> CandleCorrectedPayload:
    return CandleCorrectedPayload(
        maturity_state=MaturityState.CORRECTION,
        corrects_idempotency_key=corrige,
        correction_revision=1,
        exchange=_EXCHANGE,
        market_type=MarketType.SPOT,
        symbol=_SYMBOL,
        timeframe=Timeframe.M1,
        open_time=open_time,
        close_time=open_time + 59_999,
        open=Decimal("100.12345678"),
        high=Decimal("110.5"),
        low=Decimal("95.25"),
        close=Decimal(close),
        volume=Decimal(volume),
    )


@pytest.fixture
def limpiar_market(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """Velas y outbox: sin FK a nadie, se acumularian entre ejecuciones."""

    def _wipe() -> None:
        with migrator_db.transaction() as session:
            session.execute("DELETE FROM market_candle")
            session.execute("DELETE FROM outbox")

    _wipe()
    yield
    _wipe()


def _limiter() -> RedisAuthRateLimiter:
    assert _REDIS_URL is not None
    return RedisAuthRateLimiter(
        create_client(RedisBusConfig(url=_REDIS_URL)),
        _RATE_CONFIG,
        prefix=f"test-api-{uuid4().hex}",
    )


def _bus() -> RedisEventBus:
    assert _REDIS_URL is not None
    config = RedisBusConfig(url=_REDIS_URL, namespace=f"test-bus-{uuid4().hex}")
    return RedisEventBus(create_client(config), config)


def _context(app_db: PsycopgDatabase) -> ApiContext:
    clock = SystemClock()
    tokens = AccessTokenService(_CONFIG, clock)
    sensitive_audit = PostgresSensitiveActionAudit(app_db)
    auditor = ApiAuthAuditor(app_db, sensitive_audit)
    limiter = _limiter()
    cache = CapabilitySetCache(clock, max_staleness_ms=60_000)
    cached = CachedPolicyEvaluator(
        PolicyEvaluator(PostgresPolicyStore(app_db), clock), cache
    )
    return ApiContext(
        auth=AuthService(
            credentials=PostgresCredentialReader(app_db),
            registrar=PostgresUserRegistrar(app_db, clock),
            sessions=PostgresSessionStore(app_db),
            hasher=Argon2PasswordHasher(),
            tokens=tokens,
            clock=clock,
            config=_CONFIG,
            limiter=limiter,
            rate_config=_RATE_CONFIG,
            auditor=auditor,
        ),
        tokens=tokens,
        scoped_db=TenantScopedDatabase(app_db),
        # La lectura de mercado va por la conexion SIN tenant: market_candle es
        # public_market y el rol de aplicacion solo puede hacerle SELECT.
        market_db=app_db,
        config=_CONFIG,
        api_config=ApiConfig(),
        limiter=limiter,
        rate_config=_RATE_CONFIG,
        auditor=auditor,
        bus=_bus(),
        publisher=OutboxPublisher(db=app_db, bus=_bus()),
        invalidator=PolicyCacheInvalidator(cache),
        gate=PolicyGate(cached, sensitive_audit),
        ip_geo=StaticIpGeoProvider({}),
        kyc=StaticKycProvider({}, {}),
        vpn=StaticVpnDetector(frozenset(), frozenset()),
    )


@pytest.fixture
def client(app_db: PsycopgDatabase) -> Iterator[TestClient]:
    """La aplicacion REAL: si el router no estuviera montado, la ruta seria un 404."""
    with TestClient(
        create_app(_context(app_db)), base_url="https://testserver"
    ) as test_client:
        yield test_client


@pytest.fixture
def historico(persistir_vela: Persistir, limpiar_market: None) -> str:
    """Tres velas cerradas del flujo, sembradas en desorden. Devuelve la clave de T1."""
    for open_time, close in ((_T1, "105"), (_T0, "100.5"), (_T2, "110")):
        assert (
            persistir_vela(
                _cerrada(open_time, close),
                MarketCandleEventType.CANDLE_CLOSED,
                open_time + 42,
            )
            is True
        )
    return _cerrada(_T1, "105").idempotency_key(MarketCandleEventType.CANDLE_CLOSED)


def _velas(client: TestClient, query: str = "") -> list[dict[str, object]]:
    respuesta = client.get(f"{_RUTA}?{_FLUJO}{query}")
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert isinstance(cuerpo, list)
    return [dict(vela) for vela in cuerpo]


class TestLecturaDelHistorico:
    def test_devuelve_el_ohlcv_entero_y_no_solo_los_cierres(
        self, client: TestClient, historico: str
    ) -> None:
        # Quien dibuja una vela necesita su CUERPO: con solo el cierre no hay vela que
        # pintar. Y los precios viajan como texto para no perder digitos en el cable.
        vela = _velas(client)[0]

        assert vela == {
            "open_time": _T0,
            "open": "100.12345678",
            "high": "110.5",
            "low": "95.25",
            "close": "100.5",
            "volume": "12.5",
        }

    def test_las_sirve_en_orden_ascendente_de_open_time(
        self, client: TestClient, historico: str
    ) -> None:
        # Se sembraron en desorden (T1, T0, T2): el orden lo pone la consulta, no el
        # azar de la insercion. Una serie desordenada es una serie mentirosa.
        assert [vela["open_time"] for vela in _velas(client)] == [_T0, _T1, _T2]

    def test_un_flujo_sin_historico_es_lista_vacia_con_200(
        self, client: TestClient, historico: str
    ) -> None:
        # Mismo exchange y timeframe, otro simbolo: no hay dato, y eso no es un error.
        respuesta = client.get(
            f"{_RUTA}?exchange={_EXCHANGE}&symbol=ETH-USDT&timeframe={_TIMEFRAME}"
        )
        assert respuesta.status_code == 200
        assert respuesta.json() == []

    def test_no_hace_falta_token_para_leer_el_mercado(
        self, client: TestClient, historico: str
    ) -> None:
        # SIN NINGUNA CABECERA DE SESION responde 200 con las velas. El precio de
        # BTC-USDT no es dato de nadie (public_market, 0012): pedir sesion aqui no
        # protegeria ningun secreto y solo pasearia el token por un camino mas. Es el
        # contraste con /v1/capabilities, que sin token es 401.
        respuesta = client.get(f"{_RUTA}?{_FLUJO}")

        assert respuesta.status_code == 200
        assert len(respuesta.json()) == 3
        assert client.get("/v1/capabilities").status_code == 401


class TestCorreccion:
    def test_devuelve_la_revision_vigente_y_una_sola_fila_por_ventana(
        self,
        client: TestClient,
        persistir_vela: Persistir,
        historico: str,
    ) -> None:
        # APPEND-ONLY: la correccion NO borra el original, es otra fila con el MISMO
        # open_time. Sin el dedup por revision saldrian CUATRO velas y T1 saldria dos
        # veces, desplazando toda la serie.
        assert (
            persistir_vela(
                _correccion(_T1, historico, close="106.75", volume="13.25"),
                MarketCandleEventType.CANDLE_CORRECTED,
                _T1 + 43,
            )
            is True
        )

        velas = _velas(client)

        assert [vela["open_time"] for vela in velas] == [_T0, _T1, _T2]
        # Y lo corregido es el CUERPO entero, no solo el cierre.
        assert velas[1]["close"] == "106.75"
        assert velas[1]["volume"] == "13.25"

    def test_el_original_sigue_en_el_historico_aunque_no_se_sirva(
        self,
        client: TestClient,
        app_db: PsycopgDatabase,
        persistir_vela: Persistir,
        historico: str,
    ) -> None:
        # La verdad de entonces no se borra: lo que hace el endpoint es ELEGIR la
        # revision vigente, no reescribir la historia (ADR-007).
        persistir_vela(
            _correccion(_T1, historico, close="106.75", volume="13.25"),
            MarketCandleEventType.CANDLE_CORRECTED,
            _T1 + 43,
        )

        with app_db.transaction() as session:
            filas = session.fetchall(
                "SELECT close FROM market_candle WHERE open_time = %s ORDER BY close",
                (_T1,),
            )

        assert [str(fila[0]) for fila in filas] == ["105", "106.75"]
        assert _velas(client)[1]["close"] == "106.75"


class TestRecorte:
    def test_limit_recorta_por_el_extremo_antiguo_y_deja_las_mas_recientes(
        self, client: TestClient, historico: str
    ) -> None:
        # Pedir 2 de 3 devuelve las DOS ULTIMAS, no las dos primeras: quien abre un
        # grafico quiere ver lo que acaba de pasar, no el principio de los tiempos.
        assert [vela["open_time"] for vela in _velas(client, "&limit=2")] == [_T1, _T2]

    def test_up_to_es_el_tope_temporal_para_paginar_hacia_atras(
        self, client: TestClient, historico: str
    ) -> None:
        # Es el mecanismo de paginacion del historico: el cliente pide "las N anteriores
        # a esta vela" y no hace falta ningun reloj en el servidor (ADR-007).
        velas = _velas(client, f"&up_to={_T1}")
        assert [vela["open_time"] for vela in velas] == [_T0, _T1]

    def test_sin_limit_caben_las_tres(self, client: TestClient, historico: str) -> None:
        assert len(_velas(client)) == 3
