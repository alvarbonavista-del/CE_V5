"""Componente de muestra: recorre el lifecycle sin logica de dominio.

Demostrador de "copiar carpeta + reiniciar" (CE-14) y referencia minima de
como se escribe un Componente. Implementa el contrato ComponentLifecycle por
COMPOSICION (no hereda ninguna clase base, ADR-001). No hace trabajo de
dominio: solo lleva una bandera de si esta en marcha.
"""

from __future__ import annotations


class SampleComponent:
    """Componente minimo que satisface el contrato ComponentLifecycle."""

    def __init__(self) -> None:
        self.running = False

    def initialize(self) -> None:
        return None

    def start(self) -> None:
        self.running = True

    def pause(self) -> None:
        self.running = False

    def resume(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def unload(self) -> None:
        return None


def build() -> SampleComponent:
    """Factory declarada como entrypoint en el manifest (ADR-009)."""
    return SampleComponent()
