"""Connection configuration for the Redis Streams EventBus adapter."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

import redis

REDIS_URL_ENV_VAR = "CE_V5_REDIS_URL"


class RedisBusConfigError(RuntimeError):
    """Error de configuracion del bus Redis."""


@dataclass(frozen=True, slots=True)
class RedisBusConfig:
    """Settings for the Redis Streams adapter.

    ``url`` is a ``redis://`` DSN. ``namespace`` prefixes every stream so
    several logical buses (or test runs) can share one Redis instance.
    ``partitions`` is the basic per-``stream_key`` partition count (ADR-013:
    basic partitioning only in v5.0). ``dlq_owner`` and ``dlq_procedure`` are
    the operational owner and reprocess reference stamped on DLQ entries.
    """

    url: str
    namespace: str = "ce_v5"
    partitions: int = 1
    dlq_owner: str = "ops"
    dlq_procedure: str = "infra/bus_redis/README.md#dlq-reprocess"

    def __post_init__(self) -> None:
        if self.partitions < 1:
            raise ValueError("partitions must be >= 1")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> RedisBusConfig:
        """Construye la config leyendo la URL de Redis del entorno."""
        env: Mapping[str, str] = os.environ if environ is None else environ
        url = env.get(REDIS_URL_ENV_VAR, "").strip()
        if not url:
            raise RedisBusConfigError(
                f"Falta la variable de entorno {REDIS_URL_ENV_VAR} "
                "con la URL de conexion a Redis."
            )
        return cls(url=url)


def create_client(config: RedisBusConfig) -> redis.Redis:
    """Build a Redis client (bytes responses) from ``config``."""
    return redis.Redis.from_url(config.url, decode_responses=False)
