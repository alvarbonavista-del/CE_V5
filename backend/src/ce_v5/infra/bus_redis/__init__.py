"""Redis Streams adapter for the EventBus port (ADR-013).

Public surface of ``ce_v5.infra.bus_redis``. Wired at the composition root.
"""

from __future__ import annotations

from ce_v5.infra.bus_redis.adapter import RedisEventBus
from ce_v5.infra.bus_redis.config import RedisBusConfig, create_client

__all__ = ["RedisBusConfig", "RedisEventBus", "create_client"]
