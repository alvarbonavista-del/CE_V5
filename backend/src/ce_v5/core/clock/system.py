"""Reloj real de sistema en UTC epoch ms (ADR-007)."""

import time


class SystemClock:
    """Clock real: lee el reloj del sistema en UTC epoch milliseconds.

    Usa time.time_ns() para no perder precision por coma flotante; el
    resultado es UTC por definicion de epoch (ADR-007).
    """

    def now_ms(self) -> int:
        """Instante actual en UTC epoch milliseconds (int64)."""
        return time.time_ns() // 1_000_000
