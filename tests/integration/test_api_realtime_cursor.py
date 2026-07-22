"""La semantica del cursor del canal realtime (P06b, CA-12 punto 4).

Este test corre contra REDIS REAL a proposito. El doble en memoria es exactamente lo que
escondio el defecto de payload de P03 (sus tests usaban un event_type inexistente con
payload vacio y todo salia verde). Ya nos mordio una vez: la semantica del cursor se
prueba contra el motor de verdad.

QUE DEFIENDE: una suscripcion SIN checkpoint arranca en el FINAL REAL del topic. Con el
apano anterior (leer los primeros 100 mensajes del historico y tomar el ultimo de ESA
ventana como "el final"), un topic con mas de 100 mensajes dejaba el cursor clavado en
el mensaje 100 y el cliente recibia eventos ANTIGUOS COMO SI FUERAN NUEVOS. Con 250
eventos historicos, este test lo destapa.

DATOS FALSOS SIEMPRE.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ce_v5.core.auth.config import AuthConfig
from ce_v5.core.auth.passwords import Argon2PasswordHasher
from ce_v5.core.auth.rate_limit import RateLimitConfig
from ce_v5.core.auth.service import AuthService
from ce_v5.core.auth.tokens import AccessTokenService
from ce_v5.core.bus import BusMessage
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
from ce_v5.entrypoints.api.realtime import SUBSCRIBE_REALTIME
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
_VERSION = "pv_cursor_test"
_TOPIC = "component"
_RUTA = "/v1/realtime"
# MAS DEL DOBLE del apano de 100: con la ventana vieja, el cursor se quedaria clavado en
# el mensaje 100 y el cliente recibiria del 101 en adelante como si fueran nuevos.
_HISTORICOS = 250


@pytest.fixture
def bus() -> RedisEventBus:
    assert _REDIS_URL is not None
    config = RedisBusConfig(url=_REDIS_URL, namespace=f"test-cursor-{uuid4().hex}")
    return RedisEventBus(create_client(config), config)


@pytest.fixture(autouse=True)
def _politica(migrator_db: PsycopgDatabase) -> Iterator[None]:
    with migrator_db.transaction() as session:
        session.execute("DELETE FROM policy_rule")
        session.execute("DELETE FROM policy_version")
        session.execute(
            "INSERT INTO policy_version (policy_version, status, actor) "
            "VALUES (%s, 'current', 'seed')",
            (_VERSION,),
        )
        session.execute(
            "INSERT INTO policy_rule (rule_id, policy_version, capability_id, "
            "effect, reason_code) "
            "VALUES (%s, %s, %s, 'allow', 'allowed_by_policy')",
            (str(uuid4()), _VERSION, SUBSCRIBE_REALTIME),
        )
    yield
    with migrator_db.transaction() as session:
        session.execute("DELETE FROM policy_rule")
        session.execute("DELETE FROM policy_version")


def _context(app_db: PsycopgDatabase, bus: RedisEventBus) -> ApiContext:
    clock = SystemClock()
    tokens = AccessTokenService(_CONFIG, clock)
    sensitive_audit = PostgresSensitiveActionAudit(app_db)
    auditor = ApiAuthAuditor(app_db, sensitive_audit)
    assert _REDIS_URL is not None
    limiter = RedisAuthRateLimiter(
        create_client(RedisBusConfig(url=_REDIS_URL)),
        _RATE_CONFIG,
        prefix=f"test-cursor-{uuid4().hex}",
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
        market_db=app_db,
        config=_CONFIG,
        api_config=ApiConfig(),
        limiter=limiter,
        rate_config=_RATE_CONFIG,
        auditor=auditor,
        bus=bus,
        publisher=OutboxPublisher(db=app_db, bus=bus),
        invalidator=PolicyCacheInvalidator(cache),
        gate=PolicyGate(cached, sensitive_audit),
        ip_geo=StaticIpGeoProvider({}),
        kyc=StaticKycProvider({}, {}),
        vpn=StaticVpnDetector(frozenset(), frozenset()),
    )


@pytest.fixture
def client(app_db: PsycopgDatabase, bus: RedisEventBus) -> Iterator[TestClient]:
    with TestClient(
        create_app(_context(app_db, bus)),
        base_url="https://testserver",
        client=("203.0.113.10", 12345),
    ) as test_client:
        yield test_client


def _publicar(bus: RedisEventBus, tenant_id: str, marca: str) -> str:
    """Un envelope VALIDO del contrato canonico (component.registered, CA-06)."""
    event_id = str(uuid4())
    envelope: dict[str, Any] = {
        "event_id": event_id,
        "event_type": "component.registered",
        "envelope_version": 1,
        "event_schema_version": 1,
        "source": "test-cursor",
        "idempotency_key": f"cursor-{marca}-{event_id}",
        "stream_key": "cursor",
        "scope": "tenant",
        "tenant_id": tenant_id,
        "correlation_id": "corr-cursor",
        "payload": {"marca": marca},
    }
    bus.publish(
        _TOPIC,
        BusMessage(
            event_id=event_id,
            event_type="component.registered",
            stream_key="cursor",
            idempotency_key=str(envelope["idempotency_key"]),
            envelope=json.dumps(envelope).encode(),
        ),
    )
    return event_id


def test_una_suscripcion_sin_checkpoint_no_recibe_historia(
    client: TestClient, bus: RedisEventBus
) -> None:
    # 1. Alta y tenant del sujeto.
    respuesta = client.post(
        "/v1/auth/register",
        json={"email": f"test-{uuid4().hex}@ejemplo.test", "password": _PASSWORD},
    )
    assert respuesta.status_code == 201
    sesion = respuesta.json()
    yo = client.get(
        "/v1/me", headers={"Authorization": f"Bearer {sesion['access_token']}"}
    ).json()
    tenant_id = str(yo["tenant_id"])

    # 2. HISTORICO: 250 eventos del propio tenant, ANTES de suscribirse. Todos ellos
    # pasarian el filtro de scope, asi que si el cursor mintiera, llegarian.
    historicos = {_publicar(bus, tenant_id, "historico") for _ in range(_HISTORICOS)}
    assert len(historicos) == _HISTORICOS

    with client.websocket_connect(_RUTA) as ws:
        # 3. Suscripcion SIN checkpoint: arranca en el FINAL REAL del topic.
        ws.send_text(
            json.dumps({"type": "auth", "access_token": sesion["access_token"]})
        )
        ws.send_text(
            json.dumps({"type": "subscribe", "topic": _TOPIC, "checkpoint": None})
        )
        ack = json.loads(ws.receive_text())
        assert ack["type"] == "ack"

        # 4. UN evento nuevo, despues de la suscripcion.
        nuevo = _publicar(bus, tenant_id, "nuevo")

        recibido = json.loads(ws.receive_text())

    # EXACTAMENTE el evento nuevo, y CERO historicos. Con el apano de los 100, el
    # primero en llegar seria el historico numero 101.
    assert recibido["type"] == "event"
    assert recibido["envelope"]["event_id"] == nuevo
    assert recibido["envelope"]["payload"]["marca"] == "nuevo"
    assert recibido["envelope"]["event_id"] not in historicos
