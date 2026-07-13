"""Tests de integracion de las VENTANILLAS de identidad (P06b, CA-07, ADR-019).

Contra PostgreSQL real. El rol de APLICACION (app_db) solo puede EJECUTAR las
ventanillas: no tiene ningun privilegio de tabla sobre app_user, user_credential ni
user_session. Por eso lo que hay que COMPROBAR en esas tablas (que la familia entera
queda revocada, que la sesion caducada muere) se lee con el rol de MIGRACIONES
(migrator_db): el de aplicacion no podria, y ese es justo el punto.

DATOS FALSOS SIEMPRE: emails inventados en ejemplo.test, contrasenas de juguete.
"""

from __future__ import annotations

import os
import time
from uuid import UUID, uuid4

import pytest

from ce_v5.core.auth import (
    AccessTokenService,
    Argon2PasswordHasher,
    AuthConfig,
    AuthService,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    RefreshTokenReuseError,
    hash_refresh_token,
)
from ce_v5.core.auth.rate_limit import RateLimitConfig
from ce_v5.core.clock import SimulatedClock
from ce_v5.entrypoints.api.audit import ApiAuthAuditor
from ce_v5.infra.bus_redis import RedisBusConfig, create_client
from ce_v5.infra.db.identity import (
    PostgresCredentialReader,
    PostgresSessionStore,
    PostgresUserRegistrar,
    register_user,
)
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.infra.db.sensitive_audit import PostgresSensitiveActionAudit
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


def _now_ms() -> int:
    return int(time.time() * 1000)


def _email() -> str:
    return f"test-{uuid4().hex}@ejemplo.test"


def _service(app_db: PsycopgDatabase) -> AuthService:
    clock = SimulatedClock(start_ms=_now_ms())
    # Limitador REAL sobre Redis (el dictamen exige Redis, no mocks), con un prefijo
    # unico por servicio para que un test no frene a otro.
    assert _REDIS_URL is not None
    limiter = RedisAuthRateLimiter(
        create_client(RedisBusConfig(url=_REDIS_URL)),
        _RATE_CONFIG,
        prefix=f"test-identity-{uuid4().hex}",
    )
    return AuthService(
        credentials=PostgresCredentialReader(app_db),
        registrar=PostgresUserRegistrar(app_db, clock),
        sessions=PostgresSessionStore(app_db),
        hasher=Argon2PasswordHasher(),
        tokens=AccessTokenService(_CONFIG, clock),
        clock=clock,
        config=_CONFIG,
        limiter=limiter,
        rate_config=_RATE_CONFIG,
        auditor=ApiAuthAuditor(app_db, PostgresSensitiveActionAudit(app_db)),
    )


def _alta(app_db: PsycopgDatabase) -> tuple[str, UUID]:
    """Alta REAL por la ventanilla, con un hash Argon2id de verdad."""
    email = _email()
    user_id = register_user(app_db, email, Argon2PasswordHasher().hash(_PASSWORD))
    return email, user_id


def _sesiones_del_usuario(
    migrator_db: PsycopgDatabase, user_id: UUID
) -> list[tuple[object, ...]]:
    """Las sesiones del usuario leidas con el rol de MIGRACIONES (el app no puede)."""
    with migrator_db.transaction() as session:
        return session.fetchall(
            "SELECT session_id, family_id, revoked_at FROM user_session "
            "WHERE user_id = %s",
            (str(user_id),),
        )


def test_alta_y_login_end_to_end(app_db: PsycopgDatabase) -> None:
    email, user_id = _alta(app_db)

    issued = _service(app_db).login(email, _PASSWORD, _IP)

    assert issued.user_id == user_id
    principal = AccessTokenService(_CONFIG, SimulatedClock(_now_ms())).verify(
        issued.access_token
    )
    assert principal.user_id == user_id
    assert issued.refresh_token


def test_login_con_contrasena_incorrecta(app_db: PsycopgDatabase) -> None:
    email, _ = _alta(app_db)
    with pytest.raises(InvalidCredentialsError):
        _service(app_db).login(email, "contrasena-que-no-es", _IP)


def test_login_con_email_inexistente(app_db: PsycopgDatabase) -> None:
    with pytest.raises(InvalidCredentialsError):
        _service(app_db).login(_email(), _PASSWORD, _IP)


def test_rotacion_y_reuso_revoca_la_familia_entera(
    app_db: PsycopgDatabase, migrator_db: PsycopgDatabase
) -> None:
    email, user_id = _alta(app_db)
    service = _service(app_db)
    issued = service.login(email, _PASSWORD, _IP)

    rotado = service.refresh(issued.refresh_token, _IP)
    assert rotado.refresh_token != issued.refresh_token
    assert rotado.user_id == user_id

    # El token VIEJO ya esta gastado: usarlo otra vez significa robo.
    with pytest.raises(RefreshTokenReuseError):
        service.refresh(issued.refresh_token, _IP)

    sesiones = _sesiones_del_usuario(migrator_db, user_id)
    assert len(sesiones) == 2  # la original y la rotada: misma familia.
    assert len({str(row[1]) for row in sesiones}) == 1
    # TODAS caen, tambien la recien rotada que el ladron podria estar usando.
    assert all(row[2] is not None for row in sesiones)


def test_refresh_con_token_inventado(app_db: PsycopgDatabase) -> None:
    with pytest.raises(InvalidRefreshTokenError):
        _service(app_db).refresh(f"token-que-nadie-emitio-{uuid4().hex}", _IP)


def test_sesion_caducada_no_rota_y_queda_revocada(
    app_db: PsycopgDatabase, migrator_db: PsycopgDatabase
) -> None:
    _, user_id = _alta(app_db)
    store = PostgresSessionStore(app_db)
    # Token UNICO por ejecucion: refresh_token_hash es UNIQUE, y un literal fijo
    # chocaria contra la fila que dejo la ejecucion anterior.
    refresh_raw = f"refresh-caducado-{uuid4().hex}"

    # Sesion nacida caducada: expira una hora ANTES de ahora.
    store.create_session(
        user_id, hash_refresh_token(refresh_raw), _now_ms() - 3_600_000
    )

    with pytest.raises(InvalidRefreshTokenError):
        _service(app_db).refresh(refresh_raw, _IP)

    sesiones = _sesiones_del_usuario(migrator_db, user_id)
    assert len(sesiones) == 1
    assert sesiones[0][2] is not None  # la ventanilla la revoco al verla caducada.


def test_logout_revoca_y_el_token_ya_no_rota(
    app_db: PsycopgDatabase, migrator_db: PsycopgDatabase
) -> None:
    email, user_id = _alta(app_db)
    service = _service(app_db)
    issued = service.login(email, _PASSWORD, _IP)

    revocadas = service.logout(issued.refresh_token)
    assert revocadas == 1

    # Tras el logout el token esta REVOCADO, y para la ventanilla un token revocado
    # que vuelve a aparecer es indistinguible de uno robado: responde reuse_detected.
    # Lo que importa es que NO rota; el desenlace exacto es el mas conservador.
    with pytest.raises(RefreshTokenReuseError):
        service.refresh(issued.refresh_token, _IP)

    sesiones = _sesiones_del_usuario(migrator_db, user_id)
    assert all(row[2] is not None for row in sesiones)
