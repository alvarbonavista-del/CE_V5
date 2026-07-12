"""Unit tests del ComponentManifest y su validacion estatica (ADR-008)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from ce_v5.core.manifest import (
    Capability,
    CapabilityKind,
    ComponentType,
    validate_manifest,
)

_MINIMO: dict[str, object] = {
    "id": "dummy",
    "version": "1.0.0",
    "manifest_schema_version": 1,
    "type": "worker",
}


def test_manifest_minimo_valido() -> None:
    manifest = validate_manifest(_MINIMO)
    assert manifest.id == "dummy"
    assert manifest.type is ComponentType.WORKER
    assert manifest.produces == ()
    assert manifest.consumes == ()
    assert manifest.capabilities == ()
    assert manifest.requires.clock is False
    assert manifest.ui is None
    assert manifest.config_schema is None
    # 'critical' es opcional: por defecto NO critico (ADR-010 fail-fast). Un
    # manifest v1 (sin el campo) sigue validando y se trata como no critico.
    assert manifest.critical is False


def test_critical_explicito_se_respeta() -> None:
    data = dict(_MINIMO)
    data["critical"] = True
    manifest = validate_manifest(data)
    assert manifest.critical is True


def test_falta_un_obligatorio_falla() -> None:
    data = dict(_MINIMO)
    del data["type"]
    with pytest.raises(ValidationError):
        validate_manifest(data)


def test_clave_desconocida_falla() -> None:
    data = dict(_MINIMO)
    data["desconocida"] = 1
    with pytest.raises(ValidationError):
        validate_manifest(data)


def test_tipo_invalido_falla() -> None:
    data = dict(_MINIMO)
    data["type"] = "no_existe"
    with pytest.raises(ValidationError):
        validate_manifest(data)


def test_id_vacio_falla() -> None:
    data = dict(_MINIMO)
    data["id"] = ""
    with pytest.raises(ValidationError):
        validate_manifest(data)


def test_produces_event_type_invalido_falla() -> None:
    data = dict(_MINIMO)
    data["produces"] = [{"event_type": "tipo_sin_familia", "event_schema_version": 1}]
    with pytest.raises(ValidationError):
        validate_manifest(data)


def test_produces_event_type_valido() -> None:
    data = dict(_MINIMO)
    data["produces"] = [{"event_type": "component.running", "event_schema_version": 1}]
    manifest = validate_manifest(data)
    assert manifest.produces[0].event_type == "component.running"


def test_capability_custom_exige_nombre() -> None:
    with pytest.raises(ValidationError):
        Capability(kind=CapabilityKind.CUSTOM, version=1)


def test_capability_no_custom_rechaza_nombre() -> None:
    with pytest.raises(ValidationError):
        Capability(kind=CapabilityKind.DATASOURCE, version=1, name="x")


def test_capability_custom_bien_formada() -> None:
    cap = Capability(kind=CapabilityKind.CUSTOM, version=1, name="pattern_detector")
    assert cap.name == "pattern_detector"


def test_capability_datasource_con_detalle() -> None:
    data = dict(_MINIMO)
    data["capabilities"] = [
        {"kind": "datasource", "version": 1, "detail": {"shared_evaluation": True}}
    ]
    manifest = validate_manifest(data)
    assert manifest.capabilities[0].kind is CapabilityKind.DATASOURCE
    assert manifest.capabilities[0].detail == {"shared_evaluation": True}


def test_manifest_es_inmutable() -> None:
    manifest = validate_manifest(_MINIMO)
    manifest_any: Any = manifest
    with pytest.raises(ValidationError):
        manifest_any.id = "otro"


def test_manifest_completo_valido() -> None:
    data: dict[str, object] = {
        "id": "sample",
        "version": "2.1.0",
        "manifest_schema_version": 2,
        "type": "connector",
        "produces": [
            {"event_type": "execution.order_filled", "event_schema_version": 1}
        ],
        "consumes": [{"event_type": "signal.fired", "event_schema_version": 1}],
        "requires": {"clock": True, "event_bus": True, "components": ["other"]},
        "capabilities": [
            {"kind": "connector", "version": 1},
            {"kind": "custom", "version": 1, "name": "reconciler"},
        ],
        "ui": {"panel": True, "supported_surfaces": ["web"]},
        "policy_requirements": {"sensitive_capabilities": ["execute_order"]},
        "config_schema": {"type": "object"},
        "critical": True,
    }
    manifest = validate_manifest(data)
    assert manifest.type is ComponentType.CONNECTOR
    assert manifest.requires.clock is True
    assert len(manifest.capabilities) == 2
    assert manifest.ui is not None
    assert manifest.ui.panel is True
    assert manifest.critical is True
    assert manifest.policy_requirements.sensitive_capabilities == ("execute_order",)


def test_manifest_con_entrypoint() -> None:
    data = dict(_MINIMO)
    data["entrypoint"] = "ce_v5.components.dummy:build"
    manifest = validate_manifest(data)
    assert manifest.entrypoint == "ce_v5.components.dummy:build"


def test_entrypoint_vacio_falla() -> None:
    data = dict(_MINIMO)
    data["entrypoint"] = "   "
    with pytest.raises(ValidationError):
        validate_manifest(data)


def test_entrypoint_ausente_es_none() -> None:
    manifest = validate_manifest(_MINIMO)
    assert manifest.entrypoint is None
