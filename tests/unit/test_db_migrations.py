"""Tests unitarios del runner de migraciones (sin base de datos)."""

from pathlib import Path

import pytest

from ce_v5.infra.db.migrations.runner import MigrationsError, discover_migrations


def _write(directory: Path, name: str, content: str) -> None:
    (directory / name).write_text(content, encoding="utf-8")


def test_discover_ordena_por_version(tmp_path: Path) -> None:
    _write(tmp_path, "0002_b.sql", "SELECT 2;")
    _write(tmp_path, "0001_a.sql", "SELECT 1;")
    migrations = discover_migrations(tmp_path)
    assert [m.version for m in migrations] == ["0001", "0002"]
    assert [m.name for m in migrations] == ["a", "b"]


def test_discover_calcula_checksum_estable(tmp_path: Path) -> None:
    _write(tmp_path, "0001_a.sql", "SELECT 1;\n")
    first = discover_migrations(tmp_path)[0].checksum
    _write(tmp_path, "0001_a.sql", "SELECT 1;\n")
    second = discover_migrations(tmp_path)[0].checksum
    assert first == second
    assert len(first) == 64


def test_discover_rechaza_nombre_invalido(tmp_path: Path) -> None:
    _write(tmp_path, "no_numero.sql", "SELECT 1;")
    with pytest.raises(MigrationsError):
        discover_migrations(tmp_path)


def test_discover_rechaza_version_duplicada(tmp_path: Path) -> None:
    _write(tmp_path, "0001_a.sql", "SELECT 1;")
    _write(tmp_path, "0001_b.sql", "SELECT 2;")
    with pytest.raises(MigrationsError):
        discover_migrations(tmp_path)


def test_discover_rechaza_directorio_inexistente(tmp_path: Path) -> None:
    with pytest.raises(MigrationsError):
        discover_migrations(tmp_path / "no_existe")
