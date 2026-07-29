"""Tests deterministas del detector de absorcion (absorption.*, F1, P08c).

NO es calibracion (esa se difiere sobre corpus): aqui se verifica que el detector
DISPARA BIEN DADO un umbral, con las semillas [PARIDAD v4] parametrizadas. Cubre el
umbral adaptativo, las cuatro condiciones estructurales, el lado (bid/ask), la fuerza y
el corte por strength_min, mas la parametrizacion (calibracion-ready).
"""

from __future__ import annotations

from decimal import Decimal

from ce_v5.platform.rules.absorption import (
    AbsorptionParams,
    AbsorptionSide,
    adaptive_threshold,
    detect_absorption,
)


class TestUmbralAdaptativo:
    def test_menos_de_dos_muestras_da_el_piso(self) -> None:
        assert adaptive_threshold([]) == Decimal("2.0")
        assert adaptive_threshold([Decimal("9")]) == Decimal("2.0")

    def test_percentil_80_por_encima_del_piso(self) -> None:
        # 10 valores 1..10 ordenados; int(10*0.80)=8 -> ordered[8]=9. max(9, 2.0)=9.
        ratios = [Decimal(v) for v in range(1, 11)]
        assert adaptive_threshold(ratios) == Decimal("9")

    def test_el_piso_gana_cuando_el_percentil_es_bajo(self) -> None:
        # Todos por debajo del piso: el umbral es el piso 2.0.
        ratios = [Decimal("0.5"), Decimal("1.0"), Decimal("1.5")]
        assert adaptive_threshold(ratios) == Decimal("2.0")


class TestDeteccion:
    def test_absorcion_en_bid_suelo(self) -> None:
        # Agresion vendedora fuerte (delta<0) sin caida (displacement>0 chico).
        # V=100, R=10 -> ratio=10 > 2. |delta|=60 > 10. delta*disp<0. |2|<3.
        # abs_norm=min(1,(10-2)/2)=1 ; delta_norm=0.6 ; move_norm=1-2/10=0.8.
        # strength=0.40*1 + 0.35*0.6 + 0.25*0.8 = 0.81.
        signal = detect_absorption(
            volume=Decimal("100"),
            delta=Decimal("-60"),
            price_range=Decimal("10"),
            displacement=Decimal("2"),
            threshold=Decimal("2"),
        )
        assert signal.detected
        assert signal.side is AbsorptionSide.BID
        assert signal.strength == Decimal("0.81")

    def test_absorcion_en_ask_techo(self) -> None:
        # Agresion COMPRADORA fuerte (delta>0) sin avance de precio (displacement<0).
        signal = detect_absorption(
            volume=Decimal("100"),
            delta=Decimal("60"),
            price_range=Decimal("10"),
            displacement=Decimal("-2"),
            threshold=Decimal("2"),
        )
        assert signal.detected
        assert signal.side is AbsorptionSide.ASK
        assert signal.strength == Decimal("0.81")

    def test_precio_se_mueve_demasiado_no_es_absorcion(self) -> None:
        # |displacement|=5 > 0.30*10=3 -> falla la contencion.
        signal = detect_absorption(
            volume=Decimal("100"),
            delta=Decimal("-60"),
            price_range=Decimal("10"),
            displacement=Decimal("5"),
            threshold=Decimal("2"),
        )
        assert not signal.detected

    def test_agresion_debil_no_es_absorcion(self) -> None:
        # |delta|=5 < 0.10*100=10 -> falla la agresion minima.
        signal = detect_absorption(
            volume=Decimal("100"),
            delta=Decimal("-5"),
            price_range=Decimal("10"),
            displacement=Decimal("2"),
            threshold=Decimal("2"),
        )
        assert not signal.detected

    def test_delta_y_precio_en_la_misma_direccion_no_es_absorcion(self) -> None:
        # delta<0 y displacement<0 -> delta*disp>0: no hay oposicion.
        signal = detect_absorption(
            volume=Decimal("100"),
            delta=Decimal("-60"),
            price_range=Decimal("10"),
            displacement=Decimal("-2"),
            threshold=Decimal("2"),
        )
        assert not signal.detected

    def test_ratio_bajo_el_umbral_no_es_absorcion(self) -> None:
        # ratio=10 no supera un umbral de 20.
        signal = detect_absorption(
            volume=Decimal("100"),
            delta=Decimal("-60"),
            price_range=Decimal("10"),
            displacement=Decimal("2"),
            threshold=Decimal("20"),
        )
        assert not signal.detected

    def test_frontera_exacta_del_umbral_no_emite(self) -> None:
        # A == threshold: la condicion es A > U (estricta) -> en la frontera NO emite.
        # V=100, R=10 -> ratio=10 ; threshold=10 -> 10 > 10 es False. Las otras 3
        # condiciones si se cumplirian; aisla la frontera del ratio.
        signal = detect_absorption(
            volume=Decimal("100"),
            delta=Decimal("-60"),
            price_range=Decimal("10"),
            displacement=Decimal("2"),
            threshold=Decimal("10"),
        )
        assert not signal.detected

    def test_rango_o_volumen_no_positivos_no_es_absorcion(self) -> None:
        assert not detect_absorption(
            volume=Decimal("100"),
            price_range=Decimal("0"),
            delta=Decimal("-60"),
            displacement=Decimal("2"),
            threshold=Decimal("2"),
        ).detected
        assert not detect_absorption(
            volume=Decimal("0"),
            price_range=Decimal("10"),
            delta=Decimal("-60"),
            displacement=Decimal("2"),
            threshold=Decimal("2"),
        ).detected


class TestFuerzaYParametrizacion:
    def test_condiciones_ok_pero_fuerza_bajo_el_minimo_no_emite(self) -> None:
        # ratio apenas sobre el umbral, delta y move justos: strength < 0.30.
        signal = detect_absorption(
            volume=Decimal("100"),
            delta=Decimal("11"),
            price_range=Decimal("10"),
            displacement=Decimal("-2.9"),
            threshold=Decimal("9.5"),
        )
        assert not signal.detected  # cae por fuerza, no por estructura
        assert signal.side is AbsorptionSide.ASK  # el lado candidato SI se calcula
        assert signal.strength < Decimal("0.30")

    def test_bajar_strength_min_por_parametro_hace_que_emita(self) -> None:
        # Mismos inputs debiles, pero con un strength_min calibrado mas bajo -> emite.
        params = AbsorptionParams(strength_min=Decimal("0.20"))
        signal = detect_absorption(
            volume=Decimal("100"),
            delta=Decimal("11"),
            price_range=Decimal("10"),
            displacement=Decimal("-2.9"),
            threshold=Decimal("9.5"),
            params=params,
        )
        assert signal.detected
        assert signal.side is AbsorptionSide.ASK
