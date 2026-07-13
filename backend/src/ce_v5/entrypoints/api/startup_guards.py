"""Guardias de arranque (P06b, dictamen CSA M; prueba 15).

Una configuracion insegura NO SE AVISA: SE RECHAZA. Un aviso en un log lo lee alguien
tres semanas despues; una excepcion la lee quien despliega, ahora.

Continua la disciplina de P05 (rol sin BYPASSRLS) y de P06/CA-03 (el DSN de operador
jamas en runtime), que ya se hacen cumplir por codigo.
"""

from __future__ import annotations

from ce_v5.core.auth.config import AuthConfig
from ce_v5.core.auth.rate_limit import RateLimitConfig
from ce_v5.entrypoints.api.config import ApiConfig
from ce_v5.infra.db.ports import Database
from ce_v5.infra.db.tenancy import AppRoleError, assert_app_role_cannot_bypass_rls

# Marca de los valores de plantilla de .env.example. Un secreto de ejemplo en produccion
# es un secreto PUBLICADO: esta en el repositorio, a la vista de cualquiera.
_PLANTILLA = "cambiame"


class InsecureConfigurationError(RuntimeError):
    """La configuracion es insegura: el proceso NO arranca."""


def assert_secure_startup(
    api_config: ApiConfig,
    auth_config: AuthConfig,
    rate_config: RateLimitConfig,
    database: Database,
) -> None:
    """Se niega a arrancar si la configuracion es insegura.

    Comprueba lo que NADIE MAS comprueba. El comodin de CORS, las cookies sin Secure, el
    secreto ausente o corto y el DSN de operador en runtime ya los rechazan ApiConfig,
    AuthConfig, RateLimitConfig y DbConfig.from_env: este guardia los COMPLETA, no los
    duplica.
    """
    if api_config.is_production:
        secretos = {
            "CE_V5_JWT_SECRET": auth_config.jwt_secret,
            "CE_V5_RATE_LIMIT_SECRET": rate_config.digest_secret,
        }
        for nombre, valor in secretos.items():
            if _PLANTILLA in valor.lower():
                raise InsecureConfigurationError(
                    f"{nombre} conserva el valor de PLANTILLA de .env.example. Un "
                    "secreto de ejemplo en produccion es un secreto publicado: esta en "
                    "el repositorio, a la vista de cualquiera. El proceso no arranca."
                )
        if auth_config.jwt_secret == rate_config.digest_secret:
            raise InsecureConfigurationError(
                "El secreto de firma de tokens y el de las huellas del limitador son "
                "EL MISMO. Reutilizar un secreto significa que filtrar uno filtra los "
                "dos: quien se lleve el del limitador podria fabricarse tokens de "
                "acceso validos. El proceso no arranca."
            )

    # El aislamiento entre usuarios no puede ser decorativo: si el rol conectado pudiera
    # saltarse el RLS, las policies no le aplicarian y la separacion seria un adorno.
    # Se reutiliza la comprobacion de P05, que ya sabe hacer esto.
    try:
        with database.transaction() as session:
            assert_app_role_cannot_bypass_rls(session)
    except AppRoleError as exc:
        raise InsecureConfigurationError(
            "El rol de base de datos con el que arranca la API puede saltarse el RLS "
            "(SUPERUSER o BYPASSRLS): el aislamiento entre usuarios seria decorativo. "
            "El proceso no arranca."
        ) from exc
