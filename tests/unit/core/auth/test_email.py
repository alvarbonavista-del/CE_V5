"""Tests de la normalizacion canonica del email (P06b)."""

import pytest

from ce_v5.core.auth import normalize_email


def test_pasa_a_minusculas_y_recorta_espacios() -> None:
    assert normalize_email("  Ana@Ejemplo.TEST \n") == "ana@ejemplo.test"


def test_ya_normalizado_no_cambia() -> None:
    assert normalize_email("ana@ejemplo.test") == "ana@ejemplo.test"


def test_email_vacio_falla() -> None:
    with pytest.raises(ValueError):
        normalize_email("   ")
