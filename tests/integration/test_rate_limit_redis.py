"""Tests del limitador contra REDIS REAL (P06b, CA-10; dictamen CSA pruebas 6 y 7).

Se saltan si no hay CE_V5_REDIS_URL. Cada test usa un prefijo unico para no pisar a los
demas ni dejar basura. NUNCA datos reales: Redis de juguete (DOC_ENTREGABLES sec.5).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
import redis

from ce_v5.core.auth.rate_limit import (
    ACTION_LOGIN,
    ACTION_REFRESH,
    Attempt,
    RateLimitConfig,
    RateLimiterUnavailableError,
    digest,
)
from ce_v5.infra.bus_redis import RedisBusConfig, create_client
from ce_v5.infra.ratelimit.redis_limiter import RedisAuthRateLimiter

_URL = os.environ.get("CE_V5_REDIS_URL")
pytestmark = pytest.mark.skipif(
    _URL is None, reason="requiere CE_V5_REDIS_URL (Redis local)"
)

_SECRETO = "secreto-de-test-de-32-caracteres-o-mas"
_EMAIL = "ana@ejemplo.test"
_IP = "203.0.113.10"  # TEST-NET-3: ficticia.


@pytest.fixture
def prefix() -> str:
    return f"test-ratelimit-{uuid.uuid4().hex}"


@pytest.fixture
def client(prefix: str) -> Iterator[redis.Redis]:
    assert _URL is not None
    conn = create_client(RedisBusConfig(url=_URL))
    try:
        yield conn
    finally:
        for key in conn.scan_iter(match=f"{prefix}:*"):
            conn.delete(key)
        conn.close()


@pytest.fixture
def config() -> RateLimitConfig:
    return RateLimitConfig(digest_secret=_SECRETO)


@pytest.fixture
def limiter(
    client: redis.Redis, config: RateLimitConfig, prefix: str
) -> RedisAuthRateLimiter:
    return RedisAuthRateLimiter(client, config, prefix=prefix)


def _attempt(
    ip: str | None = _IP, email: str | None = _EMAIL, action: str = ACTION_LOGIN
) -> Attempt:
    return Attempt(
        action=action,
        ip_digest=None if ip is None else digest(ip, _SECRETO),
        account_digest=None if email is None else digest(email, _SECRETO),
    )


def _block_key(prefix: str, attempt: Attempt, dimension: str = "ip_account") -> str:
    if dimension == "ip_account":
        sufijo = f"ip_account:{attempt.ip_digest}:{attempt.account_digest}"
    elif dimension == "account":
        sufijo = f"account:{attempt.account_digest}"
    else:
        sufijo = f"ip:{attempt.ip_digest}"
    return f"{prefix}:{attempt.action}:block:{sufijo}"


def test_por_debajo_del_umbral_se_permite(limiter: RedisAuthRateLimiter) -> None:
    attempt = _attempt()
    for _ in range(3):
        limiter.register_failure(attempt)
    decision = limiter.check(attempt)
    assert decision.allowed is True
    assert decision.retry_after_seconds == 0


def test_superado_el_umbral_se_planta_una_llave_de_bloqueo(
    limiter: RedisAuthRateLimiter,
    client: redis.Redis,
    prefix: str,
    config: RateLimitConfig,
) -> None:
    attempt = _attempt()
    for _ in range(config.by_ip_account.max_failures + 1):
        limiter.register_failure(attempt)

    ttl = client.ttl(_block_key(prefix, attempt))
    assert isinstance(ttl, int)
    assert ttl > 0


def test_el_bloqueo_crece_con_los_fallos_y_se_topa(
    limiter: RedisAuthRateLimiter,
    client: redis.Redis,
    prefix: str,
    config: RateLimitConfig,
) -> None:
    # Una cuesta cada vez mas empinada: 2, 4, 8... y nunca por encima del tope.
    attempt = _attempt()
    for _ in range(config.by_ip_account.max_failures):
        limiter.register_failure(attempt)

    esperas: list[int] = []
    for _ in range(3):
        limiter.register_failure(attempt)
        ttl = client.ttl(_block_key(prefix, attempt))
        assert isinstance(ttl, int)
        esperas.append(ttl)

    assert esperas[0] < esperas[1] < esperas[2]
    assert max(esperas) <= config.max_retry_after_seconds


def test_check_deniega_mientras_el_bloqueo_esta_vivo(
    limiter: RedisAuthRateLimiter, config: RateLimitConfig
) -> None:
    attempt = _attempt()
    for _ in range(config.by_ip_account.max_failures + 2):
        limiter.register_failure(attempt)

    decision = limiter.check(attempt)
    assert decision.allowed is False
    assert decision.dimension == "ip_account"
    assert decision.retry_after_seconds > 0


def test_superada_la_cuenta_desde_muchas_ips(
    limiter: RedisAuthRateLimiter, config: RateLimitConfig
) -> None:
    # Mil maquinas contra una victima: cada IP falla poco, la cuenta acumula. Un solo
    # contador por IP dejaria pasar este ataque entero.
    for i in range(config.by_account.max_failures + 2):
        limiter.register_failure(_attempt(ip=f"203.0.113.{i + 1}"))

    decision = limiter.check(_attempt(ip="203.0.113.200"))
    assert decision.allowed is False
    assert decision.dimension == "account"


def test_acciones_distintas_no_se_pisan(
    limiter: RedisAuthRateLimiter, config: RateLimitConfig
) -> None:
    # Los fallos de LOGIN de alguien no pueden frenarle el REFRESH: no ha hecho nada.
    for _ in range(config.by_ip_account.max_failures + 3):
        limiter.register_failure(_attempt(action=ACTION_LOGIN))

    assert limiter.check(_attempt(action=ACTION_LOGIN)).allowed is False
    assert limiter.check(_attempt(email=None, action=ACTION_REFRESH)).allowed is True


def test_el_contador_caduca_solo(
    limiter: RedisAuthRateLimiter,
    client: redis.Redis,
    prefix: str,
    config: RateLimitConfig,
) -> None:
    # El TTL es lo que garantiza el decaimiento: nadie queda frenado para siempre.
    attempt = _attempt()
    limiter.register_failure(attempt)

    key = f"{prefix}:{ACTION_LOGIN}:account:{attempt.account_digest}"
    ttl = client.ttl(key)
    assert isinstance(ttl, int)
    assert 0 < ttl <= config.by_account.window_seconds


def test_reset_limpia_cuenta_e_ip_cuenta_pero_no_la_ip(
    limiter: RedisAuthRateLimiter, client: redis.Redis, prefix: str
) -> None:
    attempt = _attempt()
    for _ in range(3):
        limiter.register_failure(attempt)

    limiter.reset(attempt)

    base = f"{prefix}:{ACTION_LOGIN}"
    assert client.get(f"{base}:account:{attempt.account_digest}") is None
    assert (
        client.get(f"{base}:ip_account:{attempt.ip_digest}:{attempt.account_digest}")
        is None
    )
    # La IP CONSERVA su historial: acertar una cuenta no le borra los fallos contra las
    # demas (seria la salida facil del atacante que ya tiene una cuenta valida).
    assert client.get(f"{base}:ip:{attempt.ip_digest}") is not None


def test_las_claves_no_contienen_el_email_en_claro(
    limiter: RedisAuthRateLimiter, client: redis.Redis, prefix: str
) -> None:
    # Prueba 7 del dictamen: quien se asome al almacen no obtiene una lista de clientes.
    limiter.register_failure(_attempt())

    claves = [key.decode("utf-8") for key in client.scan_iter(match=f"{prefix}:*")]
    assert claves
    for clave in claves:
        assert _EMAIL not in clave
        assert "ana" not in clave
        assert _IP not in clave


def test_redis_caido_es_fail_closed(config: RateLimitConfig, prefix: str) -> None:
    # Prueba 6: sin contador no hay limite, y sin limite la fuerza bruta es gratis. El
    # limitador LANZA; jamas responde "permitido".
    muerto = redis.Redis.from_url(
        "redis://127.0.0.1:1/0", socket_connect_timeout=1, decode_responses=False
    )
    limiter = RedisAuthRateLimiter(muerto, config, prefix=prefix)
    try:
        with pytest.raises(RateLimiterUnavailableError):
            limiter.check(_attempt())
    finally:
        muerto.close()
