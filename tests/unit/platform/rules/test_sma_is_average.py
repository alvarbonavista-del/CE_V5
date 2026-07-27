"""P2 (dictamen P08b-04): "SMA real" NO es una DataSource; es la funcion
canonica average(market.close, N). Este test prueba que average reproduce la
media simple real (SMA) contra un referente EXACTO (fractions.Fraction) y que
devuelve NOT_EVALUABLE sin historia suficiente. Cierra el entregable de "SMA"
sin construir codigo muerto (5.11/CE-8). NO confundir con el "SMA" de
KLineChart, que es un EMA (I-01 CRITICO 4): si average == media exacta, queda
descartado que sea ese suavizado.
"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

from ce_v5.platform.rules.functions import average

_CLOSES = [
    Decimal(v)
    for v in (
        "100.00",
        "100.50",
        "101.20",
        "100.80",
        "101.50",
        "102.30",
        "101.90",
        "102.70",
        "103.40",
        "102.60",
        "101.80",
        "102.20",
        "103.10",
        "104.00",
        "103.50",
        "104.60",
        "105.20",
        "104.40",
        "103.90",
        "104.80",
        "105.70",
        "106.30",
        "105.60",
        "104.90",
        "105.50",
    )
]

_TOL = Fraction(1, 10**18)


def _exact_sma(window: tuple[Decimal, ...], count: int) -> Fraction:
    last = window[-count:]
    return sum((Fraction(c) for c in last), Fraction(0)) / count


def test_average_reproduces_simple_moving_average() -> None:
    for count in (5, 14, 20):
        for end in range(count, len(_CLOSES) + 1):
            window = tuple(_CLOSES[:end])
            fv = average(window, count)
            assert fv.evaluable, f"count={count} end={end}: deberia ser evaluable"
            assert fv.value is not None
            diff = abs(Fraction(fv.value) - _exact_sma(window, count))
            assert diff <= _TOL, f"count={count} end={end}: fuera de tolerancia"


def test_average_not_evaluable_without_enough_history() -> None:
    window = tuple(_CLOSES[:5])
    fv = average(window, 14)
    assert not fv.evaluable
    assert fv.value is None
