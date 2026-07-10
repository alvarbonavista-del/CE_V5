"""Modelo y validacion estatica del ComponentManifest (ADR-008)."""

from ce_v5.core.manifest.manifest import (
    MANIFEST_SCHEMA_VERSION,
    Capability,
    CapabilityKind,
    ComponentManifest,
    ComponentType,
    PolicyRequirements,
    Requires,
    SchemaRef,
    UiDeclaration,
    validate_manifest,
)

__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "Capability",
    "CapabilityKind",
    "ComponentManifest",
    "ComponentType",
    "PolicyRequirements",
    "Requires",
    "SchemaRef",
    "UiDeclaration",
    "validate_manifest",
]
