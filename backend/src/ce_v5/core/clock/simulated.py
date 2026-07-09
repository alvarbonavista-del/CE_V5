"""Reloj simulado y determinista para tests y backtesting (ADR-007)."""


class SimulatedClock:
    """Clock determinista: el tiempo no avanza solo.

    Se inicializa en un instante fijo (UTC epoch ms) y solo cambia cuando
    se le ordena con set() o advance(). Permite reproducir escenarios
    temporales sin depender del reloj real (ADR-007: backtesting sin tocar
    la logica).
    """

    def __init__(self, start_ms: int = 0) -> None:
        self._now_ms = start_ms

    def now_ms(self) -> int:
        """Instante actual simulado en UTC epoch milliseconds (int64)."""
        return self._now_ms

    def set(self, ms: int) -> None:
        """Fija el instante actual (UTC epoch ms)."""
        self._now_ms = ms

    def advance(self, delta_ms: int) -> None:
        """Avanza el reloj delta_ms; no admite retroceso (delta negativo)."""
        if delta_ms < 0:
            msg = "advance no admite delta negativo; el tiempo simulado no retrocede."
            raise ValueError(msg)
        self._now_ms += delta_ms
