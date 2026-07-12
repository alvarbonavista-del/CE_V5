"""Unit tests del KillSwitchQuarantineConsumer (P06-B8b, frontera CA-02)."""

from __future__ import annotations

from pathlib import Path

from ce_v5.core.bus import EventBus
from ce_v5.core.component import ComponentDefinition, LifecycleState, Supervisor
from ce_v5.core.manifest import validate_manifest
from ce_v5.core.policy import KillSwitchQuarantineConsumer
from source.envelope import Envelope
from source.families.component import ComponentLifecyclePayload
from source.families.policy import KillSwitchPayload, KillSwitchScope

_SOURCE = "core.component.supervisor"


class _Clock:
    def now_ms(self) -> int:
        return 1_000


class _Dummy:
    def initialize(self) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def unload(self) -> None: ...


def _definition(
    component_id: str = "dummy", *, sensitive: tuple[str, ...] = ()
) -> ComponentDefinition:
    manifest = validate_manifest(
        {
            "id": component_id,
            "version": "1.0.0",
            "manifest_schema_version": 2,
            "type": "worker",
            "entrypoint": "ce_v5.components.dummy:build",
            "policy_requirements": {"sensitive_capabilities": list(sensitive)},
        }
    )
    return ComponentDefinition(
        manifest=manifest, path=Path("components") / component_id
    )


def _running(sup: Supervisor, iid: str, definition: ComponentDefinition) -> None:
    sup.register(definition, _Dummy(), instance_id=iid)
    sup.initialize(iid)
    sup.start(iid)


def _envelopes(bus: EventBus) -> list[Envelope[ComponentLifecyclePayload]]:
    received = bus.replay("component", start=None, max_messages=1000)
    return [
        Envelope[ComponentLifecyclePayload].model_validate_json(r.message.envelope)
        for r in received
    ]


def _global_switch(kill_switch_id: str = "ks") -> KillSwitchPayload:
    return KillSwitchPayload(
        kill_switch_id=kill_switch_id,
        scope=KillSwitchScope.GLOBAL,
        reason_code="denied_by_kill_switch",
        policy_version="v1",
        actor="operator",
    )


def test_activacion_aisla_instancia_viva_con_causation(
    in_memory_bus: EventBus,
) -> None:
    sup = Supervisor(in_memory_bus, _Clock(), source=_SOURCE)
    _running(sup, "i", _definition())
    consumer = KillSwitchQuarantineConsumer(sup)

    consumer.on_activated(_global_switch(), event_id="evt-policy-1")

    live = sup.instance("i")
    assert live.state is LifecycleState.QUARANTINED
    assert live.quarantine_switch_id == "ks"
    quarantined = _envelopes(in_memory_bus)[-1]
    assert quarantined.event_type == "component.quarantined"
    # CA-02: la CONSECUENCIA apunta a la CAUSA por causation_id.
    assert quarantined.causation_id == "evt-policy-1"
    # El kill switch JAMAS viaja como component.*: todo es familia component.
    assert all(e.event_type.startswith("component.") for e in _envelopes(in_memory_bus))


def test_activacion_no_toca_instancias_no_vivas(in_memory_bus: EventBus) -> None:
    sup = Supervisor(in_memory_bus, _Clock(), source=_SOURCE)
    # Registrada pero sin inicializar (no viva): el switch no la aisla.
    sup.register(_definition(), _Dummy(), instance_id="dormida")
    consumer = KillSwitchQuarantineConsumer(sup)
    consumer.on_activated(_global_switch(), event_id="evt")
    assert sup.instance("dormida").state is LifecycleState.REGISTERED


def test_activacion_de_capacidad_solo_toca_a_quien_la_declara(
    in_memory_bus: EventBus,
) -> None:
    sup = Supervisor(in_memory_bus, _Clock(), source=_SOURCE)
    _running(sup, "con", _definition("con", sensitive=("execute_order",)))
    _running(sup, "sin", _definition("sin"))
    consumer = KillSwitchQuarantineConsumer(sup)
    switch = KillSwitchPayload(
        kill_switch_id="ks",
        scope=KillSwitchScope.CAPABILITY,
        reason_code="denied_by_kill_switch",
        policy_version="v1",
        actor="operator",
        target_ref="execute_order",
    )
    consumer.on_activated(switch, event_id="evt")
    assert sup.instance("con").state is LifecycleState.QUARANTINED
    assert sup.instance("sin").state is LifecycleState.RUNNING


def test_desactivacion_libera_solo_lo_que_aislo_ese_switch(
    in_memory_bus: EventBus,
) -> None:
    sup = Supervisor(in_memory_bus, _Clock(), source=_SOURCE)
    _running(sup, "i", _definition())
    consumer = KillSwitchQuarantineConsumer(sup)
    consumer.on_activated(_global_switch("ks-1"), event_id="evt-on")
    assert sup.instance("i").state is LifecycleState.QUARANTINED

    # Desactivar OTRO switch no la libera.
    consumer.on_deactivated(_global_switch("ks-2"), event_id="evt-x")
    assert sup.instance("i").state is LifecycleState.QUARANTINED

    # Desactivar el que la aislo la libera: reintenta INITIALIZE.
    consumer.on_deactivated(_global_switch("ks-1"), event_id="evt-off")
    assert sup.instance("i").state is LifecycleState.INITIALIZED
    initializing = next(
        e
        for e in _envelopes(in_memory_bus)
        if e.event_type == "component.initializing" and e.causation_id == "evt-off"
    )
    assert initializing.payload.previous_state is LifecycleState.QUARANTINED
