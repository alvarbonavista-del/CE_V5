"""Interfaz del Clock/TimeProvider inyectable (ADR-007).

Todo componente que cree o transforme eventos, procese ventanas, evalue
reglas o calcule expiraciones recibe un Clock por inyeccion, en lugar de
llamar a time.time()/datetime.now() de forma dispersa (leccion v4). El
Clock devuelve el instante actual en UTC epoch milliseconds (int64), el
formato canonico de tiempo (EpochMillis, ADR-007).
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Proveedor de tiempo inyectable (ADR-007)."""

    def now_ms(self) -> int:
        """Instante actual en UTC epoch milliseconds (int64)."""
        ...
