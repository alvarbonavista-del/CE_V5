"""Guardias de arranque (P06b, dictamen CSA M; prueba 15). Sin PostgreSQL.

Una configuracion insegura NO SE AVISA: SE RECHAZA.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from ce_v5.core.auth.config import AuthConfig
from ce_v5.core.auth.rate_limit import RateLimitConfig
from ce_v5.entrypoints.api.config import ApiConfig
from ce_v5.entrypoints.api.startup_guards import (
    InsecureConfigurationError,
    assert_secure_startup,
)
from ce_v5.infra.db.ports import Session

_JWT = "secreto-de-firma-de-32-caracteres-o-mas"
_HUELLAS = "secreto-de-huellas-de-32-caracteres"


class _FakeSession:
    """Sesion que responde lo que se le diga sobre el rol conectado."""

    def __init__(self, *, superuser: bool, bypassrls: bool) -> None:
        self._row = (superuser, bypassrls)

    def execute(self, query: str, params: Any = None) -> None:  # noqa: ANN401
        return None

    def fetchone(self, query: str, params: Any = None) -> tuple[object, ...] | None:  # noqa: ANN401
        return self._row

    def fetchall(self, query: str, params: Any = None) -> list[tuple[object, ...]]:  # noqa: ANN401
        return [self._row]


class _FakeDatabase:
    """Database en memoria: el guardia solo necesita abrir una transaccion."""

    def __init__(self, *, superuser: bool = False, bypassrls: bool = False) -> None:
        self._session = _FakeSession(superuser=superuser, bypassrls=bypassrls)

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        yield self._session

    def close(self) -> None:
        return None


def _produccion(jwt: str = _JWT, huellas: str = _HUELLAS) -> tuple[Any, ...]:
    return (
        ApiConfig(environment="production"),
        AuthConfig(jwt_secret=jwt),
        RateLimitConfig(digest_secret=huellas),
        _FakeDatabase(),
    )


def test_un_secreto_de_plantilla_en_produccion_no_arranca() -> None:
    # Un secreto de ejemplo en produccion es un secreto PUBLICADO: esta en el repo.
    api, _, rate, db = _produccion()
    plantilla = AuthConfig(jwt_secret="CAMBIAME_POR_UN_SECRETO_ALEATORIO_DE_32")
    with pytest.raises(InsecureConfigurationError):
        assert_secure_startup(api, plantilla, rate, db)


def test_el_mismo_secreto_para_tokens_y_limitador_no_arranca() -> None:
    # Filtrar uno filtraria los dos: quien se lleve el del limitador podria fabricarse
    # tokens de acceso validos.
    api, auth, _, db = _produccion()
    mismo = RateLimitConfig(digest_secret=_JWT)
    with pytest.raises(InsecureConfigurationError):
        assert_secure_startup(api, auth, mismo, db)


def test_un_rol_que_puede_saltarse_el_rls_no_arranca() -> None:
    api, auth, rate, _ = _produccion()
    with pytest.raises(InsecureConfigurationError):
        assert_secure_startup(api, auth, rate, _FakeDatabase(bypassrls=True))
    with pytest.raises(InsecureConfigurationError):
        assert_secure_startup(api, auth, rate, _FakeDatabase(superuser=True))


def test_una_configuracion_sana_arranca() -> None:
    assert_secure_startup(*_produccion())


def test_fuera_de_produccion_los_secretos_de_plantilla_se_toleran() -> None:
    # En desarrollo hay que poder trabajar con el .env.example tal cual; lo que no puede
    # es llegar a produccion.
    assert_secure_startup(
        ApiConfig(environment="development"),
        AuthConfig(jwt_secret="CAMBIAME_POR_UN_SECRETO_ALEATORIO_DE_32"),
        RateLimitConfig(digest_secret="CAMBIAME_POR_UN_SECRETO_ALEATORIO_DE_32"),
        _FakeDatabase(),
    )
