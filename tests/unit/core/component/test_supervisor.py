"""Unit tests del Supervisor de lifecycle (ADR-010)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ce_v5.core.bus import EventBus
from ce_v5.core.component import (
    ComponentDefinition,
    DuplicateInstanceError,
    HealthStatus,
    IllegalTransitionError,
    LifecycleScope,
    LifecycleState,
    ReadinessStatus,
    Supervisor,
    UnknownInstanceError,
)
from ce_v5.core.manifest import validate_manifest
from source.envelope import Envelope, Scope
from source.families.component import ComponentLifecyclePayload

_SOURCE = "core.component.supervisor"


class _FixedClock:
    def now_ms(self) -> int:
        return 1_000


class _DummyComponent:
    """Componente de test que apunta sus llamadas y puede fallar en una."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.calls: list[str] = []
        self._fail_on = fail_on

    def _run(self, name: str) -> None:
        self.calls.append(name)
        if name == self._fail_on:
            raise RuntimeError(f"fallo en {name}")

    def initialize(self) -> None:
        self._run("initialize")

    def start(self) -> None:
        self._run("start")

    def pause(self) -> None:
        self._run("pause")

    def resume(self) -> None:
        self._run("resume")

    def stop(self) -> None:
        self._run("stop")

    def unload(self) -> None:
        self._run("unload")


def _definition(component_id: str = "dummy") -> ComponentDefinition:
    manifest = validate_manifest(
        {
            "id": component_id,
            "version": "1.0.0",
            "manifest_schema_version": 1,
            "type": "worker",
            "entrypoint": "ce_v5.components.dummy:build",
        }
    )
    return ComponentDefinition(
        manifest=manifest, path=Path("components") / component_id
    )


def _event_types(bus: EventBus) -> list[str]:
    received = bus.replay("component", start=None, max_messages=1000)
    return [r.message.event_type for r in received]


def _envelopes(bus: EventBus) -> list[Envelope[ComponentLifecyclePayload]]:
    received = bus.replay("component", start=None, max_messages=1000)
    return [
        Envelope[ComponentLifecyclePayload].model_validate_json(r.message.envelope)
        for r in received
    ]


def test_viaje_completo_emite_eventos(in_memory_bus: EventBus) -> None:
    sup = Supervisor(in_memory_bus, _FixedClock(), source=_SOURCE)
    dummy = _DummyComponent()
    instance = sup.register(_definition(), dummy, instance_id="inst-1")
    sup.initialize("inst-1")
    sup.start("inst-1")
    sup.pause("inst-1")
    sup.resume("inst-1")
    sup.stop("inst-1")
    sup.unload("inst-1")
    assert instance.state is LifecycleState.UNLOADED
    assert dummy.calls == [
        "initialize",
        "start",
        "pause",
        "resume",
        "stop",
        "unload",
    ]
    assert _event_types(in_memory_bus) == [
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


def test_readiness_y_salud(in_memory_bus: EventBus) -> None:
    sup = Supervisor(in_memory_bus, _FixedClock(), source=_SOURCE)
    inst = sup.register(_definition(), _DummyComponent(), instance_id="i")
    sup.initialize("i")
    sup.start("i")
    assert inst.state is LifecycleState.RUNNING
    assert inst.health is HealthStatus.HEALTHY
    ready = inst.readiness
    assert ready is ReadinessStatus.READY
    sup.pause("i")
    not_ready = inst.readiness
    assert not_ready is ReadinessStatus.NOT_READY


def test_fallo_en_enganche_lleva_a_failed(in_memory_bus: EventBus) -> None:
    sup = Supervisor(in_memory_bus, _FixedClock(), source=_SOURCE)
    inst = sup.register(
        _definition(), _DummyComponent(fail_on="start"), instance_id="i"
    )
    sup.initialize("i")
    sup.start("i")
    assert inst.state is LifecycleState.FAILED
    assert inst.health is HealthStatus.UNHEALTHY
    assert _event_types(in_memory_bus)[-1] == "component.failed"
    failed = _envelopes(in_memory_bus)[-1]
    assert failed.payload.new_state is LifecycleState.FAILED
    assert failed.payload.previous_state is LifecycleState.STARTING
    assert failed.payload.error_code == "RuntimeError"


def test_transicion_ilegal_lanza(in_memory_bus: EventBus) -> None:
    sup = Supervisor(in_memory_bus, _FixedClock(), source=_SOURCE)
    sup.register(_definition(), _DummyComponent(), instance_id="i")
    with pytest.raises(IllegalTransitionError):
        sup.start("i")


def test_pausar_sin_estar_running_lanza(in_memory_bus: EventBus) -> None:
    sup = Supervisor(in_memory_bus, _FixedClock(), source=_SOURCE)
    sup.register(_definition(), _DummyComponent(), instance_id="i")
    with pytest.raises(IllegalTransitionError):
        sup.pause("i")


def test_instancia_desconocida_lanza(in_memory_bus: EventBus) -> None:
    sup = Supervisor(in_memory_bus, _FixedClock(), source=_SOURCE)
    with pytest.raises(UnknownInstanceError):
        sup.initialize("no_existe")


def test_instancia_duplicada_lanza(in_memory_bus: EventBus) -> None:
    sup = Supervisor(in_memory_bus, _FixedClock(), source=_SOURCE)
    sup.register(_definition(), _DummyComponent(), instance_id="i")
    with pytest.raises(DuplicateInstanceError):
        sup.register(_definition(), _DummyComponent(), instance_id="i")


def test_scope_user_viaja_en_el_envelope(in_memory_bus: EventBus) -> None:
    sup = Supervisor(in_memory_bus, _FixedClock(), source=_SOURCE)
    sup.register(
        _definition(),
        _DummyComponent(),
        instance_id="i",
        scope=LifecycleScope.USER,
        tenant_id="t-1",
        user_id="u-1",
    )
    env = _envelopes(in_memory_bus)[0]
    assert env.scope is Scope.USER
    assert env.tenant_id == "t-1"
    assert env.user_id == "u-1"
    assert env.payload.lifecycle_scope is LifecycleScope.USER


class _BusDown(RuntimeError):
    """Fallo simulado del bus al publicar."""


def _boom(topic: str, message: object) -> object:
    raise _BusDown("bus caido")


def test_publish_fallido_propaga_y_no_avanza_estado(
    in_memory_bus: EventBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sup = Supervisor(in_memory_bus, _FixedClock(), source=_SOURCE)
    instance = sup.register(_definition(), _DummyComponent(), instance_id="i")
    monkeypatch.setattr(in_memory_bus, "publish", _boom)
    with pytest.raises(_BusDown):
        sup.initialize("i")
    assert instance.state is LifecycleState.REGISTERED


def test_publish_fallido_en_register_no_registra(
    in_memory_bus: EventBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sup = Supervisor(in_memory_bus, _FixedClock(), source=_SOURCE)
    monkeypatch.setattr(in_memory_bus, "publish", _boom)
    with pytest.raises(_BusDown):
        sup.register(_definition(), _DummyComponent(), instance_id="i")
    with pytest.raises(UnknownInstanceError):
        sup.instance("i")
