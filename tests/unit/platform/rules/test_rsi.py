"""Verificacion del RSI puro contra un referente determinista INDEPENDIENTE.

Referente = misma definicion Wilder+semilla SMA computada en aritmetica
RACIONAL EXACTA (fractions.Fraction, cero redondeo), por un camino de codigo
distinto del de produccion (Decimal pinneado). Coincidencia a tolerancia
fina tras el warm-up (P08b-01/02, T-04 Q-T04-1). El fixture de cierres es
FIJO y versionado.
"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest

from ce_v5.platform.rules.indicators.rsi import (
    RSI_PERIOD_DEFAULT,
    RSI_SOURCE_ID,
    declarations,
    rsi_seed,
    rsi_step,
    rsi_value_declaration,
    wilder_rsi,
)
from source.datasource import MemoryModel, Servibility
from source.rules.scalar import ScalarType

# Fixture FIJO de cierres (golden). 40 velas con subidas y bajadas.
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
        rs = ag / al
        return Fraction(100) - Fraction(100) / (1 + rs)

    ag = sum(gains[:period], Fraction(0)) / p
    al = sum(losses[:period], Fraction(0)) / p
    out[period] = rsi(ag, al)
    for i in range(period, n - 1):
        ag = (ag * (p - 1) + gains[i]) / p
        al = (al * (p - 1) + losses[i]) / p
        out[i + 1] = rsi(ag, al)
    return out


def test_rsi_matches_exact_reference_within_tolerance() -> None:
    period = 14
    got = wilder_rsi(_CLOSES, period)
    ref = _reference_rsi(_CLOSES, period)
    assert len(got) == len(ref) == len(_CLOSES)
    for i, (g, r) in enumerate(zip(got, ref, strict=True)):
        if r is None:
            assert g is None, f"indice {i}: se esperaba warm-up (None)"
        else:
            assert g is not None, f"indice {i}: valor faltante"
            assert abs(Fraction(g) - r) <= _TOL, f"indice {i}: fuera de tolerancia"


def test_warmup_is_none_until_period() -> None:
    period = 14
    got = wilder_rsi(_CLOSES, period)
    assert all(v is None for v in got[:period])
    assert got[period] is not None


def test_monotone_up_saturates_to_100() -> None:
    closes = [Decimal(100) + Decimal(i) for i in range(30)]
    got = wilder_rsi(closes, 14)
    assert got[-1] == Decimal(100)


def test_monotone_down_saturates_to_0() -> None:
    closes = [Decimal(100) - Decimal(i) for i in range(30)]
    got = wilder_rsi(closes, 14)
    assert got[-1] == Decimal(0)


def test_bit_for_bit_reproducible() -> None:
    a = wilder_rsi(_CLOSES, 14)
    b = wilder_rsi(_CLOSES, 14)
    assert [None if x is None else str(x) for x in a] == [
        None if x is None else str(x) for x in b
    ]


def test_insufficient_history_all_none() -> None:
    got = wilder_rsi(_CLOSES[:10], 14)
    assert all(v is None for v in got)


def _replay(closes: list[Decimal], period: int) -> list[Decimal]:
    """La serie MADURA reconstruida con rsi_seed + rsi_step: lo que hace el replay.

    Devuelve un valor por barra >= period (las de warm-up no existen aqui, igual que en
    el materializador: no hay None a media serie, hay serie mas corta).
    """
    seed = rsi_seed(closes, period)
    if seed is None:
        return []
    avg_gain, avg_loss, last_close, value = seed
    salida = [value]
    for close in closes[period + 1 :]:
        avg_gain, avg_loss, last_close, value = rsi_step(
            avg_gain, avg_loss, last_close, close, period
        )
        salida.append(value)
    return salida


def _maduros(serie: tuple[Decimal | None, ...]) -> list[Decimal]:
    """Los valores no-None de wilder_rsi, en orden: la VERDAD contra la que se mide."""
    return [v for v in serie if v is not None]


class TestSeedYStepReplay:
    """rsi_seed + rsi_step: el replay desde snapshot, bit a bit igual que wilder_rsi.

    Es la pieza que hace ACOTADO el replay del RECURSIVE. Si la aritmetica o el contexto
    Decimal se separasen de los de wilder_rsi, la equivalencia de abajo dejaria de
    cumplirse en el ultimo digito y el GATE de integracion petaria.
    """

    @pytest.mark.parametrize("period", [1, 2, 7, 14, 21])
    def test_equivalencia_bit_exacta_con_wilder_rsi(self, period: int) -> None:
        # LA condicion del dictamen: sembrar en la barra `period` y avanzar paso a paso
        # reconstruye la serie madura ENTERA. Igualdad de Decimal, no tolerancia.
        esperado = _maduros(wilder_rsi(_CLOSES, period))
        obtenido = _replay(_CLOSES, period)
        assert obtenido == esperado
        assert [str(v) for v in obtenido] == [str(v) for v in esperado]

    @pytest.mark.parametrize("period", [7, 14])
    def test_equivalencia_en_serie_monotona(self, period: int) -> None:
        # Saturacion: sin perdidas el RSI es 100 y sin ganancias 0. El replay tiene que
        # reproducir tambien esos bordes, donde _rsi_from_avgs corta por rama.
        for closes in (
            [Decimal(100) + Decimal(i) for i in range(30)],
            [Decimal(100) - Decimal(i) for i in range(30)],
        ):
            assert _replay(closes, period) == _maduros(wilder_rsi(closes, period))

    def test_la_semilla_cae_en_la_barra_period(self) -> None:
        # El primer valor del replay es el de la barra `period` de wilder_rsi, no el de
        # la 0 ni el de la period-1: si la semilla se desplazara, toda la serie iria
        # corrida y el snapshot anclaria en la barra equivocada.
        period = 14
        seed = rsi_seed(_CLOSES, period)
        assert seed is not None
        _, _, last_close, valor = seed
        assert valor == wilder_rsi(_CLOSES, period)[period]
        assert last_close == _CLOSES[period]

    def test_el_step_devuelve_el_cierre_como_nuevo_last_close(self) -> None:
        # last_close entra y sale del estado porque el gain es un DIFERENCIAL. Si el
        # paso no lo avanzara, el replay compararia siempre contra el mismo cierre.
        seed = rsi_seed(_CLOSES, 14)
        assert seed is not None
        avg_gain, avg_loss, last_close, _ = seed
        _, _, nuevo_last_close, _ = rsi_step(
            avg_gain, avg_loss, last_close, _CLOSES[15], 14
        )
        assert nuevo_last_close == _CLOSES[15]

    def test_sin_historia_para_la_semilla_no_hay_estado(self) -> None:
        # len < period+1: el mismo warm-up que wilder_rsi expresa con None. None, no un
        # estado a cero: un estado inventado seria un ancla falsa.
        assert rsi_seed(_CLOSES[:14], 14) is None
        assert rsi_seed([], 14) is None
        assert rsi_seed(_CLOSES[:15], 14) is not None

    def test_period_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="period >= 1"):
            rsi_seed(_CLOSES, 0)
        with pytest.raises(ValueError, match="period >= 1"):
            rsi_step(Decimal(1), Decimal(1), Decimal(100), Decimal(101), 0)


class TestDeclaracion:
    """Cara declarativa: rsi.value en el catalogo vivo (P08b-LOTE3-01)."""

    def test_id_y_value_type(self) -> None:
        d = rsi_value_declaration()
        assert d.source_id == RSI_SOURCE_ID == "rsi.value"
        assert d.value_type == ScalarType.DECIMAL

    def test_continuous(self) -> None:
        # El warm-up NO la hace discontinua: es historia insuficiente al principio, que
        # el materializador expresa con una serie mas corta (NOT_EVALUABLE, K3).
        assert rsi_value_declaration().servibility == Servibility.CONTINUOUS

    def test_memory_model_recursive(self) -> None:
        assert rsi_value_declaration().memory_model == MemoryModel.RECURSIVE

    def test_consume_market_close(self) -> None:
        assert rsi_value_declaration().consumes == ("market.close",)

    def test_period_en_cache_key(self) -> None:
        d = rsi_value_declaration()
        assert "period" in d.cache_key_schema
        assert {p.name for p in d.params} <= set(d.cache_key_schema)

    def test_period_es_integer_con_default_catorce(self) -> None:
        (period,) = rsi_value_declaration().params
        assert period.name == "period"
        assert period.value_type == ScalarType.INTEGER
        assert period.default is not None
        assert period.default.integer_value == RSI_PERIOD_DEFAULT == 14

    def test_period_es_overridable(self) -> None:
        # Sin esto el compilador RECHAZA cualquier override de period y rsi(7) seria
        # inalcanzable pese a estar en la cache_key.
        d = rsi_value_declaration()
        assert d.overridable_params == ("period",)
        assert set(d.overridable_params) <= {p.name for p in d.params}

    def test_sharing_publico_coherente_con_0025(self) -> None:
        # La 0025 (rsi_snapshot) justifica el SIN tenant_id con esto; deben cuadrar.
        d = rsi_value_declaration()
        assert d.shared_evaluation is True
        assert d.sharing_scope.value == "public_cross_tenant"

    def test_declarations_incluye_rsi_value(self) -> None:
        assert rsi_value_declaration() in declarations()
