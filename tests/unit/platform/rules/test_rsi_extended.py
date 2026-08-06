"""Refuerzo de fixtures del RSI puro (P08b): mas periodos, serie larga
determinista, casos borde, independencia del contexto Decimal ambiente y un
CANDADO GOLDEN que ata la salida bit-exacta (contexto + formula) a
RSI_FORMULA_VERSION (I-01 B5b: formula congelada, sin refactor silencioso).
Referente EXACTO con fractions.Fraction, self-contained (igual criterio que
test_rsi.py).
"""

from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from ce_v5.platform.rules.indicators.rsi import RSI_FORMULA_VERSION, wilder_rsi

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
        "106.80",
        "107.40",
        "106.60",
        "105.80",
        "106.40",
        "107.90",
        "108.50",
        "107.70",
        "106.90",
        "107.60",
        "108.80",
        "109.50",
        "108.60",
        "107.80",
        "108.90",
    )
]

_TOL = Fraction(1, 10**25)


def _reference_rsi(closes: list[Decimal], period: int) -> list[Fraction | None]:
    fr = [Fraction(c) for c in closes]
    n = len(fr)
    out: list[Fraction | None] = [None] * n
    if n < period + 1:
        return out
    gains: list[Fraction] = []
    losses: list[Fraction] = []
    for i in range(1, n):
        ch = fr[i] - fr[i - 1]
        gains.append(ch if ch > 0 else Fraction(0))
        losses.append(-ch if ch < 0 else Fraction(0))
    p = Fraction(period)

    def rsi(ag: Fraction, al: Fraction) -> Fraction:
        if al == 0:
            return Fraction(100)
        if ag == 0:
            return Fraction(0)
        return Fraction(100) - Fraction(100) / (1 + ag / al)

    ag = sum(gains[:period], Fraction(0)) / p
    al = sum(losses[:period], Fraction(0)) / p
    out[period] = rsi(ag, al)
    for i in range(period, n - 1):
        ag = (ag * (p - 1) + gains[i]) / p
        al = (al * (p - 1) + losses[i]) / p
        out[i + 1] = rsi(ag, al)
    return out


def _assert_matches_reference(closes: list[Decimal], period: int) -> None:
    got = wilder_rsi(closes, period)
    ref = _reference_rsi(closes, period)
    assert len(got) == len(ref) == len(closes)
    for i, (g, r) in enumerate(zip(got, ref, strict=True)):
        if r is None:
            assert g is None, f"i={i}: se esperaba None (warm-up)"
        else:
            assert g is not None, f"i={i}: valor faltante"
            assert abs(Fraction(g) - r) <= _TOL, f"i={i}: fuera de tolerancia"


@pytest.mark.parametrize("period", [1, 2, 7, 14, 21])
def test_matches_reference_multiple_periods(period: int) -> None:
    _assert_matches_reference(_CLOSES, period)


def _deterministic_series(n: int) -> list[Decimal]:
    # Paseo pseudoaleatorio DETERMINISTA (LCG, semilla fija) -> serie fija y
    # reproducible para exprimir la recursion en profundidad.
    x = 1234567
    price = Decimal(100)
    out = [price]
    for _ in range(n - 1):
        x = (1103515245 * x + 12345) % (2**31)
        step = (Decimal(x % 200) - Decimal(100)) / Decimal(100)
        price = price + step
        if price < Decimal(1):
            price = Decimal(1)
        out.append(price)
    return out


def test_matches_reference_on_long_series() -> None:
    _assert_matches_reference(_deterministic_series(250), 14)


def test_minimal_history_one_value() -> None:
    got = wilder_rsi(_CLOSES[:15], 14)
    assert got[14] is not None
    assert all(v is None for v in got[:14])


def test_flat_market_is_100_by_convention() -> None:
    got = wilder_rsi([Decimal(100)] * 30, 14)
    assert got[14] == Decimal(100)


def test_single_change_then_flat() -> None:
    _assert_matches_reference([Decimal(100)] * 14 + [Decimal(101)] * 16, 14)


def test_result_independent_of_ambient_decimal_context() -> None:
    base = wilder_rsi(_CLOSES, 14)
    with localcontext() as ctx:
        ctx.prec = 6
        hostile = wilder_rsi(_CLOSES, 14)

    def as_str(series: tuple[Decimal | None, ...]) -> list[str | None]:
        return [None if v is None else str(v) for v in series]

    assert as_str(base) == as_str(hostile)


_GOLDEN_RSI14 = {
    14: "68.81720430107526881720430107526882",
    15: "72.34042553191489361702127659574469",
    16: "74.06192114315956602275734321249009",
    17: "67.98611292775807494385003755787837",
    18: "64.42855348732166602056113924523070",
    19: "67.70446132460638681084289378665871",
    20: "70.61846022811278053781725593336840",
    21: "72.40599699294107886444845488489541",
    22: "67.26440941635372646222748321180060",
    23: "62.48593100830400703588754500577243",
    24: "64.79454912262309684368220232110201",
    25: "69.21506497914210586611472068853253",
    26: "71.02349467633482498071487107330137",
    27: "65.49866073679931976156516674426914",
    28: "60.43579818877338871657034015790809",
    29: "62.76073303707907850146408666524653",
    30: "67.84756043053812572328822747534506",
    31: "69.63434893513999223758317612805085",
    32: "64.48841351073813429100601640405799",
    33: "59.73450555030931054789208089648034",
    34: "62.34984790764284209451030999460069",
    35: "66.38115754241762919097344137031349",
    36: "68.49996702231856782855898781916644",
    37: "63.00211049569820686296741556966856",
    38: "58.50697444362693214944313165863094",
    39: "62.47187245339588095670740362546143",
}


def test_golden_lock_rsi14() -> None:
    # Candado bit-exacto: si cambia la formula, la semilla o el contexto
    # Decimal, esto ROMPE y obliga a subir RSI_FORMULA_VERSION conscientemente.
    assert RSI_FORMULA_VERSION == 1
    got = wilder_rsi(_CLOSES, 14)
    for i, expected in _GOLDEN_RSI14.items():
        assert got[i] is not None
        assert str(got[i]) == expected, f"i={i}: {got[i]} != {expected}"
    assert all(got[i] is None for i in range(14))
