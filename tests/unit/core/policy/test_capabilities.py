"""Unit tests del vocabulario de capacidades del gate (ADR-012)."""

from __future__ import annotations

from ce_v5.core.policy import (
    SENSITIVE_CAPABILITIES,
    SensitiveCapability,
    is_sensitive,
)


def test_las_cinco_sensibles_son_sensibles() -> None:
    for capability in SensitiveCapability:
        assert is_sensitive(capability.value) is True


def test_lista_cerrada_tiene_las_cinco() -> None:
    assert SENSITIVE_CAPABILITIES == {
        "connect_broker",
        "execute_order",
        "activate_autotrade",
        "manual_order",
        "manage_api_key",
    }


def test_capability_de_catalogo_no_es_sensible() -> None:
    assert is_sensitive("view_dashboard") is False
