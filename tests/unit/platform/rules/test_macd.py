"""Verificacion del MACD puro (P08b) contra referente determinista INDEPENDIENTE
(fractions.Fraction) para las TRES series (macd, signal, histogram), con el
INVARIANTE DE SEMILLA macd[0]==signal[0]==histogram[0]==0 atado a
MACD_FORMULA_VERSION. Histograma x1 (TradingView). Fixture fijo.
"""

from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from ce_v5.platform.rules.indicators.macd import (
    MACD_FAST_DEFAULT,
    MACD_FORMULA_VERSION,
    MACD_HISTOGRAM_SOURCE_ID,
    MACD_LINE_SOURCE_ID,
    MACD_SIGNAL_DEFAULT,
    MACD_SIGNAL_SOURCE_ID,
    MACD_SLOW_DEFAULT,
    MacdOutput,
    declarations,
    macd,
    macd_histogram_declaration,
    macd_line_declaration,
    macd_seed,
    macd_signal_declaration,
    macd_step,
    select_output,
)
from source.datasource import DataSourceDeclaration, MemoryModel, Servibility
from source.rules.scalar import ScalarType

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


def _replay(
    closes: list[Decimal], fast: int, slow: int, signal: int
) -> tuple[list[Decimal], list[Decimal], list[Decimal]]:
    """Las tres series reconstruidas con macd_seed + macd_step: el replay en frio."""
    state = macd_seed(closes[0])
    line = [state[3]]
    signal_out = [state[4]]
    histogram = [state[5]]
    for close in closes[1:]:
        state = macd_step(state[0], state[1], state[2], close, fast, slow, signal)
        line.append(state[3])
        signal_out.append(state[4])
        histogram.append(state[5])
    return (line, signal_out, histogram)


class TestSeedYStepReplay:
    """macd_seed + macd_step: el replay desde snapshot, bit a bit igual que macd().

    Es la pieza que hace ACOTADO el replay del RECURSIVE. macd_step NO reescribe la
    recurrencia: llama a ema_from_anchor, que ES la de ema(). Si alguien las separase,
    la equivalencia de abajo dejaria de cumplirse en el ultimo digito y el GATE de
    integracion petaria.
    """

    @pytest.mark.parametrize(
        ("fast", "slow", "signal"), [(12, 26, 9), (5, 35, 5), (1, 2, 3), (3, 3, 3)]
    )
    def test_equivalencia_bit_exacta_con_macd_puro(
        self, fast: int, slow: int, signal: int
    ) -> None:
        # LA condicion del dictamen, para las TRES salidas a la vez: sembrar en el
        # primer cierre y avanzar paso a paso reconstruye las tres series ENTERAS.
        # Igualdad de Decimal Y de representacion textual: el exponente tambien tiene
        # que coincidir
        # (en Decimal, 0 y 0.00 son iguales pero se propagan distinto).
        esperado = macd(_CLOSES, fast, slow, signal)
        line, signal_out, histogram = _replay(_CLOSES, fast, slow, signal)
        assert tuple(line) == esperado.macd
        assert tuple(signal_out) == esperado.signal
        assert tuple(histogram) == esperado.histogram
        assert [str(v) for v in line] == [str(v) for v in esperado.macd]
        assert [str(v) for v in signal_out] == [str(v) for v in esperado.signal]
        assert [str(v) for v in histogram] == [str(v) for v in esperado.histogram]

    def test_equivalencia_en_serie_larga(self) -> None:
        serie = [Decimal(100) + Decimal(i % 7) - Decimal(i % 3) for i in range(200)]
        esperado = macd(serie)
        line, signal_out, histogram = _replay(
            serie, MACD_FAST_DEFAULT, MACD_SLOW_DEFAULT, MACD_SIGNAL_DEFAULT
        )
        assert tuple(line) == esperado.macd
        assert tuple(signal_out) == esperado.signal
        assert tuple(histogram) == esperado.histogram

    def test_la_semilla_respeta_el_invariante_y_su_exponente(self) -> None:
        # macd[0] == signal[0] == histogram[0] == 0, con el MISMO exponente que produce
        # macd(): los ceros se CALCULAN (close - close), no se escriben como Decimal(0).
        # Si se hardcodearan, 5.5 + 0 daria 5.5 donde macd() da 5.50 y la serie se
        # apartaria del golden a partir de ahi.
        state = macd_seed(_CLOSES[0])
        esperado = macd(_CLOSES)
        assert str(state[3]) == str(esperado.macd[0]) == "0.00"
        assert str(state[4]) == str(esperado.signal[0])
        assert str(state[5]) == str(esperado.histogram[0])
        # El estado interno arranca con las dos EMAs del precio en el propio cierre.
        assert state[0] == state[1] == _CLOSES[0]

    def test_el_step_encadena_la_signal_sobre_la_line_recien_calculada(self) -> None:
        # La EMA de la senal se alimenta de la line de ESTA barra, no de la anterior. Si
        # se desplazara, las tres series seguirian pareciendo razonables pero ninguna
        # cuadraria con macd(). Se comprueba en la barra 1, la primera con movimiento.
        state = macd_step(*macd_seed(_CLOSES[0])[:3], _CLOSES[1])
        esperado = macd(_CLOSES)
        assert state[3] == esperado.macd[1]
        assert state[4] == esperado.signal[1]
        assert state[5] == esperado.histogram[1]

    def test_select_output_proyecta_cada_salida(self) -> None:
        # Las tres fuentes comparten estado y paso; lo unico que las distingue es esto.
        state = macd_step(*macd_seed(_CLOSES[0])[:3], _CLOSES[1])
        assert select_output(state, MacdOutput.LINE) == state[3]
        assert select_output(state, MacdOutput.SIGNAL) == state[4]
        assert select_output(state, MacdOutput.HISTOGRAM) == state[5]
        # Y no son el mismo numero en esta barra: si select_output cruzara las
        # proyecciones, los tests de arriba no lo verian.
        assert len({state[3], state[4], state[5]}) > 1

    @pytest.mark.parametrize(
        ("fast", "slow", "signal"), [(0, 26, 9), (12, 0, 9), (12, 26, 0), (-1, 26, 9)]
    )
    def test_periodos_deben_ser_positivos(
        self, fast: int, slow: int, signal: int
    ) -> None:
        # Mismo dominio y mismo mensaje que macd(): el paso no admite lo que la funcion
        # pura rechaza.
        with pytest.raises(ValueError, match=">= 1"):
            macd_step(
                Decimal(1), Decimal(1), Decimal(0), Decimal(100), fast, slow, signal
            )


class TestDeclaraciones:
    """Cara declarativa: las tres fuentes macd.* en el catalogo vivo (P08b-LOTE3-01)."""

    def _todas(self) -> tuple[DataSourceDeclaration, ...]:
        return (
            macd_line_declaration(),
            macd_signal_declaration(),
            macd_histogram_declaration(),
        )

    def test_los_tres_source_id(self) -> None:
        assert macd_line_declaration().source_id == MACD_LINE_SOURCE_ID == "macd.line"
        assert (
            macd_signal_declaration().source_id
            == MACD_SIGNAL_SOURCE_ID
            == "macd.signal"
        )
        assert (
            macd_histogram_declaration().source_id
            == MACD_HISTOGRAM_SOURCE_ID
            == "macd.histogram"
        )

    @pytest.mark.parametrize(
        "d",
        [
            macd_line_declaration(),
            macd_signal_declaration(),
            macd_histogram_declaration(),
        ],
    )
    def test_forma_comun_de_las_tres(self, d: DataSourceDeclaration) -> None:
        # CONTINUOUS: hay materializador y defaults reales, y ademas el MACD da valor
        # desde la barra 0 (sin el warm-up que si tiene el RSI).
        assert d.servibility == Servibility.CONTINUOUS
        assert d.memory_model == MemoryModel.RECURSIVE
        assert d.value_type == ScalarType.DECIMAL
        assert d.consumes == ("market.close",)
        assert d.shared_evaluation is True
        assert d.sharing_scope.value == "public_cross_tenant"

    @pytest.mark.parametrize(
        "d",
        [
            macd_line_declaration(),
            macd_signal_declaration(),
            macd_histogram_declaration(),
        ],
    )
    def test_los_tres_params_en_cache_key_y_overridables(
        self, d: DataSourceDeclaration
    ) -> None:
        assert d.overridable_params == ("fast", "slow", "signal")
        for nombre in ("fast", "slow", "signal"):
            assert nombre in d.cache_key_schema
        assert {p.name for p in d.params} <= set(d.cache_key_schema)

    @pytest.mark.parametrize(
        "d",
        [
            macd_line_declaration(),
            macd_signal_declaration(),
            macd_histogram_declaration(),
        ],
    )
    def test_defaults_doce_veintiseis_nueve(self, d: DataSourceDeclaration) -> None:
        defaults = {p.name: p.default for p in d.params}
        esperados = {
            "fast": MACD_FAST_DEFAULT,
            "slow": MACD_SLOW_DEFAULT,
            "signal": MACD_SIGNAL_DEFAULT,
        }
        assert esperados == {"fast": 12, "slow": 26, "signal": 9}
        for nombre, valor in esperados.items():
            default = defaults[nombre]
            assert default is not None
            assert default.integer_value == valor
            assert default.scalar_type == ScalarType.INTEGER

    def test_los_defaults_declarados_son_los_de_la_funcion_pura(self) -> None:
        # Si divergieran, una regla sin overrides recibiria una serie distinta de la que
        # da macd(closes) por defecto. Comparten constante, y esto lo vigila.
        assert (
            macd(_CLOSES).macd
            == macd(
                _CLOSES, MACD_FAST_DEFAULT, MACD_SLOW_DEFAULT, MACD_SIGNAL_DEFAULT
            ).macd
        )

    def test_declarations_publica_las_tres(self) -> None:
        publicadas = declarations()
        assert len(publicadas) == 3
        for declaracion in self._todas():
            assert declaracion in publicadas
