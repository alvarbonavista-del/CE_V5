"""Verificacion del EMA puro (P08b) contra referente determinista INDEPENDIENTE
(aritmetica racional exacta, fractions.Fraction), con el INVARIANTE DE SEMILLA
EMA[0]==src[0] aislado y atado a EMA_FORMULA_VERSION (condicion P08b-08).
Convencion FUNDADA: semilla = primer src, valor desde la barra 0. Fixture fijo.
"""

from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from ce_v5.platform.rules.indicators.ema import (
    EMA_FORMULA_VERSION,
    EMA_SOURCE_ID,
    declarations,
    ema,
    ema_declaration,
)
from source.datasource import MemoryModel, Servibility
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


def _reference_ema(src: list[Decimal], period: int) -> list[Fraction]:
    fr = [Fraction(x) for x in src]
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


def _assert_matches_reference(src: list[Decimal], period: int) -> None:
    got = ema(src, period)
    ref = _reference_ema(src, period)
    assert len(got) == len(ref) == len(src)
    for i, (g, r) in enumerate(zip(got, ref, strict=True)):
        assert abs(Fraction(g) - r) <= _TOL, f"i={i}: fuera de tolerancia"


@pytest.mark.parametrize("period", [1, 2, 9, 12, 26, 200])
def test_matches_reference_multiple_periods(period: int) -> None:
    _assert_matches_reference(_CLOSES, period)


def test_seed_invariant_value_from_bar_zero() -> None:
    # Condicion P08b-08: EMA[0] == src[0], valor desde la barra 0. El tipo de
    # retorno (tuple[Decimal, ...], NO Decimal|None) ya codifica "sin None de
    # warm-up"; aqui se clava el invariante de semilla y la cobertura 1:1.
    assert EMA_FORMULA_VERSION == 1
    for series in ([Decimal("42.5")], _CLOSES[:1], _CLOSES):
        assert ema(series, 12)[0] == series[0]
    assert len(ema(_CLOSES, 12)) == len(_CLOSES)


def test_period_one_follows_source() -> None:
    # alpha = 2/2 = 1 -> el EMA sigue EXACTAMENTE a la fuente.
    assert list(ema(_CLOSES, 1)) == _CLOSES


def test_empty_and_single() -> None:
    assert ema([], 12) == ()
    assert ema([Decimal("7")], 12) == (Decimal("7"),)


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
    _assert_matches_reference(_deterministic_series(300), 26)


def test_result_independent_of_ambient_decimal_context() -> None:
    base = ema(_CLOSES, 12)
    with localcontext() as ctx:
        ctx.prec = 6
        hostile = ema(_CLOSES, 12)
    assert [str(v) for v in base] == [str(v) for v in hostile]


def test_period_must_be_positive() -> None:
    with pytest.raises(ValueError, match="period >= 1"):
        ema(_CLOSES, 0)


_GOLDEN_EMA12 = (
    "100.00",
    "100.0769230769230769230769230769231",
    "100.2497041420118343195266272189349",
    "100.3343650432407828857532999544834",
    "100.5136934981268162879450999614860",
    "100.7885098830303830128766230443343",
    "100.9595083625641702416648348836675",
    "101.2272763067850671275625525938725",
    "101.5615414903565952617836983486613",
    "101.7213043379940421445862062950211",
    "101.7334113629180356608037130188640",
    "101.8051942301614147899108340928849",
    "102.0043951178288894376168596170565",
    "102.3114112535475218318296504452017",
    "102.4942710606940569346250888382476",
    "102.8182293590488174062212290169787",
    "103.1846556115028454975718091682128",
    "103.3716316712716384979453769884878",
    "103.4529191064606171905691651441051",
    "103.6601623208512914689431397373197",
    "103.9739835022587850891057336238859",
    "104.3318321942189719984740822971342",
    "104.5269349335698993833242234821905",
    "104.5843295591745302474281891003150",
    "104.7252019346861409785930830848819",
    "105.0444016370421192895787626102847",
    "105.4068013851894855527204914394717",
    "105.5903704028526416215327235257068",
    "105.6226211101060813720661506755981",
    "105.7422178623974534686713582639676",
    "106.0741843451055375504142262233572",
    "106.4473867535508394657351144966869",
    "106.6400964837737872402374045741197",
    "106.6800816401162815109701115627167",
    "106.8216075416368535862054790146064",
    "107.1259756121542607267892514738977",
    "107.4912101333612975380524435548365",
    "107.6617931897672517629674522387078",
    "107.6830557759569053378955365096758",
    "107.8702779642712275936039155081872",
)


def test_golden_lock_ema12() -> None:
    assert EMA_FORMULA_VERSION == 1
    got = ema(_CLOSES, 12)
    assert len(got) == len(_GOLDEN_EMA12)
    for i, expected in enumerate(_GOLDEN_EMA12):
        assert str(got[i]) == expected, f"i={i}: {got[i]} != {expected}"
    assert str(got[0]) == "100.00"  # invariante de semilla, clavado bit-exacto


class TestDeclaracion:
    """Cara declarativa: ema.value en el catalogo (P08b LOTE 2, OPCION D)."""

    def test_id_y_value_type(self) -> None:
        d = ema_declaration()
        assert d.source_id == EMA_SOURCE_ID == "ema.value"
        assert d.value_type == ScalarType.DECIMAL

    def test_memory_model_recursive(self) -> None:
        assert ema_declaration().memory_model == MemoryModel.RECURSIVE

    def test_non_servible(self) -> None:
        # OPCION D: descubrible pero rechazada como termino hasta que haya
        # materializador.
        assert ema_declaration().servibility == Servibility.NON_SERVIBLE

    def test_consume_market_close(self) -> None:
        # EMA deriva de la serie de cierres (dictamen INT-06-A1).
        assert ema_declaration().consumes == ("market.close",)

    def test_period_en_cache_key(self) -> None:
        d = ema_declaration()
        assert "period" in d.cache_key_schema
        assert {p.name for p in d.params} <= set(d.cache_key_schema)

    def test_period_es_integer_sin_default(self) -> None:
        (period,) = ema_declaration().params
        assert period.name == "period"
        assert period.value_type == ScalarType.INTEGER
        assert period.default is None

    def test_sharing_publico_coherente_con_0023(self) -> None:
        # La 0023 (ema_snapshot) justifica el SIN tenant_id con esto; deben cuadrar.
        d = ema_declaration()
        assert d.shared_evaluation is True
        assert d.sharing_scope.value == "public_cross_tenant"

    def test_declarations_incluye_ema_value(self) -> None:
        assert ema_declaration() in declarations()
