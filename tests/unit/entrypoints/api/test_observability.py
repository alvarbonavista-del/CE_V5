"""Tests de los logs estructurados (P06b, dictamen CSA J).

Lo que no esta en el log no se puede filtrar desde el log. La disciplina no puede
depender de que nadie se equivoque nunca: si alguien pasa un secreto por error, se
REDACTA.
"""

import json
import logging

import pytest

from ce_v5.entrypoints.api.observability import REDACTED, log_event


def _lineas(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [json.loads(registro.message) for registro in caplog.records]


def test_los_campos_sospechosos_se_redactan(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="ce_v5.api"):
        log_event(
            "prueba",
            password="secreta",
            access_token="jwt",
            authorization="Bearer x",
            cookie="ce_v5_refresh=x",
            secret="s",
            password_hash="$argon2id$...",
        )

    linea = _lineas(caplog)[0]
    for clave in (
        "password",
        "access_token",
        "authorization",
        "cookie",
        "secret",
        "password_hash",
    ):
        assert linea[clave] == REDACTED
    # Y el valor original no aparece en ningun sitio de la linea.
    crudo = json.dumps(linea)
    assert "secreta" not in crudo
    assert "argon2id" not in crudo


def test_se_registran_las_huellas_y_el_correlation_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="ce_v5.api"):
        log_event(
            "auth.login_failed",
            account="huella-del-email",
            ip="huella-de-la-ip",
            correlation_id="abc123",
            reason="bad_password",
        )

    linea = _lineas(caplog)[0]
    assert linea["event"] == "auth.login_failed"
    assert linea["account"] == "huella-del-email"
    assert linea["ip"] == "huella-de-la-ip"
    assert linea["correlation_id"] == "abc123"
    assert linea["reason"] == "bad_password"
