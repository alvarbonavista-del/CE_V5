"""Unit tests de las aristas de POLITICA del supervisor (P06-B8b, D9/CA-02).

Cubren: gate antes de INITIALIZE (ALLOW/DENY -> QUARANTINED), fail-fast critico
vs backoff no critico, reintentos/liberaciones observables, y la frontera CA-02
(kill switch -> component.quarantined con causation_id, jamas como component.*).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ce_v5.core.bus import EventBus
from ce_v5.core.component import (
    ComponentDefinition,
    HealthStatus,
    LifecycleGateRequest,
    LifecycleScope,
    LifecycleState,
    LifecycleVerdict,
    Supervisor,
    SupervisorError,
)
from ce_v5.core.manifest import validate_manifest
from source.envelope import Envelope
from source.families.component import ComponentLifecyclePayload

_SOURCE = "core.component.supervisor"


class _Clock:
    """Reloj con instante mutable, para probar el backoff."""

    def __init__(self, now: int = 1_000) -> None:
        self.now = now

    def now_ms(self) -> int:
        return self.now


class _DummyComponent:
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


class _FakeGate:
    """Gate doble: devuelve un veredicto (mutable) y apunta las peticiones."""

    def __init__(self, verdict: LifecycleVerdict) -> None:
        self.verdict = verdict
        self.requests: list[LifecycleGateRequest] = []

    def check_initialize(self, request: LifecycleGateRequest) -> LifecycleVerdict:
        self.requests.append(request)
        return self.verdict


def _definition(
    component_id: str = "dummy",
    *,
    critical: bool = False,
    sensitive: tuple[str, ...] = (),
) -> ComponentDefinition:
    manifest = validate_manifest(
        {
            "id": component_id,
            "version": "1.0.0",
            "manifest_schema_version": 2,
            "type": "worker",
            "entrypoint": "ce_v5.components.dummy:build",
            "critical": critical,
            "policy_requirements": {"sensitive_capabilities": list(sensitive)},
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


# --- PASO 3: gate antes de INITIALIZE ----------------------------------------


def test_gate_deny_manda_a_quarantined_sin_ejecutar_hook(
    in_memory_bus: EventBus,
) -> None:
    gate = _FakeGate(LifecycleVerdict.deny("denied_by_plan"))
    sup = Supervisor(in_memory_bus, _Clock(), source=_SOURCE, gate=gate)
    dummy = _DummyComponent()
    inst = sup.register(_definition(), dummy, instance_id="i")
    sup.initialize("i")
    assert inst.state is LifecycleState.QUARANTINED
    assert inst.health is HealthStatus.UNHEALTHY
    assert dummy.calls == []  # el enganche initialize NO se ejecuta
    last = _envelopes(in_memory_bus)[-1]
    assert last.payload.new_state is LifecycleState.QUARANTINED
    assert last.payload.previous_state is LifecycleState.REGISTERED
    assert last.payload.error_code == "denied_by_plan"
    assert len(gate.requests) == 1


def test_gate_allow_inicializa_normal(in_memory_bus: EventBus) -> None:
    gate = _FakeGate(LifecycleVerdict.allow())
    sup = Supervisor(in_memory_bus, _Clock(), source=_SOURCE, gate=gate)
    dummy = _DummyComponent()
    inst = sup.register(_definition(), dummy, instance_id="i")
    sup.initialize("i")
    assert inst.state is LifecycleState.INITIALIZED
    assert dummy.calls == ["initialize"]


def test_gate_pasa_capacidades_y_criticidad_del_manifest(
    in_memory_bus: EventBus,
) -> None:
    gate = _FakeGate(LifecycleVerdict.allow())
    sup = Supervisor(in_memory_bus, _Clock(), source=_SOURCE, gate=gate)
    definition = _definition(critical=True, sensitive=("execute_order",))
    sup.register(definition, _DummyComponent(), instance_id="i")
    sup.initialize("i")
    request = gate.requests[0]
    assert request.critical is True
    assert request.required_capabilities == ("execute_order",)
    assert request.scope is LifecycleScope.GLOBAL


# --- PASO 4: fail-fast critico vs backoff no critico -------------------------


def test_initialize_critico_falla_va_a_failed(in_memory_bus: EventBus) -> None:
    sup = Supervisor(in_memory_bus, _Clock(), source=_SOURCE)
    definition = _definition(critical=True)
    inst = sup.register(
        definition, _DummyComponent(fail_on="initialize"), instance_id="i"
    )
    sup.initialize("i")
    assert inst.state is LifecycleState.FAILED  # fail-fast: sin cuarentena
    assert inst.next_retry_at_ms is None
    assert _event_types(in_memory_bus)[-1] == "component.failed"


def test_initialize_no_critico_falla_va_a_quarantined_con_backoff(
    in_memory_bus: EventBus,
) -> None:
    clock = _Clock(now=1_000)
    sup = Supervisor(in_memory_bus, clock, source=_SOURCE)
    inst = sup.register(
        _definition(), _DummyComponent(fail_on="initialize"), instance_id="i"
    )
    sup.initialize("i")
    assert inst.state is LifecycleState.QUARANTINED
    assert inst.init_attempts == 1
    assert inst.next_retry_at_ms == 1_000 + 1_000  # backoff(1) = 1s
    assert _event_types(in_memory_bus)[-1] == "component.quarantined"


def test_retry_antes_del_backoff_rechaza(in_memory_bus: EventBus) -> None:
    clock = _Clock(now=1_000)
    sup = Supervisor(in_memory_bus, clock, source=_SOURCE)
    sup.register(_definition(), _DummyComponent(fail_on="initialize"), instance_id="i")
    sup.initialize("i")
    # next_retry_at_ms == 2000; el reloj sigue en 1000 -> aun no toca.
    with pytest.raises(SupervisorError):
        sup.retry_initialize("i")


def test_backoff_crece_y_agota_a_failed(in_memory_bus: EventBus) -> None:
    clock = _Clock(now=1_000)
    sup = Supervisor(in_memory_bus, clock, source=_SOURCE)
    inst = sup.register(
        _definition(), _DummyComponent(fail_on="initialize"), instance_id="i"
    )
    sup.initialize("i")  # intento 1 -> QUARANTINED, next_retry = 2000
    assert inst.next_retry_at_ms == 2_000
    clock.now = 2_000
    sup.retry_initialize("i")  # intento 2 -> QUARANTINED, next_retry = 2000 + 2000
    assert inst.state is LifecycleState.QUARANTINED
    assert inst.init_attempts == 2
    assert inst.next_retry_at_ms == 2_000 + 2_000  # backoff(2) = 2s
    clock.now = 4_000
    sup.retry_initialize("i")  # intento 3 -> agotado -> FAILED
    assert sup.instance("i").state is LifecycleState.FAILED
    assert inst.init_attempts == 3


def test_retry_desde_failed_resetea_y_reintenta(in_memory_bus: EventBus) -> None:
    clock = _Clock(now=1_000)
    sup = Supervisor(in_memory_bus, clock, source=_SOURCE)
    dummy = _DummyComponent(fail_on="initialize")
    inst = sup.register(_definition(), dummy, instance_id="i")
    sup.initialize("i")
    clock.now = 2_000
    sup.retry_initialize("i")
    clock.now = 4_000
    sup.retry_initialize("i")
    assert inst.state is LifecycleState.FAILED
    # El componente ya no falla: un retry de operador desde FAILED arranca.
    dummy._fail_on = None
    sup.retry_initialize("i")
    assert sup.instance("i").state is LifecycleState.INITIALIZED
    assert inst.init_attempts == 0  # reseteado
    assert _event_types(in_memory_bus)[-1] == "component.initialized"


# --- PASO 5: kill switch -> QUARANTINED con causation_id (frontera CA-02) -----


def _run_to_running(sup: Supervisor, iid: str) -> None:
    sup.initialize(iid)
    sup.start(iid)


def test_quarantine_desde_running_lleva_causation_id(
    in_memory_bus: EventBus,
) -> None:
    sup = Supervisor(in_memory_bus, _Clock(), source=_SOURCE)
    inst = sup.register(_definition(), _DummyComponent(), instance_id="i")
    _run_to_running(sup, "i")
    assert inst.state is LifecycleState.RUNNING
    sup.quarantine(
        "i",
        reason_code="denied_by_kill_switch",
        causation_id="evt-policy-123",
        switch_id="ks-1",
    )
    assert sup.instance("i").state is LifecycleState.QUARANTINED
    assert inst.quarantine_switch_id == "ks-1"
    envelopes = _envelopes(in_memory_bus)
    quarantined = envelopes[-1]
    assert quarantined.event_type == "component.quarantined"
    assert quarantined.payload.previous_state is LifecycleState.RUNNING
    assert quarantined.payload.error_code == "denied_by_kill_switch"
    # CA-02: la consecuencia apunta a la causa por causation_id.
    assert quarantined.causation_id == "evt-policy-123"
    # El kill switch JAMAS se emite como component.*: todo es familia component.
    assert all(e.event_type.startswith("component.") for e in envelopes)


def test_release_from_quarantine_reintenta_con_causation(
    in_memory_bus: EventBus,
) -> None:
    sup = Supervisor(in_memory_bus, _Clock(), source=_SOURCE)
    sup.register(_definition(), _DummyComponent(), instance_id="i")
    _run_to_running(sup, "i")
    sup.quarantine(
        "i", reason_code="denied_by_kill_switch", causation_id="evt-on", switch_id="ks"
    )
    sup.release_from_quarantine("i", causation_id="evt-off")
    assert sup.instance("i").state is LifecycleState.INITIALIZED
    # El INITIALIZING de la liberacion (el ultimo, desde QUARANTINED) lleva la
    # causa (la desactivacion); el INITIALIZING original no.
    initializing = [
        e for e in _envelopes(in_memory_bus) if e.event_type == "component.initializing"
    ][-1]
    assert initializing.payload.previous_state is LifecycleState.QUARANTINED
    assert initializing.causation_id == "evt-off"


def test_release_con_gate_que_sigue_denegando_no_libera(
    in_memory_bus: EventBus,
) -> None:
    gate = _FakeGate(LifecycleVerdict.allow())
    sup = Supervisor(in_memory_bus, _Clock(), source=_SOURCE, gate=gate)
    inst = sup.register(_definition(), _DummyComponent(), instance_id="i")
    _run_to_running(sup, "i")
    sup.quarantine(
        "i", reason_code="denied_by_kill_switch", causation_id="evt-on", switch_id="ks"
    )
    # La politica vuelve a denegar cuando se intenta liberar: sigue en cuarentena.
    gate.verdict = LifecycleVerdict.deny("denied_by_kill_switch")
    before = len(_envelopes(in_memory_bus))
    sup.release_from_quarantine("i", causation_id="evt-off")
    assert inst.state is LifecycleState.QUARANTINED
    assert len(_envelopes(in_memory_bus)) == before  # no-op: sin re-emitir
