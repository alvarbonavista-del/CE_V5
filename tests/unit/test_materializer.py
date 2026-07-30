"""Tests deterministas del materializador WINDOWED (CE-14, materializacion)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from ce_v5.platform.rules.materializer import materialize_windowed


def _last(window: Sequence[Decimal]) -> Decimal:
    """Transform de prueba: el ultimo elemento de la ventana (valor 'actual')."""
    return window[-1]


def _span(window: Sequence[Decimal]) -> Decimal:
    """Transform de prueba: max - min de la ventana (depende de TODA la ventana)."""
    return max(window) - min(window)


def test_ventana_rodante_valores_correctos() -> None:
    base = [Decimal(v) for v in (10, 12, 9, 15, 11)]
    # window_bars=3 -> spans de [10,12,9]=3, [12,9,15]=6, [9,15,11]=6
    serie = materialize_windowed(base, _span, window_bars=3, history_bars=10)
    assert serie == (Decimal(3), Decimal(6), Decimal(6))


def test_toma_las_history_bars_mas_recientes() -> None:
    base = [Decimal(v) for v in (10, 12, 9, 15, 11)]
    # computables (w=3): 3 valores; pedimos solo las 2 mas recientes.
    serie = materialize_windowed(base, _span, window_bars=3, history_bars=2)
    assert serie == (Decimal(6), Decimal(6))


def test_transform_recibe_la_ventana_exacta() -> None:
    base = [Decimal(v) for v in (1, 2, 3, 4)]
    # _last sobre w=2 -> el ultimo de cada [i-1,i]: 2, 3, 4.
    serie = materialize_windowed(base, _last, window_bars=2, history_bars=10)
    assert serie == (Decimal(2), Decimal(3), Decimal(4))


def test_ventana_de_1_es_valor_por_barra() -> None:
    base = [Decimal(v) for v in (5, 6, 7)]
    serie = materialize_windowed(base, _last, window_bars=1, history_bars=10)
    assert serie == (Decimal(5), Decimal(6), Decimal(7))


def test_base_mas_corta_que_la_ventana_da_vacio() -> None:
    base = [Decimal(1), Decimal(2)]
    assert materialize_windowed(base, _span, window_bars=3, history_bars=5) == ()


def test_base_vacia_da_vacio() -> None:
    assert materialize_windowed([], _last, window_bars=1, history_bars=5) == ()


def test_history_bars_no_positivo_da_vacio() -> None:
    base = [Decimal(1), Decimal(2), Decimal(3)]
    assert materialize_windowed(base, _last, window_bars=1, history_bars=0) == ()


def test_window_bars_no_positivo_da_vacio() -> None:
    base = [Decimal(1), Decimal(2), Decimal(3)]
    assert materialize_windowed(base, _last, window_bars=0, history_bars=5) == ()


def test_exactamente_una_ventana() -> None:
    base = [Decimal(v) for v in (4, 9, 2)]
    # w=3, base de 3 -> exactamente 1 valor: span [4,9,2] = 7.
    serie = materialize_windowed(base, _span, window_bars=3, history_bars=5)
    assert serie == (Decimal(7),)


def test_determinismo() -> None:
    base = [Decimal(v) for v in (10, 12, 9, 15, 11)]
    a = materialize_windowed(base, _span, window_bars=3, history_bars=10)
    b = materialize_windowed(base, _span, window_bars=3, history_bars=10)
    assert a == b
