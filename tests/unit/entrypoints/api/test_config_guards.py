"""Guardias de arranque de la puerta publica (P06b, dictamen CSA prueba 10).

Una configuracion insegura no se avisa: se RECHAZA. Un aviso en un log lo lee alguien
tres semanas despues; una excepcion la lee quien despliega, ahora.
"""

import pytest

from ce_v5.entrypoints.api.config import ApiConfig, ApiConfigError


def test_el_comodin_en_cors_no_arranca() -> None:
    # Prueba 10: con credenciales, un "*" permitiria a CUALQUIER web del mundo hacer
    # peticiones autenticadas con la cookie del usuario. Es la puerta abierta.
    with pytest.raises(ApiConfigError):
        ApiConfig(allowed_origins=("*",))
    with pytest.raises(ApiConfigError):
        ApiConfig.from_env({"CE_V5_CORS_ALLOWED_ORIGINS": "https://ok.test,*"})


def test_cookies_sin_secure_en_produccion_no_arrancan() -> None:
    with pytest.raises(ApiConfigError):
        ApiConfig(environment="production", cookie_secure=False)


def test_cookies_sin_secure_fuera_de_produccion_se_admiten() -> None:
    # Desarrollar sin HTTPS debe ser posible; lo que no puede es llegar a produccion.
    config = ApiConfig(environment="development", cookie_secure=False)
    assert config.cookie_secure is False


def test_un_cuerpo_maximo_no_positivo_no_arranca() -> None:
    with pytest.raises(ApiConfigError):
        ApiConfig(max_body_bytes=0)
    with pytest.raises(ApiConfigError):
        ApiConfig(max_body_bytes=-1)


def test_los_valores_por_defecto_son_los_seguros() -> None:
    config = ApiConfig.from_env({})
    assert config.cookie_secure is True
    assert config.allowed_origins == ()
    assert config.max_body_bytes == 65_536
    assert config.trusted_proxy_count == 0
    assert config.is_production is False


def test_los_origenes_se_leen_separados_por_coma() -> None:
    config = ApiConfig.from_env(
        {"CE_V5_CORS_ALLOWED_ORIGINS": "https://a.test, https://b.test"}
    )
    assert config.allowed_origins == ("https://a.test", "https://b.test")
