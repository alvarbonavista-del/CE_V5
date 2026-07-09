"""CLI: aplica las migraciones pendientes contra la DB configurada.

Uso: python -m ce_v5.infra.db.migrations
Requiere la variable de entorno CE_V5_DATABASE_URL con el DSN de PostgreSQL.
"""

from __future__ import annotations

from ce_v5.infra.db.config import DbConfig
from ce_v5.infra.db.migrations.runner import apply_migrations
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase


def main() -> None:
    config = DbConfig.from_env()
    db = PsycopgDatabase(config)
    try:
        applied = apply_migrations(db)
    finally:
        db.close()
    if applied:
        print("Migraciones aplicadas: " + ", ".join(applied))
    else:
        print("No hay migraciones pendientes.")


if __name__ == "__main__":
    main()
