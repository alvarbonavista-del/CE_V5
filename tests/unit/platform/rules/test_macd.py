"""Verificacion del MACD puro (P08b) contra referente determinista INDEPENDIENTE
(fractions.Fraction) para las TRES series (macd, signal, histogram), con el
INVARIANTE DE SEMILLA macd[0]==signal[0]==histogram[0]==0 atado a
MACD_FORMULA_VERSION. Histograma x1 (TradingView). Fixture fijo.
"""

from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from ce_v5.platform.rules.indicators.macd import MACD_FORMULA_VERSION, macd

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


def _ref_ema(fr: list[Fraction], period: int) -> list[Fraction]:
    n = len(fr)
    if n == 0:
        return []
    alpha = Fraction(2, period + 1)
    one_minus = 1 - alpha
    out: list[Fraction] = [fr[0]]
    prev = fr[0]
    for i in range(1, n):
        prev = alpha * fr[i] + one_minus * prev
        out.append(prev)
    return out


def _ref_macd(
    closes: list[Decimal], fast: int, slow: int, sig: int
) -> tuple[list[Fraction], list[Fraction], list[Fraction]]:
    fr = [Fraction(c) for c in closes]
    ema_fast = _ref_ema(fr, fast)
    ema_slow = _ref_ema(fr, slow)
    macd_line = [a - b for a, b in zip(ema_fast, ema_slow, strict=True)]
    signal = _ref_ema(macd_line, sig)
    hist = [m - g for m, g in zip(macd_line, signal, strict=True)]
    return macd_line, signal, hist


def _assert_matches(closes: list[Decimal], fast: int, slow: int, sig: int) -> None:
    got = macd(closes, fast, slow, sig)
    ref_macd, ref_signal, ref_hist = _ref_macd(closes, fast, slow, sig)
    for got_series, ref_series in (
        (got.macd, ref_macd),
        (got.signal, ref_signal),
        (got.histogram, ref_hist),
    ):
        assert len(got_series) == len(ref_series) == len(closes)
        for i, (g, r) in enumerate(zip(got_series, ref_series, strict=True)):
            assert abs(Fraction(g) - r) <= _TOL, f"i={i}: fuera de tolerancia"


@pytest.mark.parametrize(("fast", "slow", "sig"), [(12, 26, 9), (5, 13, 4)])
def test_matches_reference(fast: int, slow: int, sig: int) -> None:
    _assert_matches(_CLOSES, fast, slow, sig)


def test_seed_invariant_first_bar_is_zero() -> None:
    # Ambas EMAs siembran en close[0] -> macd[0]=0 -> signal[0]=0 -> hist[0]=0.
    assert MACD_FORMULA_VERSION == 1
    got = macd(_CLOSES)
    assert got.macd[0] == 0
    assert got.signal[0] == 0
    assert got.histogram[0] == 0
    assert len(got.macd) == len(got.signal) == len(got.histogram) == len(_CLOSES)


def _deterministic_series(n: int) -> list[Decimal]:
    x = 1234567
    price = Decimal(100)
    out = [price]
    for _ in range(n - 1):
        x = (1103515245 * x + 12345) % (2**31)
        price = price + (Decimal(x % 200) - Decimal(100)) / Decimal(100)
        if price < Decimal(1):
            price = Decimal(1)
        out.append(price)
    return out


def test_matches_reference_on_long_series() -> None:
    _assert_matches(_deterministic_series(300), 12, 26, 9)


def test_result_independent_of_ambient_decimal_context() -> None:
    base = macd(_CLOSES)
    with localcontext() as ctx:
        ctx.prec = 6
        hostile = macd(_CLOSES)

    assert [str(v) for v in base.macd] == [str(v) for v in hostile.macd]
    assert [str(v) for v in base.signal] == [str(v) for v in hostile.signal]
    assert [str(v) for v in base.histogram] == [str(v) for v in hostile.histogram]


def test_periods_must_be_positive() -> None:
    with pytest.raises(ValueError, match="fast >= 1"):
        macd(_CLOSES, 0, 26, 9)
    with pytest.raises(ValueError, match="slow >= 1"):
        macd(_CLOSES, 12, 0, 9)
    with pytest.raises(ValueError, match="signal_period >= 1"):
        macd(_CLOSES, 12, 26, 0)


def test_empty_and_single() -> None:
    empty = macd([])
    assert empty.macd == () and empty.signal == () and empty.histogram == ()
    one = macd([Decimal("7")])
    assert one.macd[0] == 0 and one.signal[0] == 0 and one.histogram[0] == 0


_GOLDEN_HIST = (
    "0.00",
    "0.03190883190883190883190883190888",
    "0.094835593866932898271929610960944",
    "0.1034894889182435549753996844523552",
    "0.1476361076744756244157206857980442",
    "0.2177599131652617115907218043930753",
    "0.2234121948100056635188198681995802",
    "0.2644306673828391795477420564445442",
    "0.3184812697987874991766954283578754",
    "0.2814143578705556983255711991919003",
    "0.1885384617185728834328035415924002",
    "0.1417027626907963431785519176430402",
    "0.1577652346581304345100806804446722",
    "0.2120494614602191905731293614950178",
    "0.1976166418843005898752543468022542",
    "0.2425277549680054743441527900281234",
    "0.2898185788790681721548948433814587",
    "0.2461166117899938635775002146026870",
    "0.1662574050569789019561511440255096",
    "0.1568876876634937081484713748379277",
    "0.1918330138728742736528992323139422",
    "0.233139733755415993642967790366994",
    "0.192773949863747894670384833965675",
    "0.102894093132733740237904115030540",
    "0.069419871913652181954112325380192",
    "0.117568527006608658085050004424714",
    "0.169412460485128329326993922258251",
    "0.131123923594505659572094561765881",
    "0.038050390859724977214707883187905",
    "0.004775757281404186028423593784964",
    "0.068151238992078139191562146742451",
    "0.131191665172577677845635247356681",
    "0.101073660041118171355817029552785",
    "0.013914223222350888372186593891348",
    "-0.008733853802457059946024004697802",
    "0.042168527449001332258284651338958",
    "0.104497581588757614583927578103886",
    "0.068374827677183452864654743174229",
    "-0.021336071423739654371821998321257",
    "-0.018629997707172138010396260621086",
)


def test_golden_lock_histogram() -> None:
    assert MACD_FORMULA_VERSION == 1
    got = macd(_CLOSES)
    assert len(got.histogram) == len(_GOLDEN_HIST)
    for i, expected in enumerate(_GOLDEN_HIST):
        assert str(got.histogram[i]) == expected, (
            f"i={i}: {got.histogram[i]} != {expected}"
        )
    assert str(got.macd[0]) == "0.00"  # invariante de semilla, clavado
