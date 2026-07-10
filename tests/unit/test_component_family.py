"""Unit tests de la familia component.* (ADR-004, ADR-010)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from source.envelope import Envelope, Scope
from source.families import validate_event_type
from source.families.component import (
    ComponentEventType,
    ComponentLifecyclePayload,
    HealthStatus,
    LifecycleScope,
    LifecycleState,
    ReadinessStatus,
    event_type_for_state,
)


def test_event_type_for_state_cubre_todos_los_estados() -> None:
    for state in LifecycleState:
        assert event_type_for_state(state).value == f"component.{state.value}"


def test_tipos_de_evento_uno_por_estado() -> None:
    esperados = {f"component.{s.value}" for s in LifecycleState}
    assert {e.value for e in ComponentEventType} == esperados


def test_tipos_de_evento_son_event_type_validos() -> None:
    for event_type in ComponentEventType:
        assert validate_event_type(event_type.value) == event_type.value


def test_payload_global_valido() -> None:
    payload = ComponentLifecyclePayload(
        component_id="dummy",
        component_version="1.0.0",
        component_instance_id="inst-1",
        lifecycle_scope=LifecycleScope.GLOBAL,
        new_state=LifecycleState.RUNNING,
        health_status=HealthStatus.HEALTHY,
        readiness_status=ReadinessStatus.READY,
        previous_state=LifecycleState.STARTING,
    )
    assert payload.new_state is LifecycleState.RUNNING
    assert payload.previous_state is LifecycleState.STARTING


def test_payload_global_rechaza_tenant() -> None:
    with pytest.raises(ValidationError):
        ComponentLifecyclePayload(
            component_id="dummy",
            component_version="1.0.0",
            component_instance_id="inst-1",
            lifecycle_scope=LifecycleScope.GLOBAL,
            new_state=LifecycleState.RUNNING,
            health_status=HealthStatus.HEALTHY,
            readiness_status=ReadinessStatus.READY,
            tenant_id="t-1",
        )


def test_payload_tenant_exige_tenant_id() -> None:
    with pytest.raises(ValidationError):
        ComponentLifecyclePayload(
            component_id="dummy",
            component_version="1.0.0",
            component_instance_id="inst-1",
            lifecycle_scope=LifecycleScope.TENANT,
            new_state=LifecycleState.RUNNING,
            health_status=HealthStatus.HEALTHY,
            readiness_status=ReadinessStatus.READY,
        )


def test_payload_user_exige_tenant_y_user() -> None:
    payload = ComponentLifecyclePayload(
        component_id="dummy",
        component_version="1.0.0",
        component_instance_id="inst-1",
        lifecycle_scope=LifecycleScope.USER,
        new_state=LifecycleState.RUNNING,
        health_status=HealthStatus.HEALTHY,
        readiness_status=ReadinessStatus.READY,
        tenant_id="t-1",
        user_id="u-1",
    )
    assert payload.user_id == "u-1"
    with pytest.raises(ValidationError):
        ComponentLifecyclePayload(
            component_id="dummy",
            component_version="1.0.0",
            component_instance_id="inst-1",
            lifecycle_scope=LifecycleScope.USER,
            new_state=LifecycleState.RUNNING,
            health_status=HealthStatus.HEALTHY,
            readiness_status=ReadinessStatus.READY,
            tenant_id="t-1",
        )


def test_payload_viaja_en_el_envelope() -> None:
    payload = ComponentLifecyclePayload(
        component_id="dummy",
        component_version="1.0.0",
        component_instance_id="inst-1",
        lifecycle_scope=LifecycleScope.GLOBAL,
        new_state=LifecycleState.REGISTERED,
        health_status=HealthStatus.HEALTHY,
        readiness_status=ReadinessStatus.NOT_READY,
    )
    envelope = Envelope[ComponentLifecyclePayload](
        event_type=event_type_for_state(LifecycleState.REGISTERED).value,
        event_schema_version=1,
        source="core.component.supervisor",
        idempotency_key="dummy:inst-1:registered",
        stream_key="component:dummy:inst-1",
        scope=Scope.SYSTEM,
        correlation_id="corr-1",
        payload=payload,
    )
    assert envelope.event_type == "component.registered"
    assert envelope.payload.new_state is LifecycleState.REGISTERED
