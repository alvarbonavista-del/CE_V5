"""Canal realtime autenticado (P06b, dictamen CSA K/L; prueba 14).

EL TOKEN NUNCA VIAJA EN LA URL EN NINGUNO DE ESTOS TESTS, Y ESO SE COMPRUEBA: siempre se
conecta a "/v1/realtime" a secas y el token va en el PRIMER MENSAJE. Una URL queda
escrita
en logs, historial y Referer; un token ahi es un token publicado.

PostgreSQL, Redis y el bus REALES. DATOS FALSOS SIEMPRE.
"""

from __future__ import annotations

import json
import os
import uuid as uuid_mod
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
from ce_v5.entrypoints.api.realtime import MAX_MESSAGE_BYTES, SUBSCRIBE_REALTIME
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
_VERSION = "pv_realtime_test"
_TOPIC = "component"
_RUTA = "/v1/realtime"  # sin query: el token JAMAS viaja en la URL.


@pytest.fixture
def bus() -> RedisEventBus:
    assert _REDIS_URL is not None
    config = RedisBusConfig(url=_REDIS_URL, namespace=f"test-rt-{uuid4().hex}")
    return RedisEventBus(create_client(config), config)


def _seed_policy(migrator_db: PsycopgDatabase, *, permitir: bool) -> None:
    with migrator_db.transaction() as session:
        session.execute("DELETE FROM policy_rule")
        session.execute("DELETE FROM policy_version")
        session.execute(
            "INSERT INTO policy_version (policy_version, status, actor) "
            "VALUES (%s, 'current', 'seed')",
            (_VERSION,),
        )
        if permitir:
            session.execute(
                "INSERT INTO policy_rule (rule_id, policy_version, capability_id, "
                "effect, reason_code) "
                "VALUES (%s, %s, %s, 'allow', 'allowed_by_policy')",
                (str(uuid4()), _VERSION, SUBSCRIBE_REALTIME),
            )


@pytest.fixture(autouse=True)
def _limpiar_politica(migrator_db: PsycopgDatabase) -> Iterator[None]:
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
        prefix=f"test-rt-{uuid4().hex}",
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


def _alta(client: TestClient) -> dict[str, str]:
    respuesta = client.post(
        "/v1/auth/register",
        json={
            "email": f"test-{uuid4().hex}@ejemplo.test",
            "password": _PASSWORD,
        },
    )
    assert respuesta.status_code == 201
    return dict(respuesta.json())


def _me(client: TestClient, token: str) -> dict[str, str]:
    return dict(
        client.get("/v1/me", headers={"Authorization": f"Bearer {token}"}).json()
    )


def _publicar(
    bus: RedisEventBus, scope: str, tenant_id: str | None, user_id: str | None
) -> None:
    envelope: dict[str, Any] = {
        "event_id": str(uuid4()),
        "event_type": "component.demo",
        "envelope_version": 1,
        "event_schema_version": 1,
        "source": "test-realtime",
        "idempotency_key": f"rt-{uuid4().hex}",
        "stream_key": "rt",
        "scope": scope,
        "correlation_id": "corr-rt",
        "payload": {},
    }
    if tenant_id is not None:
        envelope["tenant_id"] = tenant_id
    if user_id is not None:
        envelope["user_id"] = user_id
    bus.publish(
        _TOPIC,
        BusMessage(
            event_id=str(envelope["event_id"]),
            event_type="component.demo",
            stream_key="rt",
            idempotency_key=str(envelope["idempotency_key"]),
            envelope=json.dumps(envelope).encode(),
        ),
    )


def _autenticar_y_suscribir(
    ws: Any, token: str, checkpoint: str | None = None
) -> dict[str, Any]:
    ws.send_text(json.dumps({"type": "auth", "access_token": token}))
    ws.send_text(
        json.dumps({"type": "subscribe", "topic": _TOPIC, "checkpoint": checkpoint})
    )
    return dict(json.loads(ws.receive_text()))


# --- PRUEBA 14: el handshake -------------------------------------------------------


def test_prueba_14_sin_mensaje_de_auth_se_cierra(
    client: TestClient, migrator_db: PsycopgDatabase
) -> None:
    _seed_policy(migrator_db, permitir=True)
    with pytest.raises(Exception):  # noqa: B017,PT011
        with client.websocket_connect(_RUTA) as ws:
            # No se manda nada: el servidor cierra al agotarse el plazo de auth.
            ws.receive_text()


def test_prueba_14_un_token_basura_cierra_el_canal(
    client: TestClient, migrator_db: PsycopgDatabase
) -> None:
    _seed_policy(migrator_db, permitir=True)
    with pytest.raises(Exception):  # noqa: B017,PT011
        with client.websocket_connect(_RUTA) as ws:
            ws.send_text(
                json.dumps({"type": "auth", "access_token": "esto-no-es-un-jwt"})
            )
            ws.receive_text()


def test_prueba_14_un_token_valido_se_acepta(
    client: TestClient, migrator_db: PsycopgDatabase
) -> None:
    _seed_policy(migrator_db, permitir=True)
    sesion = _alta(client)

    with client.websocket_connect(_RUTA) as ws:
        ack = _autenticar_y_suscribir(ws, sesion["access_token"])

    assert ack["type"] == "ack"
    assert ack["topic"] == _TOPIC


# --- El borde gateado: un "no lo se" se responde que no ------------------------------


def test_la_suscripcion_sin_allow_explicito_se_rechaza(
    client: TestClient, migrator_db: PsycopgDatabase
) -> None:
    # SIN regla: la capability no esta en el reglamento -> NOT_APPLICABLE. Eso NO es un
    # permiso: es un "no lo se", y en un borde publico se responde que no.
    _seed_policy(migrator_db, permitir=False)
    sesion = _alta(client)

    with pytest.raises(Exception):  # noqa: B017,PT011
        with client.websocket_connect(_RUTA) as ws:
            _autenticar_y_suscribir(ws, sesion["access_token"])


def test_un_mensaje_con_identidad_colada_es_rechazado(
    client: TestClient, migrator_db: PsycopgDatabase
) -> None:
    # El contrato prohibe campos extra: el cliente NO puede imponer identidad ni tenant.
    _seed_policy(migrator_db, permitir=True)
    sesion = _alta(client)

    with pytest.raises(Exception):  # noqa: B017,PT011
        with client.websocket_connect(_RUTA) as ws:
            ws.send_text(
                json.dumps({"type": "auth", "access_token": sesion["access_token"]})
            )
            ws.send_text(
                json.dumps(
                    {
                        "type": "subscribe",
                        "topic": _TOPIC,
                        "tenant_id": str(uuid4()),
                        "user_id": str(uuid4()),
                    }
                )
            )
            ws.receive_text()


# --- Entrega fail-closed por scope --------------------------------------------------


def test_un_evento_del_propio_tenant_llega(
    client: TestClient, migrator_db: PsycopgDatabase, bus: RedisEventBus
) -> None:
    _seed_policy(migrator_db, permitir=True)
    sesion = _alta(client)
    yo = _me(client, sesion["access_token"])

    with client.websocket_connect(_RUTA) as ws:
        _autenticar_y_suscribir(ws, sesion["access_token"])
        _publicar(bus, "tenant", yo["tenant_id"], None)
        evento = json.loads(ws.receive_text())

    assert evento["type"] == "event"
    assert evento["envelope"]["tenant_id"] == yo["tenant_id"]
    assert evento["checkpoint"]


def test_un_evento_de_otro_tenant_no_llega(
    client: TestClient, migrator_db: PsycopgDatabase, bus: RedisEventBus
) -> None:
    _seed_policy(migrator_db, permitir=True)
    sesion = _alta(client)
    yo = _me(client, sesion["access_token"])

    with client.websocket_connect(_RUTA) as ws:
        _autenticar_y_suscribir(ws, sesion["access_token"])
        # Primero el ajeno, despues el propio: si el ajeno se colara, llegaria ANTES.
        _publicar(bus, "tenant", str(uuid4()), None)
        _publicar(bus, "tenant", yo["tenant_id"], None)
        evento = json.loads(ws.receive_text())

    assert evento["envelope"]["tenant_id"] == yo["tenant_id"]


def test_un_evento_de_scope_system_no_llega(
    client: TestClient, migrator_db: PsycopgDatabase, bus: RedisEventBus
) -> None:
    # Los eventos de plataforma NO son de un usuario: no se entregan (fail-closed).
    _seed_policy(migrator_db, permitir=True)
    sesion = _alta(client)
    yo = _me(client, sesion["access_token"])

    with client.websocket_connect(_RUTA) as ws:
        _autenticar_y_suscribir(ws, sesion["access_token"])
        _publicar(bus, "system", None, None)
        _publicar(bus, "tenant", yo["tenant_id"], None)
        evento = json.loads(ws.receive_text())

    assert evento["envelope"]["scope"] == "tenant"


# --- Limite de mensaje y checkpoint -------------------------------------------------


def test_un_mensaje_demasiado_grande_cierra_la_conexion(
    client: TestClient, migrator_db: PsycopgDatabase
) -> None:
    # Un WebSocket sin limite de mensaje es una via de agotamiento de memoria.
    _seed_policy(migrator_db, permitir=True)
    sesion = _alta(client)

    with pytest.raises(Exception):  # noqa: B017,PT011
        with client.websocket_connect(_RUTA) as ws:
            _autenticar_y_suscribir(ws, sesion["access_token"])
            ws.send_text(json.dumps({"relleno": "a" * (MAX_MESSAGE_BYTES + 10)}))
            ws.receive_text()


def test_el_checkpoint_reanuda_sin_perder_ni_duplicar(
    client: TestClient, migrator_db: PsycopgDatabase, bus: RedisEventBus
) -> None:
    _seed_policy(migrator_db, permitir=True)
    sesion = _alta(client)
    yo = _me(client, sesion["access_token"])
    token = sesion["access_token"]

    with client.websocket_connect(_RUTA) as ws:
        _autenticar_y_suscribir(ws, token)
        _publicar(bus, "tenant", yo["tenant_id"], None)
        primero = json.loads(ws.receive_text())

    # Mientras el cliente esta desconectado, se publica otro evento.
    _publicar(bus, "tenant", yo["tenant_id"], None)

    with client.websocket_connect(_RUTA) as ws:
        _autenticar_y_suscribir(ws, token, checkpoint=primero["checkpoint"])
        segundo = json.loads(ws.receive_text())

    # Ni se pierde el de la desconexion ni se repite el ya visto (replay es exclusivo).
    assert segundo["type"] == "event"
    assert segundo["envelope"]["event_id"] != primero["envelope"]["event_id"]
    assert uuid_mod.UUID(str(segundo["envelope"]["event_id"]))
