"""Composition root de la API (ADR-002, DOC_ESTRUCTURA sec.6).

La API es un PROCESO PROPIO: no hay ningun main.py gigante que arranque todo. Aqui, y
solo aqui, se cablean los adapters concretos (PostgreSQL, Argon2, JWT); el resto del
codigo depende de puertos.

GUARDIA CA-03: DbConfig.from_env ABORTA el arranque si el DSN de OPERADOR esta en el
entorno. La API nunca porta esa credencial, y quien lo hace cumplir es el CODIGO.
"""

from __future__ import annotations

from dataclasses import dataclass

from ce_v5.core.auth.audit import AuthAuditor
from ce_v5.core.auth.config import AuthConfig
from ce_v5.core.auth.passwords import Argon2PasswordHasher
from ce_v5.core.auth.rate_limit import AuthRateLimiter, RateLimitConfig
from ce_v5.core.auth.service import AuthService
from ce_v5.core.auth.tokens import AccessTokenService
from ce_v5.core.bus import EventBus
from ce_v5.core.clock.system import SystemClock
from ce_v5.core.policy.cache import CapabilitySetCache
from ce_v5.core.policy.cached_evaluator import CachedPolicyEvaluator
from ce_v5.core.policy.evaluator import PolicyEvaluator
from ce_v5.core.policy.gate import PolicyGate
from ce_v5.core.policy.invalidation import PolicyCacheInvalidator
from ce_v5.core.policy.providers import (
    IpGeoProvider,
    KycProvider,
    StaticIpGeoProvider,
    StaticKycProvider,
    StaticVpnDetector,
    VpnDetector,
)
from ce_v5.entrypoints.api.audit import ApiAuthAuditor
from ce_v5.entrypoints.api.config import ApiConfig
from ce_v5.entrypoints.api.startup_guards import assert_secure_startup
from ce_v5.infra.bus_redis import RedisBusConfig, RedisEventBus, create_client
from ce_v5.infra.db.config import DbConfig
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

# TTL del cache del capability set: RED DE SEGURIDAD, no mecanismo de frescura. Lo que
# propaga un cambio de politica es la INVALIDACION POR EVENTO (ADR-012); el TTL solo
# cubre el caso de que un evento se pierda. Nunca al reves.
_MAX_STALENESS_MS = 60_000


@dataclass(frozen=True, slots=True)
class ApiContext:
    """Todo lo que la API necesita, ya cableado."""

    auth: AuthService
    tokens: AccessTokenService
    scoped_db: TenantScopedDatabase
    config: AuthConfig
    api_config: ApiConfig
    limiter: AuthRateLimiter
    rate_config: RateLimitConfig
    auditor: AuthAuditor
    bus: EventBus
    publisher: OutboxPublisher
    invalidator: PolicyCacheInvalidator
    gate: PolicyGate
    ip_geo: IpGeoProvider
    kyc: KycProvider
    vpn: VpnDetector


def build_context() -> ApiContext:
    """Cablea la API desde el entorno. Falla al arrancar si falta configuracion."""
    clock = SystemClock()
    database = PsycopgDatabase(DbConfig.from_env())
    config = AuthConfig.from_env()
    tokens = AccessTokenService(config, clock)
    # El limitador vive en Redis: estado EFIMERO con TTL, jamas una tabla (CA-10).
    rate_config = RateLimitConfig.from_env()
    bus_config = RedisBusConfig.from_env()
    redis_client = create_client(bus_config)
    limiter = RedisAuthRateLimiter(redis_client, rate_config)
    bus = RedisEventBus(create_client(bus_config), bus_config)
    api_config = ApiConfig.from_env()
    policy_store = PostgresPolicyStore(database)
    sensitive_audit = PostgresSensitiveActionAudit(database)
    # El auditor pregunta al store que version estaba VIGENTE: para un hecho de auth es
    # CONTEXTO, no fundamento (CA-11).
    auditor = ApiAuthAuditor(database, sensitive_audit, policy_store)

    # ANTES de devolver nada: una configuracion insegura no se avisa, SE RECHAZA.
    assert_secure_startup(api_config, config, rate_config, database)

    auth = AuthService(
        credentials=PostgresCredentialReader(database),
        # El registrar encola user.registered en la MISMA transaccion del alta.
        registrar=PostgresUserRegistrar(database, clock),
        sessions=PostgresSessionStore(database),
        hasher=Argon2PasswordHasher(),
        tokens=tokens,
        clock=clock,
        config=config,
        limiter=limiter,
        rate_config=rate_config,
        auditor=auditor,
    )
    # Cadena real de politica (P06), la misma del arnes de validacion en caliente.
    evaluator = PolicyEvaluator(policy_store, clock)
    cache = CapabilitySetCache(clock, max_staleness_ms=_MAX_STALENESS_MS)
    cached = CachedPolicyEvaluator(evaluator, cache)
    gate = PolicyGate(cached, sensitive_audit)
    # EL MISMO cache que usa el gate: si fueran dos objetos, invalidar uno no
    # afectaria al otro y el kill switch no morderia (el gate seguiria sirviendo su
    # entrada vieja).
    invalidator = PolicyCacheInvalidator(cache)
    publisher = OutboxPublisher(db=database, bus=bus)
    return ApiContext(
        auth=auth,
        tokens=tokens,
        scoped_db=TenantScopedDatabase(database),
        config=config,
        api_config=api_config,
        limiter=limiter,
        rate_config=rate_config,
        auditor=auditor,
        bus=bus,
        publisher=publisher,
        invalidator=invalidator,
        gate=gate,
        # Proveedores VACIOS a proposito: en v5.0 no hay proveedor real de
        # geolocalizacion, KYC ni deteccion de VPN (su seleccion es frontera comercial
        # de Alvaro). El resultado es jurisdiccion DESCONOCIDA, KYC UNKNOWN y VPN
        # INDETERMINADA, que por D5/D6 de P06 DENIEGAN toda capacidad sensible.
        # Fail-closed por ausencia de dato, que es la respuesta correcta: sin saber de
        # donde llama alguien, no se le deja ejecutar nada.
        ip_geo=StaticIpGeoProvider({}),
        kyc=StaticKycProvider({}, {}),
        vpn=StaticVpnDetector(frozenset(), frozenset()),
    )
