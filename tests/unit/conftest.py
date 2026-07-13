"""Fixtures del doble en memoria del EventBus (ADR-013).

El doble vive en tests/support/inmemory_bus.py para que tambien lo pueda usar el test
de EQUIVALENCIA de integracion (CA-12): un doble duplicado se separa del original en
silencio, y entonces deja de probar nada.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from ce_v5.core.bus import EventBus
from support.inmemory_bus import InMemoryEventBus, LogicalClock


@pytest.fixture
def _bus_clock() -> LogicalClock:
    return LogicalClock()


@pytest.fixture
def in_memory_bus(_bus_clock: LogicalClock) -> EventBus:
    return InMemoryEventBus(clock=_bus_clock)


@pytest.fixture
def advance_time(_bus_clock: LogicalClock) -> Callable[[int], None]:
    return _bus_clock.advance
