"""Tests deterministas del detector y la etiqueta de void (void.*, F7, P08c).

Cubren la logica de deteccion (cruce, snap, FAR, timeout, prioridad 3.4 del CSA), la
etiqueta (target, invalidacion, prioridad, timeout), un REFERENTE INDEPENDIENTE del snap
(transcripcion aparte, no llama al detector) y un GOLDEN atado a VOID_FORMULA_VERSION.
Todo en Decimal; solo comparaciones/multiplicaciones (sin division).
"""

from __future__ import annotations

from decimal import Decimal

from ce_v5.platform.rules.void import (
    VOID_FORMULA_VERSION,
    VOID_SNAP_BEARISH_SOURCE_ID,
    VOID_SNAP_BULLISH_SOURCE_ID,
    VoidLabelParams,
    VoidOutput,
    VoidParams,
    VoidSignal,
    VoidSnap,
    VoidSnapDirection,
    declarations,
    evaluate_void,
    label_void,
    scan_void_snaps,
    void_output,
)
from source.datasource import MemoryModel, Servibility
from source.rules.scalar import ScalarType

_LVN = Decimal(100)
# Con LVN=100 y semillas por defecto: return_up=100.05, return_down=99.95,
# far si |close-100| > 0.5 (close>100.5 o close<99.5).


def _ref_first_snap(
    closes: list[Decimal], lvn: Decimal
) -> tuple[int, VoidSnapDirection] | None:
    """Referente INDEPENDIENTE (asume <=1 cruce): halla el cruce y decide el snap.

    Transcripcion separada de la logica, para cruzar contra scan_void_snaps en fixtures
    de un solo cruce. Aplica la prioridad 3.4 (FAR gana al retorno en la misma vela).
    """
    tol = Decimal("0.0005")
    far = Decimal("0.005")
    cross_i: int | None = None
    is_up = False
    for i in range(1, len(closes)):
        if closes[i - 1] < lvn <= closes[i]:
            cross_i, is_up = i, True
            break
        if closes[i - 1] > lvn >= closes[i]:
            cross_i, is_up = i, False
            break
    if cross_i is None:
        return None
    for i in range(cross_i + 1, len(closes)):
        elapsed = i - cross_i
        c = closes[i]
        if abs(c - lvn) > lvn * far:
            return None  # invalidado / breakout real (prioridad)
        returned = c <= lvn * (1 + tol) if is_up else c >= lvn * (1 - tol)
        if returned and elapsed <= 5:
            side = VoidSnapDirection.BEARISH if is_up else VoidSnapDirection.BULLISH
            return (i, side)
        if elapsed > 5:
            return None
    return None


# --------------------------------------------------------------------------- #
# Deteccion
# --------------------------------------------------------------------------- #


def test_cruce_up_retorno_snap_bearish() -> None:
    closes = [Decimal("99.8"), Decimal("100.3"), Decimal("100.0")]
    signal = evaluate_void(closes, _LVN)
    assert signal.detected is True
    assert signal.direction is VoidSnapDirection.BEARISH


def test_cruce_down_retorno_snap_bullish() -> None:
    closes = [Decimal("100.2"), Decimal("99.7"), Decimal("100.0")]
    signal = evaluate_void(closes, _LVN)
    assert signal.detected is True
    assert signal.direction is VoidSnapDirection.BULLISH


def test_far_descarta_breakout_real() -> None:
    # Cruce up y el precio sigue subiendo mas de 0.5% -> breakout real, sin snap.
    closes = [Decimal("99.8"), Decimal("100.3"), Decimal("100.8")]
    assert scan_void_snaps(closes, _LVN) == []
    assert evaluate_void(closes, _LVN).detected is False


def test_snap_en_frontera_elapsed_igual_r() -> None:
    # Cruce en i=1; el retorno llega en i=6 (elapsed=5=R) -> snap permitido.
    closes = [
        Decimal("99.8"),
        Decimal("100.3"),
        Decimal("100.3"),
        Decimal("100.3"),
        Decimal("100.3"),
        Decimal("100.3"),
        Decimal("100.0"),
    ]
    snaps = scan_void_snaps(closes, _LVN)
    assert snaps == [VoidSnap(index=6, direction=VoidSnapDirection.BEARISH)]


def test_timeout_elapsed_mayor_que_r_no_snap() -> None:
    # Mismo caso pero el retorno llega en i=7 (elapsed=6>R) -> timeout, sin snap.
    closes = [
        Decimal("99.8"),
        Decimal("100.3"),
        Decimal("100.3"),
        Decimal("100.3"),
        Decimal("100.3"),
        Decimal("100.3"),
        Decimal("100.3"),
        Decimal("100.0"),
    ]
    assert scan_void_snaps(closes, _LVN) == []


def test_prioridad_far_sobre_retorno_3_4() -> None:
    # Misma vela: close=99.4 cumple retorno (<=100.05) Y far (|99.4-100|=0.6>0.5).
    # Fail-safe CSA (3.4): INVALIDADO, no snap. (v4 sin la regla habria dado snap.)
    closes = [Decimal("99.8"), Decimal("100.3"), Decimal("99.4")]
    assert scan_void_snaps(closes, _LVN) == []
    assert evaluate_void(closes, _LVN).detected is False


def test_sin_cruce_no_snap() -> None:
    closes = [Decimal("101"), Decimal("102"), Decimal("103")]
    assert scan_void_snaps(closes, _LVN) == []


def test_lvn_no_positivo_o_ventana_corta() -> None:
    assert scan_void_snaps([Decimal("100")], _LVN) == []
    assert scan_void_snaps([Decimal("99.8"), Decimal("100.3")], Decimal(0)) == []


def test_referente_independiente_deteccion() -> None:
    # scan coincide con el referente independiente en fixtures de un solo cruce.
    casos = [
        [Decimal("99.8"), Decimal("100.3"), Decimal("100.0")],  # snap bearish
        [Decimal("100.2"), Decimal("99.7"), Decimal("100.0")],  # snap bullish
        [Decimal("99.8"), Decimal("100.3"), Decimal("100.8")],  # far, sin snap
        [Decimal("99.8"), Decimal("100.3"), Decimal("99.4")],  # prioridad far, sin snap
    ]
    for closes in casos:
        snaps = scan_void_snaps(closes, _LVN)
        ref = _ref_first_snap(closes, _LVN)
        if ref is None:
            assert snaps == []
        else:
            assert snaps == [VoidSnap(index=ref[0], direction=ref[1])]


def test_golden_atado_a_formula_version() -> None:
    # GOLDEN [VOID_FORMULA_VERSION = void.v1]: si la logica cambia, sube la version y se
    # regenera. Ventana de cruce-up + retorno -> un snap bearish en la vela 2.
    assert VOID_FORMULA_VERSION == "void.v1"
    closes = [Decimal("99.8"), Decimal("100.3"), Decimal("100.0")]
    assert scan_void_snaps(closes, _LVN) == [
        VoidSnap(index=2, direction=VoidSnapDirection.BEARISH)
    ]


def test_parametros_configurables() -> None:
    # Cruce up en i=1; vela POSTERIOR a 100.3 (i=2); retorno a 100.0 (i=3).
    # Por defecto (far=0.5%) la vela i=2 sobrevive (|0.3|<0.5) y hay snap en i=3.
    # Con far estrecho (0.1%) la vela i=2 dispara FAR (|0.3|>0.1) y descarta: sin snap.
    closes = [Decimal("99.8"), Decimal("100.2"), Decimal("100.3"), Decimal("100.0")]
    assert scan_void_snaps(closes, _LVN) == [
        VoidSnap(index=3, direction=VoidSnapDirection.BEARISH)
    ]
    tight = VoidParams(far_threshold=Decimal("0.001"))
    assert scan_void_snaps(closes, _LVN, tight) == []


# --------------------------------------------------------------------------- #
# Etiqueta
# --------------------------------------------------------------------------- #
# LVN=100, move_target=invalidation=0.005: target/invalid bearish en 99.5 / 100.5;
# bullish en 100.5 / 99.5.


def test_etiqueta_bearish_confirma() -> None:
    label = label_void(
        direction=VoidSnapDirection.BEARISH,
        lvn=_LVN,
        subsequent=[Decimal("100.0"), Decimal("99.4")],
    )
    assert label == 1


def test_etiqueta_bearish_invalida_antes_que_target() -> None:
    label = label_void(
        direction=VoidSnapDirection.BEARISH,
        lvn=_LVN,
        subsequent=[Decimal("100.6"), Decimal("99.4")],
    )
    assert label == 0


def test_etiqueta_bearish_timeout() -> None:
    label = label_void(
        direction=VoidSnapDirection.BEARISH,
        lvn=_LVN,
        subsequent=[Decimal("100.0")] * 5,
    )
    assert label == 0


def test_etiqueta_bullish_confirma() -> None:
    label = label_void(
        direction=VoidSnapDirection.BULLISH,
        lvn=_LVN,
        subsequent=[Decimal("100.0"), Decimal("100.6")],
    )
    assert label == 1


def test_etiqueta_bullish_invalida() -> None:
    label = label_void(
        direction=VoidSnapDirection.BULLISH,
        lvn=_LVN,
        subsequent=[Decimal("99.4"), Decimal("100.6")],
    )
    assert label == 0


def test_etiqueta_respeta_horizonte_r() -> None:
    # El target llega en la vela 6 (fuera de R=5) -> timeout, etiqueta 0.
    subsequent = [Decimal("100.0")] * 5 + [Decimal("99.4")]
    label = label_void(
        direction=VoidSnapDirection.BEARISH,
        lvn=_LVN,
        subsequent=subsequent,
    )
    assert label == 0


def test_etiqueta_r_configurable() -> None:
    # Con r_bars=1, un target en la vela 2 queda fuera del horizonte -> 0.
    label = label_void(
        direction=VoidSnapDirection.BEARISH,
        lvn=_LVN,
        subsequent=[Decimal("100.0"), Decimal("99.4")],
        params=VoidLabelParams(r_bars=1),
    )
    assert label == 0


class TestProyeccionServible:
    """La cara SERVIBLE (P08c-DET-01): del par (detected, direction) a dos
    indicadoras."""

    def test_bullish_publica_uno_cuando_el_snap_es_bullish(self) -> None:
        senal = VoidSignal(detected=True, direction=VoidSnapDirection.BULLISH)
        assert void_output(senal, VoidOutput.SNAP_BULLISH) == Decimal(1)

    def test_bearish_publica_uno_cuando_el_snap_es_bearish(self) -> None:
        senal = VoidSignal(detected=True, direction=VoidSnapDirection.BEARISH)
        assert void_output(senal, VoidOutput.SNAP_BEARISH) == Decimal(1)

    def test_la_direccion_contraria_publica_cero(self) -> None:
        senal = VoidSignal(detected=True, direction=VoidSnapDirection.BULLISH)
        assert void_output(senal, VoidOutput.SNAP_BEARISH) == Decimal(0)

    def test_sin_snap_publica_cero_en_ambas(self) -> None:
        senal = VoidSignal(detected=False, direction=None)
        assert void_output(senal, VoidOutput.SNAP_BULLISH) == Decimal(0)
        assert void_output(senal, VoidOutput.SNAP_BEARISH) == Decimal(0)

    def test_solo_emite_cero_o_uno(self) -> None:
        # Es una INDICADORA: no hay fuerza que graduar hoy. El value_type es DECIMAL
        # para que una calibracion futura pueda graduarla sin cambiar el contrato.
        for detected in (True, False):
            for direction in (VoidSnapDirection.BULLISH, VoidSnapDirection.BEARISH):
                senal = VoidSignal(detected=detected, direction=direction)
                for output in VoidOutput:
                    assert void_output(senal, output) in {Decimal(0), Decimal(1)}


class TestDeclaracionesServibles:
    def test_son_dos_con_los_source_id_esperados(self) -> None:
        assert {d.source_id for d in declarations()} == {
            VOID_SNAP_BULLISH_SOURCE_ID,
            VOID_SNAP_BEARISH_SOURCE_ID,
        }

    def test_las_dos_son_continuous_windowed_decimal(self) -> None:
        for declaration in declarations():
            assert declaration.servibility is Servibility.CONTINUOUS
            assert declaration.memory_model is MemoryModel.WINDOWED
            assert declaration.value_type is ScalarType.DECIMAL

    def test_los_umbrales_son_params_default_only_en_la_cache_key(self) -> None:
        for declaration in declarations():
            nombres = {p.name for p in declaration.params}
            assert nombres == {"r_bars", "return_tolerance", "far_threshold"}
            assert declaration.overridable_params == ()
            assert nombres <= set(declaration.cache_key_schema)

    def test_no_consume_vp_lvn(self) -> None:
        # vp.lvn es NON_SERVIBLE (un CONJUNTO de niveles): no se puede pedir por
        # dispatch. El nivel se computa DENTRO del materializador con select_lvn_price,
        # como MACD calcula sus EMAs sin consumir ema.value.
        for declaration in declarations():
            assert set(declaration.consumes) == {"market.footprint", "market.close"}
            assert "vp.lvn" not in declaration.consumes
