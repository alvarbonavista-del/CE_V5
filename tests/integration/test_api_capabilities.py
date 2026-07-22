"""Tests de /v1/capabilities contra PostgreSQL real (P06b, ADR-012, D9).

Lo que se prueba aqui es una vista de CORTESIA: informa a la UI, no autoriza a nadie.
Por eso el contrato lleva advisory=true y por eso una capacidad SENSIBLE sin entitlement
sale DENY aunque ninguna regla la prohiba (D6: lo sensible exige concesion explicita; el
silencio no concede).

La politica se siembra con el rol de MIGRACIONES (el de aplicacion no puede escribir el
catalogo, y eso mismo esta probado en test_policy_store.py). DATOS FALSOS SIEMPRE.
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
_VERSION = "pv_capabilities_test"
_NO_SENSIBLE = "view_dashboard"
_SENSIBLE = "execute_order"


def _wipe(db: PsycopgDatabase) -> None:
    with db.transaction() as session:
        session.execute("DELETE FROM policy_rule")
        session.execute("DELETE FROM policy_override")
        session.execute("DELETE FROM policy_entitlement")
        # La auditoria del operador REFERENCIA los kill switches: no se puede borrar un
        # hecho al que apunta su traza. Se limpia en orden. Ojo: esto lo hace el rol de
        # MIGRACIONES en una base de JUGUETE; los roles de runtime NO pueden borrar
        # auditoria, y el check "audit" lo verifica en cada build.
        session.execute("DELETE FROM operator_audit")
        session.execute("DELETE FROM kill_switch")
        session.execute("DELETE FROM policy_version")


@pytest.fixture(autouse=True)
def _politica(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """Reglamento de juguete: ALLOW para las dos capacidades, sin entitlements."""
    _wipe(migrator_db)
    with migrator_db.transaction() as session:
        session.execute(
            "INSERT INTO policy_version (policy_version, status, actor) "
            "VALUES (%s, 'current', 'seed')",
            (_VERSION,),
        )
        for capability_id in (_NO_SENSIBLE, _SENSIBLE):
            session.execute(
                "INSERT INTO policy_rule (rule_id, policy_version, capability_id, "
                "effect, reason_code) "
                "VALUES (%s, %s, %s, 'allow', 'allowed_by_policy')",
                (str(uuid4()), _VERSION, capability_id),
            )
    yield
    _wipe(migrator_db)


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


def _token(client: TestClient) -> str:
    response = client.post(
        "/v1/auth/register",
        json={"email": f"test-{uuid4().hex}@ejemplo.test", "password": _PASSWORD},
    )
    assert response.status_code == 201
    return str(response.json()["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_sin_token_no_hay_capabilities(client: TestClient) -> None:
    assert client.get("/v1/capabilities").status_code == 401


def test_devuelve_una_decision_por_capability_pedida(client: TestClient) -> None:
    token = _token(client)
    response = client.get(
        f"/v1/capabilities?capability={_NO_SENSIBLE}&capability={_SENSIBLE}",
        headers=_auth(token),
    )

    assert response.status_code == 200
    cuerpo = response.json()
    assert cuerpo["advisory"] is True
    assert [d["capability_id"] for d in cuerpo["decisions"]] == [
        _NO_SENSIBLE,
        _SENSIBLE,
    ]
    assert cuerpo["policy_version"] == _VERSION


def test_capacidad_no_sensible_permitida_por_una_regla(client: TestClient) -> None:
    token = _token(client)
    cuerpo = client.get(
        f"/v1/capabilities?capability={_NO_SENSIBLE}", headers=_auth(token)
    ).json()

    decision = cuerpo["decisions"][0]
    assert decision["decision"] == "allow"
    assert decision["sensitive"] is False


def test_capacidad_sensible_sin_entitlement_es_denegada(client: TestClient) -> None:
    # D6: una capacidad sensible exige entitlement explicito. Hay una regla ALLOW y aun
    # asi se deniega: el silencio (o una regla generosa) no concede lo sensible.
    token = _token(client)
    cuerpo = client.get(
        f"/v1/capabilities?capability={_SENSIBLE}", headers=_auth(token)
    ).json()

    decision = cuerpo["decisions"][0]
    assert decision["decision"] == "deny"
    assert decision["sensitive"] is True
    # Sin proveedores no hay jurisdiccion conocida: eso ya deniega antes que el
    # entitlement (D5). Cualquiera de los dos motivos es fail-closed y ninguno concede.
    assert decision["reason_code"].startswith("denied_")


def test_mas_de_cincuenta_capabilities_se_rechaza(client: TestClient) -> None:
    token = _token(client)
    query = "&".join(f"capability=cap_{i}" for i in range(51))
    response = client.get(f"/v1/capabilities?{query}", headers=_auth(token))
    assert response.status_code == 422


def test_advisory_es_exactamente_true(client: TestClient) -> None:
    # Recordatorio en el propio contrato: esto NO autoriza nada. La decision
    # autoritativa se vuelve a tomar en el backend, en el punto sensible.
    token = _token(client)
    cuerpo = client.get(
        f"/v1/capabilities?capability={_NO_SENSIBLE}", headers=_auth(token)
    ).json()
    assert cuerpo["advisory"] is True
