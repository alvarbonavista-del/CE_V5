"""Tests del modelo pivotphase.confidence (P08c-P4): inputs inyectados y deterministas.

F6 por DISTANCIA a niveles VP (ELEVACION P08c-PIVOT-05); resto por percentil.
"""

from decimal import Decimal

import pytest

from ce_v5.platform.rules.pivotphase_confidence import (
    ConfidenceInputs,
    ConfidenceParams,
    Factor,
    FactorInput,
    VpContextInput,
    compute_confidence,
    default_params,
)

_DIST = (Decimal(1), Decimal(2), Decimal(3), Decimal(4), Decimal(5))
# F6 en el HVN -> soporte pleno (f6=1): precio en vp.hvn, vp.lvn lejos.
_F6_HVN = VpContextInput(
    price=Decimal(100), hvn_price=Decimal(100), lvn_price=Decimal(50)
)
# F6 en el LVN -> sin soporte (f6=0): precio en vp.lvn, vp.hvn lejos.
_F6_LVN = VpContextInput(
    price=Decimal(100), hvn_price=Decimal(150), lvn_price=Decimal(100)
)


def _fin(raw: object, dist: tuple[Decimal, ...] = _DIST) -> FactorInput:
    return FactorInput(raw=Decimal(str(raw)), distribution=dist)


def test_default_params_active_weights_sum_to_one() -> None:
    total = sum((w for _, w in default_params().weights), Decimal(0))
    assert total == Decimal(1)


def test_default_params_formula_version_is_3() -> None:
    # 2 -> 3 en P08c-CONF-01: F1 pasa de 0 a 1/6 y los demas de 1/5 a 1/6, asi que la
    # MISMA barra da otra confianza. formula_version entra en params_version (PK del
    # snapshot): sin el bump, un replay reinterpretaria snapshots viejos con los pesos
    # nuevos y la serie cambiaria en silencio.
    assert default_params().formula_version == 3


def test_solo_f5_queda_con_peso_cero() -> None:
    # Tras P08c-CONF-01 el unico factor sin peso es F5 (espera celdas de footprint).
    weights = dict(default_params().weights)
    assert weights[Factor.F5_STACKED_IMBALANCE] == Decimal(0)
    sexto = Decimal(1) / Decimal(6)
    for factor in (
        Factor.F1_ABSORPTION,
        Factor.F2_DELTA_EXHAUSTION,
        Factor.F3_CVD_DIVERGENCE,
        Factor.F4_EFFORT_RESULT,
        Factor.F6_VP_CONTEXT,
        Factor.F7_VOID_NOTRADE,
    ):
        assert weights[factor] == sexto


def test_seis_pesos_de_un_sexto_suman_exactamente_uno() -> None:
    # GATE de P08c-CONF-01: 1/6 NO es terminante en Decimal, pero el redondeo al alza
    # del ultimo digito hace que seis sumen 1.000...0, que compara == 1. Si un dia se
    # eligiera un reparto que no cierre, __post_init__ mordera -- y la solucion sera
    # declarar los pesos, no relajar la validacion.
    sexto = Decimal(1) / Decimal(6)
    assert sum((sexto for _ in range(6)), Decimal(0)) == Decimal(1)


def test_all_factors_max_support_gives_100() -> None:
    # Los SEIS con peso a soporte pleno dan 100 exacto: es la prueba de que el reparto
    # 6x(1/6) cierra tambien al multiplicar por la normalizacion, no solo al sumar.
    inputs = ConfidenceInputs(
        f1=_fin(100),
        f2=_fin(100),
        f3=_fin(100),
        f4=_fin(100),
        f6=_F6_HVN,
        f7=_fin(-100),
    )
    r = compute_confidence(inputs, default_params())
    assert r.confidence == Decimal("100.00")
    assert set(r.used_factors) == {
        Factor.F1_ABSORPTION,
        Factor.F2_DELTA_EXHAUSTION,
        Factor.F3_CVD_DIVERGENCE,
        Factor.F4_EFFORT_RESULT,
        Factor.F6_VP_CONTEXT,
        Factor.F7_VOID_NOTRADE,
    }


def test_all_factors_min_support_gives_0() -> None:
    inputs = ConfidenceInputs(
        f2=_fin(-100),
        f3=_fin(-100),
        f4=_fin(-100),
        f6=_F6_LVN,
        f7=_fin(100),
    )
    r = compute_confidence(inputs, default_params())
    assert r.confidence == Decimal("0.00")


def test_f7_penalizes_higher_void_lowers_confidence() -> None:
    low = compute_confidence(
        ConfidenceInputs(f2=_fin(3), f3=_fin(3), f4=_fin(3), f6=_F6_HVN, f7=_fin(1)),
        default_params(),
    )
    high = compute_confidence(
        ConfidenceInputs(f2=_fin(3), f3=_fin(3), f4=_fin(3), f6=_F6_HVN, f7=_fin(5)),
        default_params(),
    )
    assert high.confidence is not None
    assert low.confidence is not None
    assert high.confidence < low.confidence


def test_missing_factor_contributes_zero_and_caps_confidence() -> None:
    # Cuatro de los seis a soporte pleno: 4/6 = 66,67. La evidencia ausente NO se
    # renormaliza -- los presentes no suben de peso para tapar el hueco.
    inputs = ConfidenceInputs(f2=_fin(100), f4=_fin(100), f6=_F6_HVN, f7=_fin(-100))
    r = compute_confidence(inputs, default_params())
    assert r.confidence == Decimal("66.67")
    assert Factor.F1_ABSORPTION not in r.used_factors
    assert Factor.F3_CVD_DIVERGENCE not in r.used_factors
    f3c = next(c for c in r.contributions if c.factor is Factor.F3_CVD_DIVERGENCE)
    assert f3c.evaluable is False
    assert f3c.contribution == Decimal(0)


def test_empty_distribution_is_not_evaluable() -> None:
    inputs = ConfidenceInputs(
        f2=FactorInput(raw=Decimal(3), distribution=()),
        f4=_fin(100),
        f6=_F6_HVN,
        f7=_fin(-100),
    )
    r = compute_confidence(inputs, default_params())
    assert Factor.F2_DELTA_EXHAUSTION not in r.used_factors


def test_all_absent_is_not_evaluable() -> None:
    r = compute_confidence(ConfidenceInputs(), default_params())
    assert r.confidence is None
    assert r.score is None
    assert r.used_factors == ()


def test_f6_distance_hvn_lvn_equidistant() -> None:
    p = default_params()
    # solo F6 evaluable -> confidence = (1/6) * f6 * 100 = 16,67 * f6.
    at_hvn = compute_confidence(ConfidenceInputs(f6=_F6_HVN), p)
    at_lvn = compute_confidence(ConfidenceInputs(f6=_F6_LVN), p)
    equi = VpContextInput(
        price=Decimal(100), hvn_price=Decimal(110), lvn_price=Decimal(90)
    )
    at_equi = compute_confidence(ConfidenceInputs(f6=equi), p)
    assert at_hvn.confidence == Decimal("16.67")  # f6=1
    assert at_lvn.confidence == Decimal("0.00")  # f6=0
    assert at_equi.confidence == Decimal("8.33")  # f6=0.5


def test_f6_degenerate_is_not_evaluable() -> None:
    # price<=0 o ambos niveles en el precio -> F6 no evaluable.
    degenerate = VpContextInput(
        price=Decimal(100), hvn_price=Decimal(100), lvn_price=Decimal(100)
    )
    r = compute_confidence(ConfidenceInputs(f6=degenerate), default_params())
    assert Factor.F6_VP_CONTEXT not in r.used_factors
    assert r.confidence is None


def test_percentile_midrank_equal_to_all() -> None:
    dist = (Decimal(2), Decimal(2), Decimal(2), Decimal(2))
    inputs = ConfidenceInputs(f2=FactorInput(raw=Decimal(2), distribution=dist))
    r = compute_confidence(inputs, default_params())
    assert r.confidence == Decimal("8.33")


def test_deterministic_same_inputs_same_result() -> None:
    inputs = ConfidenceInputs(
        f2=_fin(3), f3=_fin(4), f4=_fin(2), f6=_F6_HVN, f7=_fin(2)
    )
    p = default_params()
    assert compute_confidence(inputs, p) == compute_confidence(inputs, p)


def test_params_reject_weights_not_summing_one() -> None:
    with pytest.raises(ValueError, match="suma de pesos"):
        ConfidenceParams(
            weights=((Factor.F2_DELTA_EXHAUSTION, Decimal("0.5")),),
            formula_version=2,
        )


def test_explainability_breakdown_present() -> None:
    inputs = ConfidenceInputs(f2=_fin(3), f6=_F6_HVN)
    r = compute_confidence(inputs, default_params())
    factors = {c.factor for c in r.contributions}
    assert factors == {
        Factor.F1_ABSORPTION,
        Factor.F2_DELTA_EXHAUSTION,
        Factor.F3_CVD_DIVERGENCE,
        Factor.F4_EFFORT_RESULT,
        Factor.F6_VP_CONTEXT,
        Factor.F7_VOID_NOTRADE,
    }
    assert r.score is not None
    assert r.confidence == (r.score * Decimal(100)).quantize(Decimal("0.01"))


class TestF1Absorcion:
    """F1 activado en P08c-CONF-01: peso 1/6 e input vivo desde absorption.*."""

    def test_f1_con_input_aporta_al_score(self) -> None:
        con = compute_confidence(ConfidenceInputs(f1=_fin(100)), default_params())
        assert con.confidence == Decimal("16.67")
        assert Factor.F1_ABSORPTION in con.used_factors

    def test_f1_ausente_aporta_cero_sin_renormalizar(self) -> None:
        # La convencion de siempre: evidencia ausente NO infla al resto. Con solo F2
        # presente, el resultado es el mismo que tendria si F1 no existiera.
        sin_f1 = compute_confidence(ConfidenceInputs(f2=_fin(100)), default_params())
        assert sin_f1.confidence == Decimal("16.67")
        assert Factor.F1_ABSORPTION not in sin_f1.used_factors
        f1c = next(c for c in sin_f1.contributions if c.factor is Factor.F1_ABSORPTION)
        assert f1c.evaluable is False
        assert f1c.contribution == Decimal(0)

    def test_f1_sube_la_confianza_frente_a_no_tenerlo(self) -> None:
        # Lo que el reweight COMPRA: con la misma evidencia de los demas, tener F1
        # medido aporta soporte adicional en vez de quedarse en 0.
        base = ConfidenceInputs(f2=_fin(100), f4=_fin(100), f6=_F6_HVN)
        con_f1 = ConfidenceInputs(f1=_fin(100), f2=_fin(100), f4=_fin(100), f6=_F6_HVN)
        p = default_params()
        con = compute_confidence(con_f1, p).confidence
        sin = compute_confidence(base, p).confidence
        assert con is not None
        assert sin is not None
        assert con > sin

    def test_f1_mas_absorcion_es_mas_soporte(self) -> None:
        # Orientacion: F1 NO invierte (a diferencia de F7). Mas fuerza de absorcion del
        # lado que sostiene el pivote = mas confianza.
        p = default_params()
        floja = compute_confidence(ConfidenceInputs(f1=_fin(1)), p).confidence
        fuerte = compute_confidence(ConfidenceInputs(f1=_fin(100)), p).confidence
        assert fuerte is not None
        assert floja is not None
        assert fuerte > floja

    def test_techo_efectivo_de_hoy_es_83_33(self) -> None:
        # Tras 3b (F7 vivo): F3 sigue siendo el UNICO con peso y sin input (llega None,
        # aporta 0). El maximo alcanzable con los cinco extractores vivos -- F1, F2, F4,
        # F6, F7 -- es 5/6. Este candado se actualiza cuando F3 entre, y hasta entonces
        # documenta el techo real.
        vivos = ConfidenceInputs(
            f1=_fin(100),
            f2=_fin(100),
            f4=_fin(100),
            f6=_F6_HVN,
            # raw=0 estrictamente POR DEBAJO de la distribucion estandar (1..5) ->
            # rank=0 -> normalized=1 (descending): cero toxicidad = soporte pleno.
            f7=_fin(0),
        )
        assert compute_confidence(vivos, default_params()).confidence == Decimal(
            "83.33"
        )


class TestF7Toxicidad:
    """F7 activado en P08c-CONF-01 paso 3b: peso 1/6, input vivo, y es el UNICO factor
    que PENALIZA (mas toxicidad -> menos confianza, descending=True)."""

    def test_f7_alto_penaliza_mas_que_f7_bajo(self) -> None:
        # Orientacion, la parte que hay que clavar: MAS toxicidad tiene que dar MENOS
        # confianza. Si el extractor no invirtiera, esto saldria al reves.
        p = default_params()
        poca = compute_confidence(ConfidenceInputs(f7=_fin(1)), p).confidence
        mucha = compute_confidence(ConfidenceInputs(f7=_fin(5)), p).confidence
        assert poca is not None
        assert mucha is not None
        assert poca > mucha

    def test_f7_con_input_aporta_al_score(self) -> None:
        con = compute_confidence(ConfidenceInputs(f7=_fin(0)), default_params())
        assert con.confidence == Decimal("16.67")
        assert Factor.F7_VOID_NOTRADE in con.used_factors

    def test_f7_ausente_aporta_cero_sin_renormalizar(self) -> None:
        sin_f7 = compute_confidence(ConfidenceInputs(f2=_fin(100)), default_params())
        assert sin_f7.confidence == Decimal("16.67")
        assert Factor.F7_VOID_NOTRADE not in sin_f7.used_factors
        f7c = next(
            c for c in sin_f7.contributions if c.factor is Factor.F7_VOID_NOTRADE
        )
        assert f7c.evaluable is False
        assert f7c.contribution == Decimal(0)

    def test_f7_de_maxima_toxicidad_aporta_menos_que_f1_de_maximo_soporte(self) -> None:
        # LA PENALIZACION NO ES RESTA, ES UN TECHO MAS BAJO: contribution = weight *
        # normalized, y normalized cae en [0,1] para CUALQUIER factor -- nunca negativo.
        # Anadir F7 (evaluable) nunca puede bajar la confianza por DEBAJO de omitirlo;
        # lo que demuestra la inversion es que, a MAXIMA toxicidad, F7 aporta el
        # MINIMO de su rango (~0), mientras que F1 a maximo soporte aporta el MAXIMO
        # (peso entero). Comparar "con F7 toxico" contra "sin F7" no cazaria la
        # inversion (ambas dan con >= sin siempre); comparar los DOS EXTREMOS si.
        p = default_params()
        f7_toxico_extremo = compute_confidence(
            ConfidenceInputs(f7=_fin(6, dist=(Decimal(1), Decimal(2), Decimal(6)))), p
        ).confidence
        f1_soporte_extremo = compute_confidence(
            ConfidenceInputs(f1=_fin(6, dist=(Decimal(1), Decimal(2), Decimal(6)))), p
        ).confidence
        assert f7_toxico_extremo is not None
        assert f1_soporte_extremo is not None
        assert f7_toxico_extremo < f1_soporte_extremo
