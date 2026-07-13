"""Tests del refresh token opaco (P06b): se guarda la huella, nunca el token."""

from ce_v5.core.auth import hash_refresh_token, new_refresh_token


def test_raw_y_hash_son_distintos() -> None:
    token = new_refresh_token()
    assert token.raw != token.hash


def test_el_hash_es_el_de_su_raw() -> None:
    token = new_refresh_token()
    assert token.hash == hash_refresh_token(token.raw)


def test_dos_tokens_seguidos_son_distintos() -> None:
    assert new_refresh_token().raw != new_refresh_token().raw


def test_el_raw_tiene_entropia_suficiente() -> None:
    # 32 bytes en base64 url-safe: al menos 40 caracteres.
    assert len(new_refresh_token().raw) >= 40
