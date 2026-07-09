"""Runner de migraciones append-only para PostgreSQL (ADR-005).

Aplica ficheros .sql numerados (NNNN_nombre.sql) en orden, registra cada
uno en la tabla schema_migrations con su checksum y verifica que una
migracion ya aplicada no se ha modificado en disco. Una migracion aplicada
es historica: un cambio posterior es una migracion sucesora, nunca una
edicion.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from ce_v5.infra.db.ports import Database

_MIGRATION_FILENAME = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")

_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     text PRIMARY KEY,
    name        text NOT NULL,
    checksum    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationsError(RuntimeError):
    """Error del sistema de migraciones."""


@dataclass(frozen=True, slots=True)
class Migration:
    """Una migracion descubierta en disco, con su huella de contenido."""

    version: str
    name: str
    sql: str
    checksum: str


def _default_sql_dir() -> Path:
    return Path(__file__).parent / "sql"


def _checksum(text: str) -> str:
    normalized = text.replace("\r\n", "\n").encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def discover_migrations(sql_dir: Path | None = None) -> list[Migration]:
    """Lee y ordena las migraciones de un directorio. Valida nombres y unicidad."""
    directory = _default_sql_dir() if sql_dir is None else sql_dir
    if not directory.is_dir():
        raise MigrationsError(f"No existe el directorio de migraciones: {directory}")
    migrations: list[Migration] = []
    seen_versions: set[str] = set()
    for path in sorted(directory.glob("*.sql")):
        match = _MIGRATION_FILENAME.match(path.name)
        if match is None:
            raise MigrationsError(
                f"Nombre de migracion invalido: {path.name} (esperado NNNN_nombre.sql)."
            )
        version = match.group("version")
        if version in seen_versions:
            raise MigrationsError(f"Version de migracion duplicada: {version}.")
        seen_versions.add(version)
        text = path.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=version,
                name=match.group("name"),
                sql=text,
                checksum=_checksum(text),
            )
        )
    return migrations


def apply_migrations(db: Database, sql_dir: Path | None = None) -> list[str]:
    """Aplica las migraciones pendientes en orden. Devuelve las nuevas versiones.

    Verifica que las ya aplicadas no se han modificado (checksum). Cada
    migracion pendiente se aplica en su propia transaccion junto con su
    registro en schema_migrations: o entra entera, o no entra (atomicidad).
    """
    migrations = discover_migrations(sql_dir)

    with db.transaction() as session:
        session.execute(_SCHEMA_MIGRATIONS_DDL)

    with db.transaction() as session:
        rows = session.fetchall("SELECT version, checksum FROM schema_migrations")
    applied: dict[str, str] = {
        str(version): str(checksum) for version, checksum in rows
    }

    newly_applied: list[str] = []
    for migration in migrations:
        existing = applied.get(migration.version)
        if existing is not None:
            if existing != migration.checksum:
                raise MigrationsError(
                    f"La migracion {migration.version} ya aplicada ha cambiado "
                    "en disco (checksum distinto). Una migracion aplicada es "
                    "historica: crea una sucesora, no la edites."
                )
            continue
        with db.transaction() as session:
            session.execute(migration.sql)
            session.execute(
                "INSERT INTO schema_migrations (version, name, checksum) "
                "VALUES (%s, %s, %s)",
                (migration.version, migration.name, migration.checksum),
            )
        newly_applied.append(migration.version)
    return newly_applied
