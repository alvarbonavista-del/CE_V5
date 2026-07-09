"""Sistema de migraciones append-only para la persistencia (ADR-005)."""

from ce_v5.infra.db.migrations.runner import (
    Migration,
    MigrationsError,
    apply_migrations,
    discover_migrations,
)

__all__ = [
    "Migration",
    "MigrationsError",
    "apply_migrations",
    "discover_migrations",
]
