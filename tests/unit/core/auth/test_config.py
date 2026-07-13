"""Tests de AuthConfig (P06b). Entorno EXPLICITO: jamas se lee os.environ."""

import pytest

from ce_v5.core.auth import AuthConfig, AuthConfigError

_SECRETO = "s" * 32


def test_from_env_usa_los_valores_por_defecto_sin_ttls() -> None:
    config = AuthConfig.from_env({"CE_V5_JWT_SECRET": _SECRETO})
    assert config.jwt_secret == _SECRETO
    assert config.access_ttl_seconds == 900
    assert config.refresh_ttl_seconds == 1_209_600


def test_from_env_sin_secreto_falla() -> None:
    with pytest.raises(AuthConfigError):
        AuthConfig.from_env({})


def test_secreto_corto_falla() -> None:
    with pytest.raises(AuthConfigError):
        AuthConfig.from_env({"CE_V5_JWT_SECRET": "s" * 31})


def test_acceso_que_vive_tanto_como_el_refresh_falla() -> None:
    with pytest.raises(AuthConfigError):
        AuthConfig(jwt_secret=_SECRETO, access_ttl_seconds=100, refresh_ttl_seconds=100)


def test_ttl_no_numerico_falla() -> None:
    with pytest.raises(AuthConfigError):
        AuthConfig.from_env(
            {
                "CE_V5_JWT_SECRET": _SECRETO,
                "CE_V5_ACCESS_TOKEN_TTL_SECONDS": "quince-minutos",
            }
        )
