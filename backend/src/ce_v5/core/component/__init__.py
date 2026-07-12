"""Raiz Componente: rol por contratos, lifecycle y supervisor (ADR-001/010)."""

from ce_v5.core.component.definition import ComponentDefinition
from ce_v5.core.component.gate import (
    LifecycleGate,
    LifecycleGateRequest,
    LifecycleVerdict,
)
from ce_v5.core.component.lifecycle import (
    LEGAL_TRANSITIONS,
    ComponentLifecycle,
    can_transition,
)
from ce_v5.core.component.supervisor import (
    ComponentInstance,
    DuplicateInstanceError,
    IllegalTransitionError,
    Supervisor,
    SupervisorError,
    UnknownInstanceError,
)
from source.families.component import (
    HealthStatus,
    LifecycleScope,
    LifecycleState,
    ReadinessStatus,
)

__all__ = [
    "LEGAL_TRANSITIONS",
    "ComponentDefinition",
    "ComponentInstance",
    "ComponentLifecycle",
    "DuplicateInstanceError",
    "HealthStatus",
    "IllegalTransitionError",
    "LifecycleGate",
    "LifecycleGateRequest",
    "LifecycleScope",
    "LifecycleState",
    "LifecycleVerdict",
    "ReadinessStatus",
    "Supervisor",
    "SupervisorError",
    "UnknownInstanceError",
    "can_transition",
]
