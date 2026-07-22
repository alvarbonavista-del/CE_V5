"""El limitador montado en la PUERTA: PostgreSQL y Redis REALES (dictamen CSA 1-6, 8).

La propiedad que se defiende aqui es la INDISTINGUIBILIDAD: usuario inexistente, clave
equivocada, cuenta frenada y limitador caido deben producir la MISMA respuesta. En
cuanto una de las cuatro se diferencie, la API se convierte en un oraculo que dice quien
tiene cuenta y cuando esta frenada.

Por eso NO hay cabecera Retry-After: diria "estas frenado", que es justo lo que no puede
saberse.

DATOS FALSOS SIEMPRE. Cada test usa su prefijo de Redis (uuid4) y sus emails inventados.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
import redis
from fastapi.testclient import TestClient
from httpx2 import Response

from ce_v5.core.auth.config import AuthConfig
from ce_v5.core.auth.passwords import Argon2PasswordHasher
from ce_v5.core.auth.ports import PasswordHasher
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

_DSN = os.environ.get("CE_V5_DATABASE_URL")
_REDIS_URL = os.environ.get("CE_V5_REDIS_URL")
pytestmark = pytest.mark.skipif(
    _DSN is None or _REDIS_URL is None,
    reason="requiere CE_V5_DATABASE_URL y CE_V5_REDIS_URL (PostgreSQL y Redis locales)",
)

_CONFIG = AuthConfig(jwt_secret="secreto-de-test-de-32-caracteres-o-mas")
_RATE_CONFIG = RateLimitConfig(digest_secret="secreto-de-huellas-de-32-caracteres")
_PASSWORD = "contrasena-falsa-de-test"
_IP = "203.0.113.10"  # TEST-NET-3: ficticia.


class _SpyHasher:
    """Envuelve al hasher REAL y cuenta las verificaciones (prueba 2: el senuelo)."""

    def __init__(self, inner: PasswordHasher) -> None:
        self._inner = inner
        self.verificaciones = 0

    def hash(self, password: str) -> str:
        return self._inner.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        self.verificaciones += 1
        return self._inner.verify(password_hash, password)


def _redis_client() -> redis.Redis:
    assert _REDIS_URL is not None
    return create_client(RedisBusConfig(url=_REDIS_URL))


def _bus() -> RedisEventBus:
    """Bus REAL: el canal realtime lo necesita; los demas tests no lo usan."""
    assert _REDIS_URL is not None
    config = RedisBusConfig(url=_REDIS_URL, namespace=f"test-bus-{uuid4().hex}")
    return RedisEventBus(create_client(config), config)


def _context(
    app_db: PsycopgDatabase,
    limiter: RedisAuthRateLimiter,
    hasher: PasswordHasher | None = None,
) -> ApiContext:
    clock = SystemClock()
    tokens = AccessTokenService(_CONFIG, clock)
    sensitive_audit = PostgresSensitiveActionAudit(app_db)
    auditor = ApiAuthAuditor(app_db, sensitive_audit)
    cache = CapabilitySetCache(clock, max_staleness_ms=60_000)
    cached = CachedPolicyEvaluator(
        PolicyEvaluator(PostgresPolicyStore(app_db), clock), cache
    )
    return ApiContext(
        auth=AuthService(
            credentials=PostgresCredentialReader(app_db),
            registrar=PostgresUserRegistrar(app_db, clock),
            sessions=PostgresSessionStore(app_db),
            hasher=Argon2PasswordHasher() if hasher is None else hasher,
            tokens=tokens,
            clock=clock,
            config=_CONFIG,
            limiter=limiter,
            rate_config=_RATE_CONFIG,
            auditor=auditor,
        ),
        tokens=tokens,
        scoped_db=TenantScopedDatabase(app_db),
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
def prefix() -> str:
    return f"test-apirl-{uuid4().hex}"


@pytest.fixture
def limiter(prefix: str) -> Iterator[RedisAuthRateLimiter]:
    conn = _redis_client()
    try:
        yield RedisAuthRateLimiter(conn, _RATE_CONFIG, prefix=prefix)
    finally:
        for key in conn.scan_iter(match=f"{prefix}:*"):
            conn.delete(key)
        conn.close()


def _client(
    app_db: PsycopgDatabase,
    limiter: RedisAuthRateLimiter,
    ip: str = _IP,
    hasher: PasswordHasher | None = None,
) -> TestClient:
    """Un cliente cuya IP DE CONEXION es la que se le da (no una cabecera)."""
    return TestClient(
        create_app(_context(app_db, limiter, hasher)),
        base_url="https://testserver",
        client=(ip, 12345),
    )


@pytest.fixture
def client(
    app_db: PsycopgDatabase, limiter: RedisAuthRateLimiter
) -> Iterator[TestClient]:
    with _client(app_db, limiter) as test_client:
        yield test_client


def _email() -> str:
    return f"test-{uuid4().hex}@ejemplo.test"


def _alta(client: TestClient) -> str:
    email = _email()
    response = client.post(
        "/v1/auth/register", json={"email": email, "password": _PASSWORD}
    )
    assert response.status_code == 201
    return email


def _login(client: TestClient, email: str, password: str = _PASSWORD) -> Response:
    return client.post("/v1/auth/login", json={"email": email, "password": password})


def test_prueba_1_inexistente_y_clave_mala_son_indistinguibles(
    client: TestClient,
) -> None:
    email = _alta(client)

    clave_mala = _login(client, email, "no-es-la-clave")
    inexistente = _login(client, _email())

    assert clave_mala.status_code == inexistente.status_code == 401
    # Byte a byte: ni el codigo ni el mensaje delatan si la cuenta existe.
    assert clave_mala.content == inexistente.content


def test_prueba_2_el_email_inexistente_paga_el_senuelo(
    app_db: PsycopgDatabase, limiter: RedisAuthRateLimiter
) -> None:
    espia = _SpyHasher(Argon2PasswordHasher())
    with _client(app_db, limiter, hasher=espia) as client:
        antes = espia.verificaciones
        assert _login(client, _email()).status_code == 401

    # Se verifico contra el hash SENUELO: si no, responder "no existe" seria mucho mas
    # rapido que "clave mala", y el reloj delataria quien tiene cuenta.
    assert espia.verificaciones == antes + 1


def test_prueba_3_el_limite_por_ip_muerde(
    app_db: PsycopgDatabase, limiter: RedisAuthRateLimiter
) -> None:
    # Uno que prueba claves contra MUCHAS cuentas distintas desde la MISMA IP: ni la
    # cuenta ni ip_account se disparan, pero la IP acumula.
    with _client(app_db, limiter) as client:
        umbral = _RATE_CONFIG.by_ip.max_failures
        for _ in range(umbral + 1):
            _login(client, _email())

        # Una cuenta REAL con su clave CORRECTA: si la IP no estuviera frenada, entra.
        email = _alta(client)
        respuesta = _login(client, email)

    assert respuesta.status_code == 401


def test_prueba_4_el_limite_por_cuenta_muerde(
    app_db: PsycopgDatabase, limiter: RedisAuthRateLimiter
) -> None:
    # Mil maquinas contra una victima: cada IP falla poco, la cuenta acumula.
    with _client(app_db, limiter) as alta_client:
        email = _alta(alta_client)

    umbral = _RATE_CONFIG.by_account.max_failures
    for i in range(umbral + 1):
        with _client(app_db, limiter, ip=f"203.0.113.{i + 20}") as atacante:
            _login(atacante, email, "no-es-la-clave")

    # Desde una IP LIMPIA y con la clave CORRECTA: la cuenta esta frenada igualmente.
    with _client(app_db, limiter, ip="198.51.100.9") as limpio:
        respuesta = _login(limpio, email)

    assert respuesta.status_code == 401


def test_prueba_5_el_limite_por_ip_y_cuenta_es_el_mas_estrecho(
    client: TestClient,
) -> None:
    email = _alta(client)
    # Salta ANTES que los otros dos: es el ataque dirigido, la firma mas clara.
    for _ in range(_RATE_CONFIG.by_ip_account.max_failures + 1):
        _login(client, email, "no-es-la-clave")

    # Aun no se llego al umbral de cuenta (10) ni al de IP (30), y sin embargo la clave
    # CORRECTA ya no entra.
    assert (
        _RATE_CONFIG.by_ip_account.max_failures < _RATE_CONFIG.by_account.max_failures
    )
    assert _login(client, email).status_code == 401


def test_prueba_6_con_redis_caido_el_login_deniega(app_db: PsycopgDatabase) -> None:
    # Fail-closed: sin contador no hay limite, asi que NO se autentica a nadie. Jamas
    # "permitido por si acaso".
    muerto = redis.Redis.from_url(
        "redis://127.0.0.1:1/0", socket_connect_timeout=1, decode_responses=False
    )
    limiter_muerto = RedisAuthRateLimiter(muerto, _RATE_CONFIG, prefix="test-muerto")
    try:
        with _client(app_db, limiter_muerto) as client:
            respuesta = _login(client, _email())
    finally:
        muerto.close()

    assert respuesta.status_code == 401
    assert respuesta.json()["code"] == "invalid_credentials"


def test_prueba_8_los_cuatro_casos_dicen_lo_mismo(
    app_db: PsycopgDatabase, limiter: RedisAuthRateLimiter
) -> None:
    """Enumeracion por diferencia de mensaje: los cuatro cuerpos deben ser IDENTICOS."""
    cuerpos: list[bytes] = []

    with _client(app_db, limiter) as client:
        email = _alta(client)
        # 1. Usuario inexistente.
        cuerpos.append(_login(client, _email()).content)
        # 2. Clave equivocada.
        cuerpos.append(_login(client, email, "no-es-la-clave").content)
        # 3. Cuenta frenada (se supera el umbral mas estrecho).
        for _ in range(_RATE_CONFIG.by_ip_account.max_failures + 1):
            _login(client, email, "no-es-la-clave")
        cuerpos.append(_login(client, email).content)

    # 4. Limitador caido.
    muerto = redis.Redis.from_url(
        "redis://127.0.0.1:1/0", socket_connect_timeout=1, decode_responses=False
    )
    try:
        with _client(
            app_db, RedisAuthRateLimiter(muerto, _RATE_CONFIG, prefix="test-muerto")
        ) as caido:
            cuerpos.append(_login(caido, email).content)
    finally:
        muerto.close()

    assert len(set(cuerpos)) == 1


def test_no_se_devuelve_retry_after(client: TestClient) -> None:
    # Retry-After diria "estas frenado": rompe la indistinguibilidad exigida.
    email = _alta(client)
    for _ in range(_RATE_CONFIG.by_ip_account.max_failures + 1):
        _login(client, email, "no-es-la-clave")

    respuesta = _login(client, email)
    assert respuesta.status_code == 401
    assert "retry-after" not in {k.lower() for k in respuesta.headers}
