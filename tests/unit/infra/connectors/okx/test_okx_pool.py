"""Pruebas del planificador de conexiones de OKX (T-03). Hermeticas, sin IO."""

from __future__ import annotations

import pytest

from ce_v5.infra.connectors.okx.pool import (
    ConnectionPlanner,
    ExchangeLimitExceeded,
    OkxLimits,
)


def test_reparte_dentro_de_una_conexion() -> None:
    limits = OkxLimits(max_subscriptions_per_connection=3, max_connections=2)
    plan = ConnectionPlanner(limits).assign({"a", "b", "c"})
    assert sorted(s for streams in plan.values() for s in streams) == ["a", "b", "c"]
    assert len(plan) == 1


def test_desborda_a_la_segunda_conexion() -> None:
    limits = OkxLimits(max_subscriptions_per_connection=2, max_connections=2)
    plan = ConnectionPlanner(limits).assign({"a", "b", "c"})
    assert len(plan) == 2
    assert sum(len(v) for v in plan.values()) == 3


def test_estabilidad_una_nueva_no_recoloca_las_viejas() -> None:
    limits = OkxLimits(max_subscriptions_per_connection=2, max_connections=5)
    planner = ConnectionPlanner(limits)
    primera = planner.assign({"a", "b"})
    donde = next(i for i, s in primera.items() if "a" in s)
    segunda = planner.assign({"a", "b", "c"})
    assert "a" in segunda[donde]
    assert "b" in segunda[donde]


def test_pasarse_de_capacidad_no_abre_nada() -> None:
    limits = OkxLimits(max_subscriptions_per_connection=1, max_connections=1)
    with pytest.raises(ExchangeLimitExceeded):
        ConnectionPlanner(limits).assign({"a", "b"})
