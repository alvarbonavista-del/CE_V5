"""Smoke test del esqueleto P00.

Verifica que el paquete backend esta correctamente instalado y que las
capas declaradas en DOC_ESTRUCTURA sec.3 son importables. No prueba
logica de negocio (no existe en P00): valida que el empaquetado del
monorepo es correcto, cazando roturas de estructura desde el commit 0.
"""

from __future__ import annotations

import importlib


def test_root_package_importable() -> None:
    module = importlib.import_module("ce_v5")
    assert module is not None


def test_layers_importable() -> None:
    for layer in (
        "ce_v5.core",
        "ce_v5.components",
        "ce_v5.platform",
        "ce_v5.entrypoints",
        "ce_v5.infra",
    ):
        module = importlib.import_module(layer)
        assert module is not None
