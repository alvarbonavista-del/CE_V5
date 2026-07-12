"""Tests unitarios de la configuracion de conexion (infra/db/config.py)."""

import pytest

from ce_v5.infra.db.config import (
    DSN_ENV_VAR,
    OPERATOR_DSN_ENV_VAR,
    DbConfig,
    DbConfigError,
    OperatorDbConfig,
    OperatorDsnInRuntimeError,
)


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


def test_from_env_rechaza_dsn_de_operador_en_runtime() -> None:
    # Guardia fail-closed (CA-03): un proceso de runtime no puede portar el
    # DSN de operador; from_env aborta y el proceso no arranca.
    with pytest.raises(OperatorDsnInRuntimeError):
        DbConfig.from_env(
            {
                DSN_ENV_VAR: "postgresql://ce_v5_app@localhost/db",
                OPERATOR_DSN_ENV_VAR: "postgresql://ce_v5_operator@localhost/db",
            }
        )


def test_from_env_ok_sin_dsn_de_operador() -> None:
    config = DbConfig.from_env({DSN_ENV_VAR: "postgresql://localhost/db"})
    assert config.dsn == "postgresql://localhost/db"


def test_from_env_guardia_usa_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DSN_ENV_VAR, "postgresql://ce_v5_app@localhost/db")
    monkeypatch.setenv(OPERATOR_DSN_ENV_VAR, "postgresql://ce_v5_operator@localhost/db")
    with pytest.raises(OperatorDsnInRuntimeError):
        DbConfig.from_env()


def test_operator_from_env_lee_el_dsn() -> None:
    config = OperatorDbConfig.from_env(
        {OPERATOR_DSN_ENV_VAR: "postgresql://ce_v5_operator@localhost/db"}
    )
    assert config.dsn == "postgresql://ce_v5_operator@localhost/db"


def test_operator_from_env_falla_si_falta_la_variable() -> None:
    with pytest.raises(DbConfigError):
        OperatorDbConfig.from_env({})
