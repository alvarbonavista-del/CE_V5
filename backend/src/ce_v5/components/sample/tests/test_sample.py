"""Tests del componente de muestra (P04)."""

from __future__ import annotations

from ce_v5.components.sample import SampleComponent, build
from ce_v5.core.component import ComponentLifecycle


def test_build_devuelve_sample() -> None:
    assert isinstance(build(), SampleComponent)


def test_sample_cumple_el_contrato_de_lifecycle() -> None:
    assert isinstance(build(), ComponentLifecycle)


def test_start_y_stop_cambian_la_bandera() -> None:
    component = build()
    assert component.running is False
    component.start()
    assert component.running is True
    component.pause()
    assert component.running is False
    component.resume()
    assert component.running is True
    component.stop()
    assert component.running is False
