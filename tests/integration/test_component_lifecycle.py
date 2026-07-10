"""Integracion / validacion en caliente de P04 (ADR-009/010).

Demuestra CE-14 "copiar carpeta + reiniciar": el discovery escanea la carpeta
real components/, descubre el componente sample (copiado en P04), valida su
manifest ANTES de cargar codigo, lo registra, y el supervisor lo lleva por el
lifecycle emitiendo eventos component.* que se LEEN del bus externo (Redis).
Requiere Redis local; se salta si no esta CE_V5_REDIS_URL. Sin datos reales.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import redis

from ce_v5.core.clock import SystemClock
from ce_v5.core.component import ComponentLifecycle, LifecycleState, Supervisor
from ce_v5.core.discovery import discover, import_entrypoint
from ce_v5.infra.bus_redis import RedisBusConfig, RedisEventBus, create_client
from source.envelope import Envelope
from source.families.component import ComponentLifecyclePayload

_URL = os.environ.get("CE_V5_REDIS_URL")
pytestmark = pytest.mark.skipif(
    _URL is None, reason="requiere CE_V5_REDIS_URL (Redis local)"
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPONENTS = _REPO_ROOT / "backend" / "src" / "ce_v5" / "components"

_EXPECTED = [
    "component.registered",
    "component.initializing",
    "component.initialized",
    "component.starting",
    "component.running",
    "component.paused",
    "component.running",
    "component.stopping",
    "component.stopped",
    "component.unloaded",
]


@pytest.fixture
def config() -> RedisBusConfig:
    assert _URL is not None
    return RedisBusConfig(url=_URL, namespace="test-" + uuid.uuid4().hex)


@pytest.fixture
def client(config: RedisBusConfig) -> Iterator[redis.Redis]:
    conn = create_client(config)
    try:
        yield conn
    finally:
        for key in conn.scan_iter(match=f"{config.namespace}:*"):
            conn.delete(key)
        conn.close()


@pytest.fixture
def bus(client: redis.Redis, config: RedisBusConfig) -> RedisEventBus:
    return RedisEventBus(client, config)


def test_alta_por_carpeta_y_lifecycle_por_el_bus(bus: RedisEventBus) -> None:
    result = discover(_COMPONENTS, import_entrypoint)
    ids = {d.component_id for d in result.registered}
    assert "sample" in ids
    assert result.rejected == ()
    definition = next(d for d in result.registered if d.component_id == "sample")

    entrypoint = definition.entrypoint
    assert entrypoint is not None
    target = import_entrypoint(entrypoint)
    assert callable(target)
    component = target()
    assert isinstance(component, ComponentLifecycle)

    supervisor = Supervisor(bus, SystemClock(), source="tests.hot.p04")
    instance = supervisor.register(definition, component, instance_id="sample-1")
    supervisor.initialize("sample-1")
    supervisor.start("sample-1")
    supervisor.pause("sample-1")
    supervisor.resume("sample-1")
    supervisor.stop("sample-1")
    supervisor.unload("sample-1")
    assert instance.state is LifecycleState.UNLOADED

    received = bus.replay("component", start=None, max_messages=100)
    types = [r.message.event_type for r in received]
    assert types == _EXPECTED

    last = Envelope[ComponentLifecyclePayload].model_validate_json(
        received[-1].message.envelope
    )
    assert last.payload.component_id == "sample"
    assert last.payload.new_state is LifecycleState.UNLOADED

    print("\n[P04 caliente] descubiertos:", sorted(ids))
    print("[P04 caliente] rechazados:", result.rejected)
    print("[P04 caliente] transiciones emitidas y leidas del bus:")
    for r in received:
        print("   ", r.message.event_type)
