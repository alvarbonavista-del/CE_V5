"""Tests de la logica PURA del limitador de intentos (P06b, CA-10). Sin Redis."""

import pytest

from ce_v5.core.auth.rate_limit import (
    RateLimitConfig,
    RateLimitConfigError,
    RateLimitRule,
    decide,
    digest,
    retry_after_seconds,
)

_SECRETO = "s" * 32
_OTRO_SECRETO = "x" * 32
_EMAIL = "ana@ejemplo.test"

_SIN_CONTADORES: dict[str, int] = {}


def _config() -> RateLimitConfig:
    return RateLimitConfig(digest_secret=_SECRETO)


def test_la_huella_no_contiene_el_valor_original() -> None:
    huella = digest(_EMAIL, _SECRETO)
    assert _EMAIL not in huella
    assert "ana" not in huella
    assert "ejemplo" not in huella


def test_la_huella_es_estable_con_el_mismo_secreto() -> None:
    assert digest(_EMAIL, _SECRETO) == digest(_EMAIL, _SECRETO)


def test_la_huella_cambia_con_otro_secreto() -> None:
    # Sin el secreto no se pueden recomputar las huellas: quien se asome al almacen no
    # reconstruye la lista de emails.
    assert digest(_EMAIL, _SECRETO) != digest(_EMAIL, _OTRO_SECRETO)


def test_retry_after_es_cero_por_debajo_del_umbral() -> None:
    regla = RateLimitRule(max_failures=5, window_seconds=300)
    assert retry_after_seconds(1, regla, cap=300) == 0
    assert retry_after_seconds(5, regla, cap=300) == 0


def test_retry_after_crece_con_el_exceso_y_se_topa() -> None:
    regla = RateLimitRule(max_failures=5, window_seconds=300)
    # Una cuesta cada vez mas empinada: 2, 4, 8...
    assert retry_after_seconds(6, regla, cap=300) == 2
    assert retry_after_seconds(7, regla, cap=300) == 4
    assert retry_after_seconds(8, regla, cap=300) == 8
    # El tope existe para que la espera NUNCA sea infinita: el bloqueo caduca solo.
    assert retry_after_seconds(50, regla, cap=300) == 300


def test_sin_bloqueos_vivos_se_permite() -> None:
    # Los contadores altos por si solos NO frenan: lo que frena es la llave de bloqueo.
    decision = decide({"ip": 29, "account": 9, "ip_account": 4}, {}, _config())
    assert decision.allowed is True
    assert decision.retry_after_seconds == 0


def test_un_bloqueo_de_ip_mas_cuenta_deniega() -> None:
    decision = decide(_SIN_CONTADORES, {"ip_account": 8}, _config())
    assert decision.allowed is False
    assert decision.dimension == "ip_account"
    assert decision.retry_after_seconds == 8


def test_un_bloqueo_de_cuenta_deniega() -> None:
    # Mil maquinas contra una victima: cada IP esta limpia, la cuenta no.
    decision = decide(_SIN_CONTADORES, {"account": 16}, _config())
    assert decision.allowed is False
    assert decision.dimension == "account"


def test_un_bloqueo_de_ip_deniega() -> None:
    # Uno que prueba mil claves repartidas entre muchas cuentas.
    decision = decide(_SIN_CONTADORES, {"ip": 4}, _config())
    assert decision.allowed is False
    assert decision.dimension == "ip"


def test_un_ttl_agotado_no_frena() -> None:
    # TTL <= 0: la llave ya no existe. El bloqueo se deshace SOLO.
    decision = decide(_SIN_CONTADORES, {"ip_account": 0, "account": -2}, _config())
    assert decision.allowed is True


def test_manda_el_bloqueo_mas_largo() -> None:
    decision = decide(_SIN_CONTADORES, {"ip": 64, "ip_account": 2}, _config())
    assert decision.dimension == "ip"
    assert decision.retry_after_seconds == 64


def test_sin_dimension_de_ip_se_evalua_el_resto() -> None:
    # Peticion sin IP conocida: esa dimension no se inventa, pero la cuenta sigue viva.
    decision = decide(_SIN_CONTADORES, {"account": 16}, _config())
    assert decision.allowed is False
    assert decision.dimension == "account"


def test_from_env_sin_secreto_falla() -> None:
    with pytest.raises(RateLimitConfigError):
        RateLimitConfig.from_env({})


def test_from_env_con_secreto_corto_falla() -> None:
    with pytest.raises(RateLimitConfigError):
        RateLimitConfig.from_env({"CE_V5_RATE_LIMIT_SECRET": "s" * 31})


def test_from_env_con_secreto_valido() -> None:
    config = RateLimitConfig.from_env({"CE_V5_RATE_LIMIT_SECRET": _SECRETO})
    # IP+cuenta es el mas estrecho; el de IP, el mas ancho.
    assert config.by_ip_account.max_failures < config.by_account.max_failures
    assert config.by_account.max_failures < config.by_ip.max_failures
