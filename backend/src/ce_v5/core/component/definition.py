"""ComponentDefinition: lo que el discovery descubre (ADR-009, ADR-010)."""

from dataclasses import dataclass
from pathlib import Path

from ce_v5.core.manifest import ComponentManifest


@dataclass(frozen=True, slots=True)
class ComponentDefinition:
    """Un Componente descubierto: su manifest validado y donde vive.

    ADR-010 distingue la Definition (global, lo que el discovery descubre)
    de la Instance (objeto vivo de runtime, Bloque 5). La Definition NO es
    una instancia: es la plantilla desde la que el supervisor crea
    ComponentInstances. Identidad: (component_id, version).
    """

    manifest: ComponentManifest
    path: Path

    @property
    def component_id(self) -> str:
        return self.manifest.id

    @property
    def version(self) -> str:
        return self.manifest.version

    @property
    def entrypoint(self) -> str | None:
        return self.manifest.entrypoint
