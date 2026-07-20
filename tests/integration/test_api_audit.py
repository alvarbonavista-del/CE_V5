"""La auditoria de autenticacion, PARTIDA EN DOS (P06b, dictamen CSA N).

Lo que tiene dueno va a sensitive_action_audit. Lo que NO lo tiene (un login fallido: no
sabemos quien llama, esa es justo la cuestion) va al LOG, con HUELLAS, jamas con el
email:
guardar los emails que fallan seria construir la lista de emails atacados.

DATOS FALSOS SIEMPRE.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from uuid import UUID, uuid4

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
from ce_v5.core.policy.gate import PolicyDenied, PolicyGate
from ce_v5.core.policy.inputs import (
    EvidenceSource,
    KycStatus,
    PolicyInputs,
    ResolvedJurisdiction,
)
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
from ce_v5.entrypoints.api.observability import CORRELATION_HEADER
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
    assert _REDIS_URL is not None
    limiter = RedisAuthRateLimiter(
        create_client(RedisBusConfig(url=_REDIS_URL)),
        _RATE_CONFIG,
        prefix=f"test-audit-{uuid4().hex}",
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
        create_app(_context(app_db)),
        base_url="https://testserver",
        client=("203.0.113.10", 12345),
    ) as test_client:
        yield test_client


def _email() -> str:
    return f"test-{uuid4().hex}@ejemplo.test"


def _alta(client: TestClient) -> tuple[str, dict[str, str]]:
    email = _email()
    response = client.post(
        "/v1/auth/register", json={"email": email, "password": _PASSWORD}
    )
    assert response.status_code == 201
    return email, response.json()


def _filas_de_auditoria(
    migrator_db: PsycopgDatabase, user_id: str
) -> list[tuple[object, ...]]:
    """Se leen con el rol de MIGRACIONES: el de aplicacion solo ve su propio tenant."""
    with migrator_db.transaction() as session:
        return session.fetchall(
            "SELECT capability_id, decision, sensitive, audit_kind, reason_code "
            "FROM sensitive_action_audit WHERE user_id = %s ORDER BY evaluated_at",
            (user_id,),
        )


def test_un_registro_deja_su_fila_de_auditoria(
    client: TestClient, migrator_db: PsycopgDatabase
) -> None:
    # Una cuenta que nace y opera sin dejar rastro seria un agujero de auditoria. El
    # alta es atomica: hay usuario y tenant desde el primer instante, asi que el hecho
    # tiene dueno y le corresponde su fila.
    _, sesion = _alta(client)

    filas = _filas_de_auditoria(migrator_db, sesion["user_id"])
    alta = next(fila for fila in filas if str(fila[0]) == "auth.register")
    assert str(alta[3]) == "auth"
    assert str(alta[4]) == "auth_registered"
    assert str(alta[1]) == "allow"


def test_un_login_correcto_deja_su_fila_de_auditoria(
    client: TestClient, migrator_db: PsycopgDatabase
) -> None:
    email, sesion = _alta(client)
    assert (
        client.post(
            "/v1/auth/login", json={"email": email, "password": _PASSWORD}
        ).status_code
        == 200
    )

    filas = _filas_de_auditoria(migrator_db, sesion["user_id"])
    capacidades = [str(fila[0]) for fila in filas]
    # El alta y la entrada son dos hechos distintos, y los dos dejan rastro.
    assert "auth.register" in capacidades
    assert "auth.login" in capacidades
    # No son capacidades sensibles del catalogo de politica (D1 de P06).
    assert all(fila[2] is False for fila in filas)

    # CA-11: la fila declara SU tipo y usa SU vocabulario. Nada de motivos de politica
    # tomados prestados para un hecho de autenticacion.
    login = next(fila for fila in filas if str(fila[0]) == "auth.login")
    assert str(login[3]) == "auth"
    assert str(login[4]) == "auth_login_succeeded"


def test_ninguna_fila_de_auth_usa_un_motivo_de_politica(
    client: TestClient, migrator_db: PsycopgDatabase
) -> None:
    """La afirmacion que hace cumplir CA-11 PARA SIEMPRE.

    Antes se tomaban prestados motivos de politica (denied_not_evaluated) para hechos de
    auth: una traza que MENTIA en su columna de motivo. Este test lo impide en el
    futuro: toda fila de audit_kind='auth' usa un motivo del vocabulario de auth.
    """
    email, sesion = _alta(client)
    client.post("/v1/auth/login", json={"email": email, "password": _PASSWORD})

    filas = _filas_de_auditoria(migrator_db, sesion["user_id"])
    de_auth = [fila for fila in filas if str(fila[3]) == "auth"]
    assert de_auth
    for fila in de_auth:
        assert str(fila[4]).startswith("auth_")


def test_los_dos_tipos_de_auditoria_conviven_sin_confundirse(
    client: TestClient, app_db: PsycopgDatabase, migrator_db: PsycopgDatabase
) -> None:
    """Una fila de auth y una de politica, en la misma tabla, discriminadas (CA-11)."""
    _, sesion = _alta(client)
    user_id = sesion["user_id"]

    # El tenant se resuelve por el CAMINO REAL del backend: TenantScopedDatabase fija
    # app.current_user_id y app.current_tenant_id dentro de la transaccion, y de ahi
    # sale el contexto ya resuelto (ni hace falta consultar la pertenencia a mano).
    #
    # Con app_db a pelo NO se puede: una transaccion del rol de aplicacion que no dice
    # QUIEN es no ve NADA. La policy de RLS de P05 compara contra
    # app_current_tenant_id(), que sin fijar vale NULL, y una comparacion NULL no casa
    # con ninguna fila. No es un fallo del test: es el aislamiento funcionando.
    with TenantScopedDatabase(app_db).transaction(UUID(user_id)) as scoped:
        tenant_id = str(scoped.context.tenant_id)

    # Una decision del GATE (politica): sin reglamento vigente, deniega. Lo que importa
    # aqui no es el veredicto, sino que la fila se declara 'policy'.
    contexto = _context(app_db)
    inputs = PolicyInputs(
        subject_tenant_id=tenant_id,
        subject_user_id=user_id,
        jurisdiction=ResolvedJurisdiction(
            jurisdiction="AA", source=EvidenceSource.KYC, conflicting=False
        ),
        kyc_status=KycStatus.VERIFIED,
        vpn_detected=False,
        plan=None,
        role=None,
    )
    with pytest.raises(PolicyDenied):
        contexto.gate.require(inputs, "execute_order")

    tipos = {str(fila[3]) for fila in _filas_de_auditoria(migrator_db, user_id)}
    assert tipos == {"auth", "policy"}


def test_un_login_fallido_no_deja_fila_no_tiene_dueno(
    client: TestClient, migrator_db: PsycopgDatabase
) -> None:
    email, sesion = _alta(client)
    antes = len(_filas_de_auditoria(migrator_db, sesion["user_id"]))

    assert (
        client.post(
            "/v1/auth/login", json={"email": email, "password": "no-es-la-clave"}
        ).status_code
        == 401
    )

    # El hecho vive en el LOG: no hay tenant al que atarlo, y guardar el email que falla
    # seria construir la lista de emails atacados.
    assert len(_filas_de_auditoria(migrator_db, sesion["user_id"])) == antes


def test_el_log_de_un_login_fallido_no_lleva_el_email(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    email, _ = _alta(client)

    with caplog.at_level(logging.INFO, logger="ce_v5"):
        client.post("/v1/auth/login", json={"email": email, "password": "mala"})

    lineas = [json.loads(r.message) for r in caplog.records]
    fallidos = [linea for linea in lineas if linea["event"] == "auth.login_failed"]
    assert fallidos
    fallo = fallidos[0]
    # Huella, no email. Ni el email ni su parte local aparecen en ningun sitio.
    assert email not in json.dumps(fallo)
    assert fallo["account"]
    assert fallo["reason"] == "bad_password"


def test_una_respuesta_de_error_lleva_su_correlation_id(client: TestClient) -> None:
    respuesta = client.get("/v1/me")
    assert respuesta.status_code == 401
    assert respuesta.headers[CORRELATION_HEADER]


def test_un_logout_deja_su_fila_de_auditoria(
    client: TestClient, migrator_db: PsycopgDatabase
) -> None:
    _, sesion = _alta(client)
    csrf = {CSRF_HEADER_NAME: str(client.cookies.get(CSRF_COOKIE_NAME))}
    # Con el access token, la traza tiene dueno.
    headers = csrf | {"Authorization": f"Bearer {sesion['access_token']}"}

    assert client.post("/v1/auth/logout", headers=headers).status_code == 204

    capacidades = [
        str(fila[0]) for fila in _filas_de_auditoria(migrator_db, sesion["user_id"])
    ]
    assert "auth.logout" in capacidades
