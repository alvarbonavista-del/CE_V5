"""Linea base HTTP de la puerta (P06b, dictamen CSA pruebas 9, 11 y 12).

CSRF, limite de cuerpo, Content-Type y cabeceras de seguridad, contra la API REAL. Los
tests se adaptan al sistema: NO se relaja el CSRF para que pasen.

DATOS FALSOS SIEMPRE.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
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
from ce_v5.entrypoints.api.cookies import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
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
# Limite pequeno a proposito: para probar el 413 no hace falta mandar megabytes.
_MAX_BODY = 512


def _bus() -> RedisEventBus:
    """Bus REAL: el canal realtime lo necesita; los demas tests no lo usan."""
    assert _REDIS_URL is not None
    config = RedisBusConfig(url=_REDIS_URL, namespace=f"test-bus-{uuid4().hex}")
    return RedisEventBus(create_client(config), config)


def _context(app_db: PsycopgDatabase, api_config: ApiConfig) -> ApiContext:
    clock = SystemClock()
    tokens = AccessTokenService(_CONFIG, clock)
    sensitive_audit = PostgresSensitiveActionAudit(app_db)
    auditor = ApiAuthAuditor(app_db, sensitive_audit)
    assert _REDIS_URL is not None
    limiter = RedisAuthRateLimiter(
        create_client(RedisBusConfig(url=_REDIS_URL)),
        _RATE_CONFIG,
        prefix=f"test-hard-{uuid4().hex}",
    )
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
        config=_CONFIG,
        api_config=api_config,
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


def _make_client(app_db: PsycopgDatabase, api_config: ApiConfig) -> TestClient:
    return TestClient(
        create_app(_context(app_db, api_config)),
        base_url="https://testserver",
        client=("203.0.113.10", 12345),
    )


@pytest.fixture
def client(app_db: PsycopgDatabase) -> Iterator[TestClient]:
    with _make_client(app_db, ApiConfig(max_body_bytes=_MAX_BODY)) as test_client:
        yield test_client


def _registrar(client: TestClient) -> None:
    response = client.post(
        "/v1/auth/register",
        json={"email": f"test-{uuid4().hex}@ejemplo.test", "password": _PASSWORD},
    )
    assert response.status_code == 201


def _csrf(client: TestClient) -> dict[str, str]:
    return {CSRF_HEADER_NAME: str(client.cookies.get(CSRF_COOKIE_NAME))}


# --- PRUEBA 9: CSRF ----------------------------------------------------------------


def test_prueba_9_refresh_sin_cabecera_csrf_es_403(client: TestClient) -> None:
    # La cookie viaja sola (el navegador la manda sin preguntar). Sin la mitad que una
    # pagina ajena NO puede leer, la peticion se rechaza.
    _registrar(client)

    sin_csrf = client.post("/v1/auth/refresh")

    assert sin_csrf.status_code == 403
    assert sin_csrf.json()["code"] == "csrf_failed"


def test_prueba_9_refresh_con_cabecera_csrf_correcta_pasa(client: TestClient) -> None:
    _registrar(client)
    assert client.post("/v1/auth/refresh", headers=_csrf(client)).status_code == 200


def test_prueba_9_logout_sin_csrf_es_403_y_con_csrf_pasa(client: TestClient) -> None:
    _registrar(client)
    assert client.post("/v1/auth/logout").status_code == 403
    assert client.post("/v1/auth/logout", headers=_csrf(client)).status_code == 204


def test_una_cabecera_csrf_que_no_coincide_es_403(client: TestClient) -> None:
    _registrar(client)
    response = client.post(
        "/v1/auth/refresh", headers={CSRF_HEADER_NAME: "no-es-el-token"}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"


# --- PRUEBAS 11 y 12: cuerpo y contrato --------------------------------------------


def test_prueba_11_un_cuerpo_gigante_es_413(client: TestClient) -> None:
    # Se rechaza ANTES de leerlo, mirando Content-Length: leerlo para medirlo ya seria
    # haber perdido.
    enorme = {
        "email": "ana@ejemplo.test",
        "password": _PASSWORD,
        "relleno": "a" * (_MAX_BODY * 2),
    }
    response = client.post("/v1/auth/login", json=enorme)

    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"


def test_prueba_12_un_campo_extra_es_422(client: TestClient) -> None:
    # El contrato (extra="forbid") lo rechaza. Es la misma defensa que impide colar un
    # tenant en la peticion (ADR-011); aqui se referencia como parte de la linea base.
    response = client.post(
        "/v1/auth/login",
        json={
            "email": "ana@ejemplo.test",
            "password": _PASSWORD,
            "campo_inventado": "x",
        },
    )
    assert response.status_code == 422


def test_un_content_type_que_no_es_json_es_415(client: TestClient) -> None:
    response = client.post(
        "/v1/auth/login",
        content=b"email=ana&password=x",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 415
    assert response.json()["code"] == "unsupported_media_type"


# --- Cabeceras de seguridad --------------------------------------------------------


def test_las_cabeceras_de_seguridad_estan_en_todas_las_respuestas(
    client: TestClient,
) -> None:
    response = client.get("/v1/me")  # 401: hasta en los errores.

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"


def test_fuera_de_produccion_no_se_manda_hsts(client: TestClient) -> None:
    # En desarrollo sin HTTPS, HSTS dejaria el navegador clavado en https para ese host.
    response = client.get("/v1/me")
    assert "strict-transport-security" not in {k.lower() for k in response.headers}


def test_en_produccion_si_se_manda_hsts(app_db: PsycopgDatabase) -> None:
    produccion = ApiConfig(environment="production", max_body_bytes=_MAX_BODY)
    with _make_client(app_db, produccion) as client:
        response = client.get("/v1/me")

    assert "max-age=31536000" in response.headers["strict-transport-security"]
