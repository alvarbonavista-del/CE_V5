"""La API publica y consume eventos (P06b, Bloque G). PostgreSQL, Redis y bus REALES.

FRONTERA DURA (DOC_ROADMAP, ficha P06b): la API publica y consume eventos, pero NO
EVALUA REGLAS NI EJECUTA ORDENES. Eso es de otras piezas, para siempre. El ultimo test
de este fichero lo hace cumplir: si alguien anade manana un endpoint de ejecucion, se
pone rojo.

DATOS FALSOS SIEMPRE.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ce_v5.core.auth.config import AuthConfig
from ce_v5.core.auth.passwords import Argon2PasswordHasher
from ce_v5.core.auth.rate_limit import RateLimitConfig
from ce_v5.core.auth.service import AuthService
from ce_v5.core.auth.tokens import AccessTokenService
from ce_v5.core.bus import BusMessage
from ce_v5.core.clock.system import SystemClock
from ce_v5.core.policy.cache import (
    CacheKey,
    CapabilitySetCache,
    capabilities_digest,
    resources_digest,
)
from ce_v5.core.policy.cached_evaluator import CachedPolicyEvaluator
from ce_v5.core.policy.evaluator import CapabilitySet, PolicyEvaluator
from ce_v5.core.policy.gate import PolicyGate
from ce_v5.core.policy.invalidation import PolicyCacheInvalidator
from ce_v5.core.policy.providers import (
    StaticIpGeoProvider,
    StaticKycProvider,
    StaticVpnDetector,
)
from ce_v5.entrypoints.api.app import create_app
from ce_v5.entrypoints.api.audit import ApiAuthAuditor
from ce_v5.entrypoints.api.background import (
    POLICY_TOPIC,
    PolicyInvalidationSubscriber,
)
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
from source.envelope import Envelope, EventPayload, Scope
from source.families.policy import (
    InvalidationReason,
    PolicyEventType,
    SubjectInvalidatedPayload,
)
from source.families.registry import payload_class_for
from source.families.user import UserEventType, UserRegisteredPayload

_DSN = os.environ.get("CE_V5_DATABASE_URL")
_REDIS_URL = os.environ.get("CE_V5_REDIS_URL")
pytestmark = pytest.mark.skipif(
    _DSN is None or _REDIS_URL is None,
    reason="requiere CE_V5_DATABASE_URL y CE_V5_REDIS_URL (PostgreSQL y Redis locales)",
)

_CONFIG = AuthConfig(jwt_secret="secreto-de-test-de-32-caracteres-o-mas")
_RATE_CONFIG = RateLimitConfig(digest_secret="secreto-de-huellas-de-32-caracteres")
_PASSWORD = "contrasena-falsa-de-test"

# Las mismas huellas que calcula el CachedPolicyEvaluator para una pregunta.
_DIGEST_RECURSOS = resources_digest(None)
_DIGEST_CAPS = capabilities_digest(["view_dashboard"])

# Las UNICAS rutas que la API expone. Es una lista CERRADA: un endpoint nuevo obliga
# a tocarla, y ese diff es la conversacion que impide que la API se convierta en otra
# cosa.
_RUTAS_PERMITIDAS = {
    ("/v1/auth/register", "POST"),
    ("/v1/auth/login", "POST"),
    ("/v1/auth/refresh", "POST"),
    ("/v1/auth/logout", "POST"),
    ("/v1/me", "GET"),
    ("/v1/capabilities", "GET"),
    ("/v1/realtime", "WEBSOCKET"),
}


@pytest.fixture
def bus() -> RedisEventBus:
    assert _REDIS_URL is not None
    config = RedisBusConfig(url=_REDIS_URL, namespace=f"test-ev-{uuid4().hex}")
    return RedisEventBus(create_client(config), config)


@pytest.fixture
def cache() -> CapabilitySetCache:
    return CapabilitySetCache(SystemClock(), max_staleness_ms=60_000)


def _context(
    app_db: PsycopgDatabase, bus: RedisEventBus, cache: CapabilitySetCache
) -> ApiContext:
    clock = SystemClock()
    tokens = AccessTokenService(_CONFIG, clock)
    sensitive_audit = PostgresSensitiveActionAudit(app_db)
    auditor = ApiAuthAuditor(app_db, sensitive_audit)
    assert _REDIS_URL is not None
    limiter = RedisAuthRateLimiter(
        create_client(RedisBusConfig(url=_REDIS_URL)),
        _RATE_CONFIG,
        prefix=f"test-ev-{uuid4().hex}",
    )
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
        # EL MISMO cache que el gate: si fueran dos, invalidar uno no afectaria al otro.
        invalidator=PolicyCacheInvalidator(cache),
        gate=PolicyGate(cached, sensitive_audit),
        ip_geo=StaticIpGeoProvider({}),
        kyc=StaticKycProvider({}, {}),
        vpn=StaticVpnDetector(frozenset(), frozenset()),
    )


@pytest.fixture
def context(
    app_db: PsycopgDatabase, bus: RedisEventBus, cache: CapabilitySetCache
) -> ApiContext:
    return _context(app_db, bus, cache)


@pytest.fixture
def app(context: ApiContext) -> FastAPI:
    return create_app(context)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _limpiar_outbox(migrator_db: PsycopgDatabase) -> Iterator[None]:
    with migrator_db.transaction() as session:
        session.execute("DELETE FROM outbox")
    yield
    with migrator_db.transaction() as session:
        session.execute("DELETE FROM outbox")


def _alta(client: TestClient) -> tuple[str, dict[str, str]]:
    email = f"test-{uuid4().hex}@ejemplo.test"
    respuesta = client.post(
        "/v1/auth/register", json={"email": email, "password": _PASSWORD}
    )
    assert respuesta.status_code == 201
    return email, respuesta.json()


def _fila_de_outbox(migrator_db: PsycopgDatabase, user_id: str) -> dict[str, Any]:
    with migrator_db.transaction() as session:
        filas = session.fetchall(
            "SELECT event_type, stream_key, idempotency_key, envelope FROM outbox "
            "WHERE stream_key = %s",
            (user_id,),
        )
    assert len(filas) == 1
    fila = filas[0]
    return {
        "event_type": str(fila[0]),
        "stream_key": str(fila[1]),
        "idempotency_key": str(fila[2]),
        "envelope": fila[3],
    }


def test_el_alta_deja_su_evento_en_la_outbox(
    client: TestClient, migrator_db: PsycopgDatabase
) -> None:
    # MISMA TRANSACCION: o existen la cuenta, el tenant, la pertenencia Y el evento,
    # o no existe nada. Un usuario del que el resto del sistema nunca se entero es un
    # fantasma.
    _, sesion = _alta(client)
    user_id = sesion["user_id"]

    fila = _fila_de_outbox(migrator_db, user_id)
    assert fila["event_type"] == UserEventType.REGISTERED.value
    assert fila["idempotency_key"] == f"user.registered:{user_id}"

    with migrator_db.transaction() as session:
        pertenencia = session.fetchone(
            "SELECT tenant_id FROM user_tenant_membership WHERE user_id = %s",
            (user_id,),
        )
        usuario = session.fetchone(
            "SELECT user_id FROM app_user WHERE user_id = %s", (user_id,)
        )
    # Los cuatro hechos existen a la vez, o no existiria ninguno.
    assert usuario is not None
    assert pertenencia is not None


def test_el_envelope_valida_contra_el_registro_de_contratos(
    client: TestClient, migrator_db: PsycopgDatabase
) -> None:
    _, sesion = _alta(client)
    user_id = sesion["user_id"]
    envelope = _fila_de_outbox(migrator_db, user_id)["envelope"]

    # El payload se valida contra su clase CONCRETA, resuelta por event_type (CA-06).
    assert payload_class_for(UserEventType.REGISTERED.value) is UserRegisteredPayload
    payload = UserRegisteredPayload.model_validate(envelope["payload"])
    Envelope[EventPayload].model_validate({**envelope, "payload": {}})

    assert envelope["scope"] == Scope.USER.value
    assert envelope["user_id"] == user_id
    tenant_id = str(envelope["tenant_id"])
    assert tenant_id
    assert payload.user_id == user_id
    assert payload.tenant_id == tenant_id


def test_el_email_no_aparece_en_ninguna_parte_del_envelope(
    client: TestClient, migrator_db: PsycopgDatabase
) -> None:
    # Un evento se publica en un bus, lo consumen procesos que hoy no existen y acaba en
    # logs y en replays. Un email ahi seria repartir un dato personal para siempre.
    email, sesion = _alta(client)
    envelope = _fila_de_outbox(migrator_db, sesion["user_id"])["envelope"]

    crudo = json.dumps(envelope)
    assert email not in crudo
    assert email.split("@")[0] not in crudo
    assert "ejemplo.test" not in crudo


def test_el_publisher_saca_el_evento_al_bus(
    client: TestClient, context: ApiContext, bus: RedisEventBus
) -> None:
    _, sesion = _alta(client)

    # Los tests llaman a drain_once() explicitamente: es mas determinista que esperar a
    # que un bucle de fondo pase por ahi.
    assert context.publisher.drain_once(batch_size=100) >= 1

    recibidos = bus.replay("user", start=None, max_messages=10)
    tipos = [r.message.event_type for r in recibidos]
    assert UserEventType.REGISTERED.value in tipos
    entregado = next(
        r for r in recibidos if r.message.event_type == UserEventType.REGISTERED.value
    )
    envelope = json.loads(entregado.message.envelope)
    assert envelope["user_id"] == sesion["user_id"]


def _sembrar_cache(cache: CapabilitySetCache, tenant_id: str, user_id: str) -> None:
    """Una entrada viva del sujeto, con las mismas huellas que usa el evaluador."""
    cache.put(
        CacheKey(
            tenant_id=tenant_id,
            user_id=user_id,
            policy_version="pv1",
            resources_digest=_DIGEST_RECURSOS,
            capabilities_digest=_DIGEST_CAPS,
        ),
        CapabilitySet(
            tenant_id=tenant_id,
            user_id=user_id,
            policy_version="pv1",
            evaluated_at=0,
            decisions={},
        ),
    )


def test_un_policy_subject_invalidated_del_bus_invalida_el_cache(
    context: ApiContext, bus: RedisEventBus, cache: CapabilitySetCache
) -> None:
    # El kill switch (y cualquier invalidacion) tiene que morder EN CALIENTE, no cuando
    # caduque el TTL.
    tenant_id = str(uuid4())
    user_id = str(uuid4())
    _sembrar_cache(cache, tenant_id, user_id)
    assert cache.find(tenant_id, user_id, _DIGEST_RECURSOS, _DIGEST_CAPS) is not None

    subscriber = PolicyInvalidationSubscriber(bus, context.invalidator)
    # Se sitúa el cursor ANTES de publicar: el subscriber arranca en el final del topic.
    subscriber.start()
    subscriber.stop()

    payload = SubjectInvalidatedPayload(
        tenant_id=tenant_id,
        user_id=user_id,
        reason=InvalidationReason.ROLE_CHANGED,
        policy_version="pv1",
    )
    envelope = Envelope[SubjectInvalidatedPayload](
        event_type=PolicyEventType.SUBJECT_INVALIDATED.value,
        event_schema_version=1,
        source="test",
        idempotency_key=f"inv-{uuid4().hex}",
        stream_key=tenant_id,
        scope=Scope.SYSTEM,
        correlation_id="corr-inv",
        payload=payload,
    )
    bus.publish(
        POLICY_TOPIC,
        BusMessage(
            event_id=str(envelope.event_id),
            event_type=envelope.event_type,
            stream_key=tenant_id,
            idempotency_key=envelope.idempotency_key,
            envelope=json.dumps(envelope.model_dump(mode="json")).encode(),
        ),
    )

    # Una pasada del bucle (sin hilo: determinista).
    subscriber._tick()  # noqa: SLF001

    # La entrada del sujeto se fue: la siguiente evaluacion RECOMPUTA.
    assert cache.find(tenant_id, user_id, _DIGEST_RECURSOS, _DIGEST_CAPS) is None


def _rutas_expuestas(routes: Iterable[Any]) -> set[tuple[str, str]]:
    """Todas las rutas /v1, recorriendo los routers INCLUIDOS.

    app.include_router NO aplana las rutas en app.routes: mete un _IncludedRouter que no
    tiene ni 'path' ni 'methods' y que guarda el router real en 'original_router'. Sin
    descender ahi, el recorrido no ve NINGUNA de nuestras rutas.

    Y las de WebSocket son APIWebSocketRoute: tienen 'path' pero NO tienen 'methods'.
    """
    expuestas: set[tuple[str, str]] = set()
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            expuestas |= _rutas_expuestas(original.routes)
            continue
        path = getattr(route, "path", None)
        if path is None or not str(path).startswith("/v1"):
            continue  # /openapi.json, /docs y demas los pone FastAPI.
        metodos = getattr(route, "methods", None)
        if metodos is None:
            # APIWebSocketRoute: no tiene metodos HTTP.
            expuestas.add((str(path), "WEBSOCKET"))
            continue
        for metodo in metodos:
            if metodo in ("HEAD", "OPTIONS"):
                continue
            expuestas.add((str(path), str(metodo)))
    return expuestas


def test_la_api_no_expone_ninguna_ruta_que_evalue_reglas_ni_ejecute_ordenes(
    app: FastAPI,
) -> None:
    """TEST DE FRONTERA (DOC_ROADMAP, ficha P06b).

    La API es una puerta: autentica, resuelve identidad, informa y delega. NO evalua
    reglas de negocio ni ejecuta ordenes, y eso es para SIEMPRE. Si alguien anade manana
    un endpoint de ejecucion, este test se pone rojo y obliga a la conversacion.
    """
    expuestas = _rutas_expuestas(app.routes)

    # Que el recorrido VE algo: un conjunto vacio pasaria cualquier afirmacion de "no
    # hay endpoints prohibidos" sin probar nada. Si deja de mirar, el test debe fallar.
    assert expuestas

    # IGUALDAD EXACTA contra la lista cerrada: un endpoint NUEVO pone esto rojo. No se
    # relaja a "contiene", porque ahi esta todo el valor del test.
    assert expuestas == _RUTAS_PERMITIDAS

    # Y ninguna huele a ejecucion ni a evaluacion de reglas.
    prohibidas = ("order", "execute", "trade", "rule", "signal", "position")
    for path, _ in expuestas:
        assert not any(palabra in path.lower() for palabra in prohibidas)


def test_el_evento_de_alta_no_lo_produce_nadie_mas(
    migrator_db: PsycopgDatabase,
) -> None:
    # user.registered tiene UN productor: el alta atomica de la API. Su idempotency_key
    # deriva del user_id, asi que un reintento se deduplica en vez de emitir dos hechos.
    with migrator_db.transaction() as session:
        filas = session.fetchall("SELECT idempotency_key FROM outbox")
    claves = [str(f[0]) for f in filas]
    assert len(claves) == len(set(claves))
    for clave in claves:
        if clave.startswith("user.registered:"):
            assert UUID(clave.split(":", 1)[1])
