"""Unit tests del lifecycle de Componente (ADR-001, ADR-010)."""

from __future__ import annotations

from ce_v5.core.component import (
    LEGAL_TRANSITIONS,
    ComponentLifecycle,
    HealthStatus,
    LifecycleState,
    ReadinessStatus,
    can_transition,
)


def test_todos_los_estados_tienen_transiciones_declaradas() -> None:
    assert set(LEGAL_TRANSITIONS) == set(LifecycleState)


def test_los_destinos_son_estados_validos() -> None:
    for targets in LEGAL_TRANSITIONS.values():
        for target in targets:
            assert isinstance(target, LifecycleState)


def test_unloaded_es_el_unico_terminal() -> None:
    terminales = {s for s, t in LEGAL_TRANSITIONS.items() if not t}
    assert terminales == {LifecycleState.UNLOADED}


def test_camino_feliz_es_legal() -> None:
    feliz = [
        (LifecycleState.REGISTERED, LifecycleState.INITIALIZING),
        (LifecycleState.INITIALIZING, LifecycleState.INITIALIZED),
        (LifecycleState.INITIALIZED, LifecycleState.STARTING),
        (LifecycleState.STARTING, LifecycleState.RUNNING),
        (LifecycleState.RUNNING, LifecycleState.PAUSED),
        (LifecycleState.PAUSED, LifecycleState.RUNNING),
        (LifecycleState.RUNNING, LifecycleState.STOPPING),
        (LifecycleState.STOPPING, LifecycleState.STOPPED),
        (LifecycleState.STOPPED, LifecycleState.UNLOADED),
    ]
    for current, target in feliz:
        assert can_transition(current, target)


def test_fallo_alcanzable_desde_transiciones_activas() -> None:
    for origen in (
        LifecycleState.INITIALIZING,
        LifecycleState.INITIALIZED,
        LifecycleState.STARTING,
        LifecycleState.RUNNING,
        LifecycleState.PAUSED,
        LifecycleState.STOPPING,
        LifecycleState.STOPPED,
    ):
        assert can_transition(origen, LifecycleState.FAILED)


def test_aristas_de_politica_no_estan_en_p04() -> None:
    assert not can_transition(LifecycleState.FAILED, LifecycleState.INITIALIZING)
    assert not can_transition(LifecycleState.QUARANTINED, LifecycleState.INITIALIZING)


def test_transicion_ilegal_es_falsa() -> None:
    assert not can_transition(LifecycleState.REGISTERED, LifecycleState.RUNNING)
    assert not can_transition(LifecycleState.RUNNING, LifecycleState.REGISTERED)


def test_ejes_de_salud_separados_del_lifecycle() -> None:
    assert HealthStatus.DEGRADED.value == "degraded"
    assert ReadinessStatus.READY.value == "ready"
    assert "degraded" not in {s.value for s in LifecycleState}


class _ComponenteMinimo:
    """Doble de test: implementa los enganches del contrato (no hereda)."""

    def initialize(self) -> None:
        return None

    def start(self) -> None:
        return None

    def pause(self) -> None:
        return None

    def resume(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def unload(self) -> None:
        return None


class _ComponenteIncompleto:
    """Le falta 'unload': no debe cumplir el contrato."""

    def initialize(self) -> None:
        return None

    def start(self) -> None:
        return None

    def pause(self) -> None:
        return None

    def resume(self) -> None:
        return None

    def stop(self) -> None:
        return None


def test_componente_minimo_cumple_el_contrato() -> None:
    assert isinstance(_ComponenteMinimo(), ComponentLifecycle)


def test_componente_incompleto_no_cumple_el_contrato() -> None:
    assert not isinstance(_ComponenteIncompleto(), ComponentLifecycle)
