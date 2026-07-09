"""Tests unitarios de la configuracion de conexion (infra/db/config.py)."""

import pytest

from ce_v5.infra.db.config import DSN_ENV_VAR, DbConfig, DbConfigError


def test_from_env_lee_el_dsn() -> None:
    config = DbConfig.from_env({DSN_ENV_VAR: "postgresql://localhost/db"})
    assert config.dsn == "postgresql://localhost/db"


def test_from_env_recorta_espacios() -> None:
    config = DbConfig.from_env({DSN_ENV_VAR: "  postgresql://localhost/db  "})
    assert config.dsn == "postgresql://localhost/db"


def test_from_env_falla_si_falta_la_variable() -> None:
    with pytest.raises(DbConfigError):
        DbConfig.from_env({})


def test_from_env_falla_si_esta_vacia() -> None:
    with pytest.raises(DbConfigError):
        DbConfig.from_env({DSN_ENV_VAR: "   "})
