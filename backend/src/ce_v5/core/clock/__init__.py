"""Clock/TimeProvider inyectable (ADR-007): reloj real y simulado."""

from ce_v5.core.clock.protocol import Clock
from ce_v5.core.clock.simulated import SimulatedClock
from ce_v5.core.clock.system import SystemClock

__all__ = ["Clock", "SimulatedClock", "SystemClock"]
