"""Discovery de Componentes por carpeta (ADR-009)."""

from ce_v5.core.discovery.discovery import (
    MANIFEST_FILENAME,
    DiscoveryResult,
    EntrypointLoader,
    Rejection,
    RejectionReason,
    discover,
    import_entrypoint,
)

__all__ = [
    "MANIFEST_FILENAME",
    "DiscoveryResult",
    "EntrypointLoader",
    "Rejection",
    "RejectionReason",
    "discover",
    "import_entrypoint",
]
