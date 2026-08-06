"""Tests de la extraccion de senales de pivotphase (P08c P5 T2/T3): inyectados.

|delta| (magnitud): impulse_score y las features son sign-agnosticas en |delta|.
"""

from decimal import Decimal

from ce_v5.platform.rules.indicators.divergence import (
    DivergenceKind,
    divergence_replay,
    divergence_seed,
)
from ce_v5.platform.rules.pivotphase import BEARISH, BULLISH
from ce_v5.platform.rules.pivotphase_signals import (
    cvd_divergence_feature,
    cvd_divergence_magnitudes,
    effort_result_feature,
    exhaustion_feature,
    normalize_impulse_score,
)

_DIST = (Decimal(1), Decimal(2), Decimal(3), Decimal(4), Decimal(5))


# --- impulse_score (T2) ---------------------------------------------------------------
def test_impulse_empty_distribution_is_none() -> None:
    assert normalize_impulse_score(Decimal(3), ()) is None


def test_impulse_above_all_gives_100() -> None:
    assert normalize_impulse_score(Decimal(100), _DIST) == Decimal("100.00")


def test_impulse_below_all_gives_0() -> None:
    assert normalize_impulse_score(Decimal("0.5"), _DIST) == Decimal("0.00")


def test_impulse_sign_agnostic() -> None:
    assert normalize_impulse_score(Decimal(4), _DIST) == normalize_impulse_score(
        Decimal(-4), _DIST
    )


def test_impulse_midrank_middle() -> None:
    assert normalize_impulse_score(Decimal(3), _DIST) == Decimal("50.00")


def test_impulse_threshold_70_as_percentile() -> None:
    dist = tuple(Decimal(i) for i in range(1, 11))
    assert normalize_impulse_score(Decimal(8), dist) == Decimal("75.00")
    assert normalize_impulse_score(Decimal(7), dist) == Decimal("65.00")


# --- F2 exhaustion (T3) ---------------------------------------------------------------
def test_exhaustion_at_peak_is_zero() -> None:
    # |delta| = pico reciente -> sin exhaustion -> 0.
    assert exhaustion_feature(Decimal(5), _DIST) == Decimal(0)


def test_exhaustion_far_below_peak_approaches_one() -> None:
    # |delta|=1, pico=5 -> 1 - 1/5 = 0.8.
    assert exhaustion_feature(Decimal(1), _DIST) == Decimal("0.8")


def test_exhaustion_sign_agnostic() -> None:
    assert exhaustion_feature(Decimal(2), _DIST) == exhaustion_feature(
        Decimal(-2), _DIST
    )


def test_exhaustion_empty_window_is_none() -> None:
    assert exhaustion_feature(Decimal(3), ()) is None


def test_exhaustion_zero_peak_is_none() -> None:
    assert exhaustion_feature(Decimal(0), (Decimal(0), Decimal(0))) is None


def test_exhaustion_clamped_non_negative() -> None:
    # |delta| mayor que el pico de la ventana -> acotado a 0 (no negativo).
    assert exhaustion_feature(Decimal(10), (Decimal(1), Decimal(2))) == Decimal(0)


# --- F4 esfuerzo/resultado (T3) -------------------------------------------------------
def test_effort_result_basic() -> None:
    # |delta|=10, rango=2 -> 5.
    assert effort_result_feature(Decimal(10), Decimal(2)) == Decimal(5)


def test_effort_result_sign_agnostic() -> None:
    assert effort_result_feature(Decimal(-8), Decimal(4)) == effort_result_feature(
        Decimal(8), Decimal(4)
    )


def test_effort_result_zero_range_is_none() -> None:
    assert effort_result_feature(Decimal(10), Decimal(0)) is None


class TestF3DivergenciaPrecioCvd:
    """F3 (P08c-CONF-03): magnitud de la divergencia precio-vs-CVD, orientada.

    Serie construida a mano: el precio hace DOS maximos, el segundo mas alto (100 ->
    110), mientras el CVD hace sus dos maximos a la BAJA (50 -> 38). Eso es una
    divergencia REGULAR_BEAR: el precio sube sin que lo acompane el flujo acumulado.
    Con strength=2 cada pivote necesita 2 barras estrictamente menores a cada lado.
    """

    _CLOSES = [Decimal(x) for x in [90, 95, 100, 95, 90, 92, 105, 110, 105, 100, 98]]
    _CVD = [Decimal(x) for x in [40, 45, 50, 45, 40, 42, 38, 40, 35, 30, 28]]

    def test_detecta_la_divergencia_bajista_y_su_magnitud_relativa(self) -> None:
        # |110 - 100| / 100 = 0.1 -- el salto RELATIVO del precio entre los dos pivotes
        # del par, no el absoluto: asi la escala no depende del precio del simbolo.
        magnitudes = cvd_divergence_magnitudes(self._CLOSES, self._CVD)
        assert magnitudes == {7: {DivergenceKind.REGULAR_BEAR: Decimal("0.1")}}

    def test_la_orientacion_solo_soporta_el_pivote_que_toca(self) -> None:
        # LA TRAMPA (misma que F1): direction es la del IMPULSO. Impulso BULLISH
        # busca un TECHO, y lo confirma la divergencia BAJISTA. El contrario NO.
        magnitudes = cvd_divergence_magnitudes(self._CLOSES, self._CVD)
        en_la_barra = magnitudes[7]
        assert cvd_divergence_feature(BULLISH, en_la_barra) == Decimal("0.1")
        assert cvd_divergence_feature(BEARISH, en_la_barra) == Decimal(0)

    def test_sin_direccion_no_hay_pivote_que_soportar(self) -> None:
        magnitudes = cvd_divergence_magnitudes(self._CLOSES, self._CVD)
        assert cvd_divergence_feature("", magnitudes[7]) == Decimal(0)

    def test_una_barra_sin_divergencia_da_cero_no_none(self) -> None:
        # "No hubo divergencia" es un HECHO, no un hueco: devuelve 0 y ese 0 SI entra en
        # la distribucion, que es lo que hace destacar a una divergencia real.
        assert cvd_divergence_feature(BULLISH, {}) == Decimal(0)

    def test_cvd_que_acompana_al_precio_no_es_divergencia(self) -> None:
        # MUERDE: mismo precio, pero con el CVD haciendo maximos al ALZA (acompana la
        # subida). Sin discrepancia entre precio y flujo no hay divergencia.
        cvd_que_acompana = [
            Decimal(x) for x in [40, 45, 50, 45, 40, 42, 55, 60, 55, 50, 48]
        ]
        assert cvd_divergence_magnitudes(self._CLOSES, cvd_que_acompana) == {}

    def test_es_determinista_bit_a_bit(self) -> None:
        # ADR-007: misma entrada -> misma salida, digito a digito.
        una = cvd_divergence_magnitudes(self._CLOSES, self._CVD)
        otra = cvd_divergence_magnitudes(self._CLOSES, self._CVD)
        assert una == otra
        assert [str(v) for v in una[7].values()] == [str(v) for v in otra[7].values()]

    def test_precio_no_positivo_se_descarta_fail_safe(self) -> None:
        # price_prev <= 0 daria una magnitud sin sentido al dividir. Este caso lo EJERCE
        # de verdad: dos minimos, el primero en 0 y el segundo en 1, con el CVD cayendo
        # -> divergence_replay SI emite un hidden_bull con price_prev=0, y el fail-safe
        # lo descarta. Sin el filtro, aqui habria una ZeroDivisionError.
        closes = [Decimal(x) for x in [5, 3, 0, 3, 5, 4, 2, 1, 2, 4, 5]]
        cvd = [Decimal(x) for x in [9, 8, 7, 8, 9, 8, 7, 5, 6, 7, 8]]

        _, eventos = divergence_replay(closes, closes, cvd, divergence_seed(), 2)

        # (a) el evento con price_prev=0 EXISTE aguas arriba...
        assert any(e.price_prev == Decimal(0) for e in eventos)
        # (b) ...y F3 no lo publica.
        assert cvd_divergence_magnitudes(closes, cvd) == {}
