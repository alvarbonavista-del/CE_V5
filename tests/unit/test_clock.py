import pytest

from ce_v5.core.clock import Clock, SimulatedClock, SystemClock


def _stamp(clock: Clock) -> int:
    return clock.now_ms()


def test_system_clock_devuelve_epoch_ms_plausible() -> None:
    now = SystemClock().now_ms()
    assert isinstance(now, int)
    assert now > 1_577_836_800_000


def test_system_clock_satisface_el_protocolo() -> None:
    assert isinstance(SystemClock(), Clock)


def test_simulated_clock_es_determinista() -> None:
    clock = SimulatedClock(start_ms=1_000)
    assert clock.now_ms() == 1_000
    assert clock.now_ms() == 1_000


def test_simulated_clock_set_y_advance() -> None:
    clock = SimulatedClock()
    assert clock.now_ms() == 0
    clock.set(5_000)
    assert clock.now_ms() == 5_000
    clock.advance(1_500)
    assert clock.now_ms() == 6_500


def test_simulated_clock_no_retrocede() -> None:
    clock = SimulatedClock()
    with pytest.raises(ValueError):
        clock.advance(-1)


def test_simulated_clock_satisface_el_protocolo() -> None:
    assert isinstance(SimulatedClock(), Clock)


def test_clock_inyectable_reproducible() -> None:
    sim = SimulatedClock(start_ms=1_700_000_000_000)
    assert _stamp(sim) == 1_700_000_000_000
    sim.advance(60_000)
    assert _stamp(sim) == 1_700_000_060_000
