"""Tests del hash de contrasenas con Argon2id (P06b). Contrasenas FALSAS."""

from ce_v5.core.auth import Argon2PasswordHasher

_PASSWORD = "contrasena-falsa-de-test"


def test_el_hash_no_es_la_contrasena_y_es_argon2id() -> None:
    hasher = Argon2PasswordHasher()
    password_hash = hasher.hash(_PASSWORD)
    assert password_hash != _PASSWORD
    assert password_hash.startswith("$argon2id$")


def test_verify_con_la_contrasena_correcta() -> None:
    hasher = Argon2PasswordHasher()
    assert hasher.verify(hasher.hash(_PASSWORD), _PASSWORD) is True


def test_verify_con_la_contrasena_incorrecta() -> None:
    hasher = Argon2PasswordHasher()
    assert hasher.verify(hasher.hash(_PASSWORD), "otra-cosa") is False


def test_verify_con_un_hash_corrupto_no_propaga() -> None:
    hasher = Argon2PasswordHasher()
    assert hasher.verify("no-es-un-hash", "loquesea") is False


def test_dos_hashes_de_la_misma_contrasena_son_distintos() -> None:
    hasher = Argon2PasswordHasher()
    assert hasher.hash(_PASSWORD) != hasher.hash(_PASSWORD)
