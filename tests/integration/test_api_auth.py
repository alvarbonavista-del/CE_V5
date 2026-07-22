"""Tests de la API de autenticacion contra PostgreSQL real (P06b, ADR-019).

El ApiContext se construye A MANO con la fixture app_db (no con build_context): asi el
test no depende del entorno del proceso y puede inyectar su propia AuthConfig.

BASE HTTPS EN EL CLIENTE DE PRUEBAS: la cookie del refresh es Secure, asi que un cliente
sobre http:// NO la reenviaria y los tests de rotacion no probarian nada. Con
base_url https://testserver la cookie viaja, que es lo que ocurre en produccion.

DATOS FALSOS SIEMPRE: emails inventados en ejemplo.test, contrasenas de juguete.
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
from ce_v5.entrypoints.api.cookies import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    REFRESH_COOKIE_NAME,
    REFRESH_COOKIE_PATH,
)
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


def _limiter() -> RedisAuthRateLimiter:
    """Limitador REAL sobre Redis (el dictamen exige Redis, no mocks).

    Prefijo unico por contexto: un test no puede frenar a otro.
    """
    assert _REDIS_URL is not None
    return RedisAuthRateLimiter(
        create_client(RedisBusConfig(url=_REDIS_URL)),
        _RATE_CONFIG,
        prefix=f"test-api-{uuid4().hex}",
    )


def _bus() -> RedisEventBus:
    """Bus REAL: el canal realtime lo necesita; los demas tests no lo usan."""
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
        market_db=app_db,
        config=_CONFIG,
        api_config=ApiConfig(),
        limiter=limiter,
        rate_config=_RATE_CONFIG,
        # El gate y el bus no los ejercitan estos tests (son de auth), pero el contexto
        # los exige: se cablea la cadena real, con los proveedores vacios de v5.0.
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
    with TestClient(
        create_app(_context(app_db)), base_url="https://testserver"
    ) as test_client:
        yield test_client


def _email() -> str:
    return f"test-{uuid4().hex}@ejemplo.test"


def _registrar(
    client: TestClient, email: str | None = None
) -> tuple[str, dict[str, str]]:
    payload = {"email": email or _email(), "password": _PASSWORD}
    response = client.post("/v1/auth/register", json=payload)
    assert response.status_code == 201
    return payload["email"], response.json()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_register_devuelve_201_sin_refresh_en_el_cuerpo(client: TestClient) -> None:
    response = client.post(
        "/v1/auth/register", json={"email": _email(), "password": _PASSWORD}
    )

    assert response.status_code == 201
    cuerpo = response.json()
    # Regla dura de ADR-019: el refresh token NO aparece en el JSON.
    assert not any("refresh" in campo for campo in cuerpo)
    assert cuerpo["access_token"]

    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert REFRESH_COOKIE_NAME in set_cookie


def test_el_access_token_del_registro_sirve_para_me(client: TestClient) -> None:
    _, sesion = _registrar(client)
    response = client.get("/v1/me", headers=_auth(sesion["access_token"]))
    assert response.status_code == 200
    assert response.json()["user_id"] == sesion["user_id"]


def test_me_devuelve_un_tenant_que_el_cliente_nunca_mando(client: TestClient) -> None:
    # El tenant lo resolvio el BACKEND desde la pertenencia (ADR-011): en ninguna
    # peticion de este test viajo un tenant_id.
    _, sesion = _registrar(client)
    cuerpo = client.get("/v1/me", headers=_auth(sesion["access_token"])).json()
    assert cuerpo["tenant_id"]
    assert cuerpo["tenant_id"] != cuerpo["user_id"]


def test_login_correcto(client: TestClient) -> None:
    email, _ = _registrar(client)
    response = client.post(
        "/v1/auth/login", json={"email": email, "password": _PASSWORD}
    )
    assert response.status_code == 200
    assert (
        client.get("/v1/me", headers=_auth(response.json()["access_token"])).status_code
        == 200
    )


def test_login_con_contrasena_incorrecta(client: TestClient) -> None:
    email, _ = _registrar(client)
    response = client.post(
        "/v1/auth/login", json={"email": email, "password": "no-es-la-clave"}
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_login_con_email_inexistente_responde_exactamente_igual(
    client: TestClient,
) -> None:
    email, _ = _registrar(client)
    mala_clave = client.post(
        "/v1/auth/login", json={"email": email, "password": "no-es-la-clave"}
    )
    sin_cuenta = client.post(
        "/v1/auth/login", json={"email": _email(), "password": _PASSWORD}
    )
    assert sin_cuenta.status_code == mala_clave.status_code == 401
    # Cuerpo IDENTICO: la API no dice quien tiene cuenta.
    assert sin_cuenta.json() == mala_clave.json()


def test_register_con_email_repetido_da_409(client: TestClient) -> None:
    email, _ = _registrar(client)
    response = client.post(
        "/v1/auth/register", json={"email": email, "password": _PASSWORD}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "email_taken"


def test_me_sin_cabecera_authorization(client: TestClient) -> None:
    assert client.get("/v1/me").status_code == 401


def test_me_con_token_basura(client: TestClient) -> None:
    assert client.get("/v1/me", headers=_auth("esto-no-es-un-jwt")).status_code == 401


# --- OBLIGACION VINCULANTE: el cliente NO puede imponer identidad ------------------


def test_query_user_id_no_cambia_la_identidad(client: TestClient) -> None:
    _, sesion = _registrar(client)
    response = client.get(
        f"/v1/me?user_id={uuid4()}", headers=_auth(sesion["access_token"])
    )
    assert response.status_code == 200
    assert response.json()["user_id"] == sesion["user_id"]


def test_cabecera_x_user_id_no_cambia_la_identidad(client: TestClient) -> None:
    _, sesion = _registrar(client)
    headers = _auth(sesion["access_token"]) | {"X-User-Id": str(uuid4())}
    response = client.get("/v1/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["user_id"] == sesion["user_id"]


def test_query_tenant_id_no_cambia_el_tenant(client: TestClient) -> None:
    _, sesion = _registrar(client)
    propio = client.get("/v1/me", headers=_auth(sesion["access_token"])).json()
    response = client.get(
        f"/v1/me?tenant_id={uuid4()}", headers=_auth(sesion["access_token"])
    )
    assert response.status_code == 200
    assert response.json()["tenant_id"] == propio["tenant_id"]


def test_login_con_tenant_colado_en_el_cuerpo_es_rechazado(client: TestClient) -> None:
    # El contrato (extra="forbid") lo rechaza antes de que nadie lo lea: 422.
    email, _ = _registrar(client)
    response = client.post(
        "/v1/auth/login",
        json={"email": email, "password": _PASSWORD, "tenant_id": str(uuid4())},
    )
    assert response.status_code == 422


# --- Rotacion, reuso y logout ------------------------------------------------------
#
# refresh y logout se autentican POR COOKIE, asi que EXIGEN el token CSRF de doble
# envio. Los tests se adaptan al sistema: aqui se reenvia la cabecera igual que la hara
# nuestro JavaScript, leyendo la cookie CSRF (que es legible a proposito).


def _csrf(client: TestClient) -> dict[str, str]:
    """La cabecera CSRF con el valor de la cookie, como haria el cliente real."""
    return {CSRF_HEADER_NAME: str(client.cookies.get(CSRF_COOKIE_NAME))}


def test_refresh_por_cookie_rota_el_token(client: TestClient) -> None:
    _, sesion = _registrar(client)
    cookie_inicial = client.cookies.get(REFRESH_COOKIE_NAME)

    response = client.post("/v1/auth/refresh", headers=_csrf(client))

    assert response.status_code == 200
    assert response.json()["access_token"]
    assert client.cookies.get(REFRESH_COOKIE_NAME) != cookie_inicial
    assert sesion["user_id"] == response.json()["user_id"]


def _forzar_cookies(client: TestClient, refresh: str, csrf: str) -> None:
    """Deja en el cliente EXACTAMENTE estas cookies.

    Se LIMPIA antes de ponerlas porque tras rotar (o tras el logout) el cliente ya lleva
    otras para el mismo path: sin el clear coexistirian dos, la peticion podria llevar
    la que no es y el test probaria otra cosa. Se fijan en el CLIENTE y no por peticion
    porque pasar cookies sueltas a client.post esta deprecado por ambiguo.
    """
    client.cookies.clear()
    client.cookies.set(REFRESH_COOKIE_NAME, refresh, path=REFRESH_COOKIE_PATH)
    client.cookies.set(CSRF_COOKIE_NAME, csrf, path=REFRESH_COOKIE_PATH)


def test_reusar_el_refresh_anterior_es_robo(client: TestClient) -> None:
    _registrar(client)
    viejo = str(client.cookies.get(REFRESH_COOKIE_NAME))
    csrf_viejo = str(client.cookies.get(CSRF_COOKIE_NAME))
    assert client.post("/v1/auth/refresh", headers=_csrf(client)).status_code == 200

    # El token viejo ya se gasto: usarlo otra vez revoca la familia entera.
    _forzar_cookies(client, viejo, csrf_viejo)
    response = client.post("/v1/auth/refresh", headers=_csrf(client))
    assert response.status_code == 401
    assert response.json()["code"] == "refresh_token_reused"


def test_logout_y_refresh_posterior(client: TestClient) -> None:
    _registrar(client)
    cookie = str(client.cookies.get(REFRESH_COOKIE_NAME))
    csrf = str(client.cookies.get(CSRF_COOKIE_NAME))

    assert client.post("/v1/auth/logout", headers=_csrf(client)).status_code == 204

    _forzar_cookies(client, cookie, csrf)
    assert client.post("/v1/auth/refresh", headers=_csrf(client)).status_code == 401


def test_refresh_sin_cookie(client: TestClient) -> None:
    # Sin sesion no hay cookie CSRF, asi que se fabrica el doble envio a mano: lo que se
    # prueba aqui es la ausencia de refresh, no el CSRF.
    client.cookies.set(CSRF_COOKIE_NAME, "token-csrf", path=REFRESH_COOKIE_PATH)
    response = client.post("/v1/auth/refresh", headers={CSRF_HEADER_NAME: "token-csrf"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_refresh_token"
