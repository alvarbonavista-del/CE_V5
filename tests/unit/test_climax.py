"""Tests deterministas del detector y la etiqueta de climax (climax.*, F7, P08c).

Cubren la lista que exigio el CSA (dictamen de apertura) mas el REFERENTE EXACTO
INDEPENDIENTE (recalculo de la fuerza por una via separada, no llamando al detector) y
un GOLDEN atado a CLIMAX_FORMULA_VERSION. Todo en Decimal; sin float en las
comparaciones de fuerza.
"""

from __future__ import annotations

from decimal import Decimal

from ce_v5.platform.rules.climax import (
    CLIMAX_BOTTOM_STRENGTH_SOURCE_ID,
    CLIMAX_FORMULA_VERSION,
    CLIMAX_TOP_STRENGTH_SOURCE_ID,
    MIN_CANDLES,
    ClimaxCandle,
    ClimaxOutput,
    ClimaxSide,
    ClimaxSignal,
    LabelCandle,
    LabelParams,
    climax_output,
    climax_thresholds,
    declarations,
    evaluate_climax,
    label_climax,
)
from source.datasource import MemoryModel, Servibility
from source.rules.scalar import ScalarType

# --------------------------------------------------------------------------- #
# Fixtures y referente independiente
# --------------------------------------------------------------------------- #

# Ventana previa de 19 velas: volumen y rango = 1..19 (ambos crecientes). Con estos
# valores, el percentil por interpolacion lineal da umbrales cerrados y verificables a
# mano: P95(vol) = 18.1 ; P90(rango) = 17.2 (ver test_percentiles_deterministas).
_PRIOR = [
    ClimaxCandle(
        volume=Decimal(i),
        high=Decimal(100 + i),
        low=Decimal(100),
        close=Decimal(100),
    )
    for i in range(1, 20)
]


def _ref_percentile(ordered: list[Decimal], pct: Decimal) -> Decimal:
    """Referente independiente del percentil (transcripcion aparte)."""
    n = len(ordered)
    if pct <= 0:
        return ordered[0]
    if pct >= 100:
        return ordered[-1]
    position = Decimal(n - 1) * pct / Decimal(100)
    low_index = int(position)
    high_index = min(low_index + 1, n - 1)
    if low_index == high_index:
        return ordered[low_index]
    weight_high = position - Decimal(low_index)
    weight_low = Decimal(high_index) - position
    return ordered[low_index] * weight_low + ordered[high_index] * weight_high


def _ref_strength(current: ClimaxCandle) -> Decimal:
    """Referente independiente de la fuerza de un climax de TECHO sobre _PRIOR.

    Recalcula umbrales y fuerza sin llamar a evaluate_climax/_strength: umbrales por
    _ref_percentile, normalizacion topada al 200% y dividida entre 2 (rev 3, H3),
    rej_score de techo, pesos 0.40/0.30/0.30.
    """
    volumes = sorted(candle.volume for candle in _PRIOR)
    ranges = sorted(span for candle in _PRIOR if (span := candle.high - candle.low) > 0)
    vol_threshold = _ref_percentile(volumes, Decimal("95"))
    range_threshold = _ref_percentile(ranges, Decimal("90"))
    price_range = current.high - current.low
    pos_close = (current.close - current.low) / price_range
    cap = Decimal("2")
    vol_excess = min(current.volume / vol_threshold - Decimal(1), cap) / cap
    range_excess = min(price_range / range_threshold - Decimal(1), cap) / cap
    rejection = (Decimal("0.33") - pos_close) / Decimal("0.33")
    rej_score = min(max(rejection, Decimal(0)), Decimal(1))
    return (
        Decimal("0.40") * vol_excess
        + Decimal("0.30") * range_excess
        + Decimal("0.30") * rej_score
    )


# --------------------------------------------------------------------------- #
# Deteccion
# --------------------------------------------------------------------------- #


def test_menos_de_min_candles_no_evalua() -> None:
    window = _PRIOR[: MIN_CANDLES - 1]
    signal = evaluate_climax(window)
    assert signal.detected is False
    assert signal.side is None
    assert signal.strength == Decimal(0)


def test_high_igual_low_no_divide_por_cero() -> None:
    # Vela actual con rango nulo (high == low): sin division, sin senal.
    flat = ClimaxCandle(
        volume=Decimal(40), high=Decimal(100), low=Decimal(100), close=Decimal(100)
    )
    signal = evaluate_climax([*_PRIOR, flat])
    assert signal.detected is False
    assert signal.side is None


def test_percentiles_deterministas() -> None:
    # Mismo input -> mismos umbrales, y coinciden con el referente independiente.
    first = climax_thresholds(_PRIOR)
    second = climax_thresholds(_PRIOR)
    assert first == second
    assert first is not None
    vol_threshold, range_threshold = first
    volumes = sorted(candle.volume for candle in _PRIOR)
    ranges = sorted(span for candle in _PRIOR if (span := candle.high - candle.low) > 0)
    assert vol_threshold == _ref_percentile(volumes, Decimal("95"))
    assert range_threshold == _ref_percentile(ranges, Decimal("90"))
    assert vol_threshold == Decimal("18.1")
    assert range_threshold == Decimal("17.2")


def test_techo_por_pos_close() -> None:
    # close en el tercio inferior (pos_close = 0.10) -> climax de TECHO.
    current = ClimaxCandle(
        volume=Decimal(40), high=Decimal(140), low=Decimal(100), close=Decimal(104)
    )
    signal = evaluate_climax([*_PRIOR, current])
    assert signal.detected is True
    assert signal.side is ClimaxSide.TOP


def test_suelo_por_pos_close() -> None:
    # close en el tercio superior (pos_close = 0.90) -> climax de SUELO.
    current = ClimaxCandle(
        volume=Decimal(40), high=Decimal(140), low=Decimal(100), close=Decimal(136)
    )
    signal = evaluate_climax([*_PRIOR, current])
    assert signal.detected is True
    assert signal.side is ClimaxSide.BOTTOM


def test_direccion_ignora_open_rev3_h2() -> None:
    # Sin campo open: la clasificacion depende SOLO de pos_close. Una vela que cierra
    # muy abajo (pos_close 0.05) es TECHO aunque "parezca" bajista. Documenta H2.
    current = ClimaxCandle(
        volume=Decimal(40), high=Decimal(140), low=Decimal(100), close=Decimal(102)
    )
    signal = evaluate_climax([*_PRIOR, current])
    assert signal.side is ClimaxSide.TOP


def test_and_falla_por_volumen() -> None:
    # Rango y rechazo cumplen, pero volumen <= umbral (18.1) -> sin senal.
    current = ClimaxCandle(
        volume=Decimal(10), high=Decimal(140), low=Decimal(100), close=Decimal(104)
    )
    signal = evaluate_climax([*_PRIOR, current])
    assert signal.detected is False
    assert signal.side is None


def test_and_falla_por_rango() -> None:
    # Volumen y rechazo cumplen, pero rango <= umbral (17.2) -> sin senal.
    current = ClimaxCandle(
        volume=Decimal(40), high=Decimal(110), low=Decimal(100), close=Decimal(101)
    )
    signal = evaluate_climax([*_PRIOR, current])
    assert signal.detected is False
    assert signal.side is None


def test_and_falla_por_rechazo() -> None:
    # Volumen y rango cumplen, pero close en el centro (pos_close 0.5) -> sin senal.
    current = ClimaxCandle(
        volume=Decimal(40), high=Decimal(140), low=Decimal(100), close=Decimal(120)
    )
    signal = evaluate_climax([*_PRIOR, current])
    assert signal.detected is False
    assert signal.side is None


def test_volumen_igual_umbral_no_emite() -> None:
    # Frontera exacta: volumen == umbral (18.1). El AHP firmado usa ">" estricto -> NO
    # emite en la igualdad. (v4 usa ">="; divergencia de frontera notificada a Central.)
    current = ClimaxCandle(
        volume=Decimal("18.1"), high=Decimal(140), low=Decimal(100), close=Decimal(104)
    )
    signal = evaluate_climax([*_PRIOR, current])
    assert signal.detected is False
    assert signal.side is None


def test_strength_bajo_minimo_no_detecta_pero_reporta_lado() -> None:
    # Candidato estructural con fuerza < STRENGTH_MIN: detected False, side presente.
    # Volumen y rango apenas por encima del umbral, rechazo debil -> fuerza baja.
    current = ClimaxCandle(
        volume=Decimal("19"),
        high=Decimal("117.3"),
        low=Decimal(100),
        close=Decimal("105.5"),
    )
    signal = evaluate_climax([*_PRIOR, current])
    assert signal.side is ClimaxSide.TOP
    assert signal.detected is False
    assert signal.strength < Decimal("0.30")


def test_referente_independiente_de_la_fuerza() -> None:
    # La fuerza del detector coincide EXACTAMENTE con el referente independiente.
    current = ClimaxCandle(
        volume=Decimal(40), high=Decimal(140), low=Decimal(100), close=Decimal(104)
    )
    signal = evaluate_climax([*_PRIOR, current])
    assert signal.strength == _ref_strength(current)


def test_golden_fuerza_atado_a_formula_version() -> None:
    # GOLDEN [CLIMAX_FORMULA_VERSION = climax.v1]: si la formula cambia, sube la version
    # y se regenera este numero. 8 decimales (presentacion; la fuente no redondea).
    assert CLIMAX_FORMULA_VERSION == "climax.v1"
    current = ClimaxCandle(
        volume=Decimal(40), high=Decimal(140), low=Decimal(100), close=Decimal(104)
    )
    signal = evaluate_climax([*_PRIOR, current])
    assert signal.strength.quantize(Decimal("0.00000001")) == Decimal("0.64991707")


# --------------------------------------------------------------------------- #
# Etiqueta
# --------------------------------------------------------------------------- #

_TOP_HIGH = Decimal(140)
_TOP_LOW = Decimal(100)


def _top(
    delta: Decimal, high: Decimal = _TOP_HIGH, low: Decimal = _TOP_LOW
) -> LabelCandle:
    return LabelCandle(delta=delta, high=high, low=low)


def test_etiqueta_flip_dos_velas_confirma() -> None:
    # Delta se invierte (negativo) dos velas consecutivas dentro de R -> 1 (gira).
    subsequent = [_top(Decimal(-5)), _top(Decimal(-3)), _top(Decimal(1))]
    label = label_climax(
        side=ClimaxSide.TOP,
        climax_high=_TOP_HIGH,
        climax_low=_TOP_LOW,
        subsequent=subsequent,
    )
    assert label == 1


def test_etiqueta_invalidacion_antes_del_flip() -> None:
    # La primera vela rompe el high por > 0.003 (140 * 1.003 = 140.42) -> 0 (continua).
    subsequent = [_top(Decimal(-5), high=Decimal("140.5")), _top(Decimal(-3))]
    label = label_climax(
        side=ClimaxSide.TOP,
        climax_high=_TOP_HIGH,
        climax_low=_TOP_LOW,
        subsequent=subsequent,
    )
    assert label == 0


def test_etiqueta_sin_flip_en_r_timeout() -> None:
    # Delta nunca se invierte en R=5 velas -> 0 (timeout).
    subsequent = [_top(Decimal(2)) for _ in range(5)]
    label = label_climax(
        side=ClimaxSide.TOP,
        climax_high=_TOP_HIGH,
        climax_low=_TOP_LOW,
        subsequent=subsequent,
    )
    assert label == 0


def test_etiqueta_flip_no_consecutivo_no_confirma() -> None:
    # Flips alternos (nunca dos seguidos) en R=5 -> 0. El contador se resetea.
    subsequent = [
        _top(Decimal(-5)),
        _top(Decimal(1)),
        _top(Decimal(-5)),
        _top(Decimal(1)),
        _top(Decimal(-5)),
    ]
    label = label_climax(
        side=ClimaxSide.TOP,
        climax_high=_TOP_HIGH,
        climax_low=_TOP_LOW,
        subsequent=subsequent,
    )
    assert label == 0


def test_etiqueta_flip_fuera_de_r_no_cuenta() -> None:
    # El flip de dos velas llega en las posiciones 6-7, fuera de R=5 -> 0.
    subsequent = [
        _top(Decimal(2)),
        _top(Decimal(2)),
        _top(Decimal(2)),
        _top(Decimal(2)),
        _top(Decimal(2)),
        _top(Decimal(-5)),
        _top(Decimal(-5)),
    ]
    label = label_climax(
        side=ClimaxSide.TOP,
        climax_high=_TOP_HIGH,
        climax_low=_TOP_LOW,
        subsequent=subsequent,
    )
    assert label == 0


def test_etiqueta_suelo_simetrica() -> None:
    # SUELO: flip = delta > 0 sostenido; invalidacion = low por debajo de low*0.997.
    subsequent = [
        LabelCandle(delta=Decimal(5), high=Decimal(140), low=Decimal(100)),
        LabelCandle(delta=Decimal(3), high=Decimal(140), low=Decimal(100)),
    ]
    label = label_climax(
        side=ClimaxSide.BOTTOM,
        climax_high=_TOP_HIGH,
        climax_low=_TOP_LOW,
        subsequent=subsequent,
    )
    assert label == 1


def test_etiqueta_suelo_invalidacion() -> None:
    # SUELO: la primera vela rompe el low por debajo de 100 * 0.997 = 99.7 -> 0.
    subsequent = [
        LabelCandle(delta=Decimal(5), high=Decimal(140), low=Decimal("99.6")),
        LabelCandle(delta=Decimal(3), high=Decimal(140), low=Decimal(100)),
    ]
    label = label_climax(
        side=ClimaxSide.BOTTOM,
        climax_high=_TOP_HIGH,
        climax_low=_TOP_LOW,
        subsequent=subsequent,
    )
    assert label == 0


def test_etiqueta_r_bars_configurable() -> None:
    # Con r_bars=2, un flip de 2 velas en posiciones 3-4 queda fuera del horizonte -> 0.
    subsequent = [
        _top(Decimal(2)),
        _top(Decimal(2)),
        _top(Decimal(-5)),
        _top(Decimal(-5)),
    ]
    label = label_climax(
        side=ClimaxSide.TOP,
        climax_high=_TOP_HIGH,
        climax_low=_TOP_LOW,
        subsequent=subsequent,
        params=LabelParams(r_bars=2),
    )
    assert label == 0


class TestProyeccionServible:
    """La cara SERVIBLE (P08c-DET-01): del triplete del veredicto a dos escalares."""

    def _signal(
        self, *, detected: bool, side: ClimaxSide | None, strength: str
    ) -> ClimaxSignal:
        return ClimaxSignal(detected=detected, side=side, strength=Decimal(strength))

    def test_top_publica_la_fuerza_cuando_el_climax_es_top(self) -> None:
        senal = self._signal(detected=True, side=ClimaxSide.TOP, strength="0.71")
        assert climax_output(senal, ClimaxOutput.TOP_STRENGTH) == Decimal("0.71")

    def test_bottom_publica_la_fuerza_cuando_el_climax_es_bottom(self) -> None:
        senal = self._signal(detected=True, side=ClimaxSide.BOTTOM, strength="0.55")
        assert climax_output(senal, ClimaxOutput.BOTTOM_STRENGTH) == Decimal("0.55")

    def test_el_lado_contrario_publica_cero(self) -> None:
        senal = self._signal(detected=True, side=ClimaxSide.TOP, strength="0.71")
        assert climax_output(senal, ClimaxOutput.BOTTOM_STRENGTH) == Decimal(0)

    def test_sin_deteccion_publica_cero_aunque_haya_fuerza(self) -> None:
        senal = self._signal(detected=False, side=ClimaxSide.TOP, strength="0.29")
        assert climax_output(senal, ClimaxOutput.TOP_STRENGTH) == Decimal(0)

    def test_sin_lado_publica_cero_en_ambas(self) -> None:
        senal = self._signal(detected=False, side=None, strength="0")
        assert climax_output(senal, ClimaxOutput.TOP_STRENGTH) == Decimal(0)
        assert climax_output(senal, ClimaxOutput.BOTTOM_STRENGTH) == Decimal(0)


class TestDeclaracionesServibles:
    def test_son_dos_con_los_source_id_esperados(self) -> None:
        assert {d.source_id for d in declarations()} == {
            CLIMAX_TOP_STRENGTH_SOURCE_ID,
            CLIMAX_BOTTOM_STRENGTH_SOURCE_ID,
        }

    def test_las_dos_son_continuous_windowed_decimal(self) -> None:
        for declaration in declarations():
            assert declaration.servibility is Servibility.CONTINUOUS
            assert declaration.memory_model is MemoryModel.WINDOWED
            assert declaration.value_type is ScalarType.DECIMAL

    def test_los_umbrales_son_params_default_only_en_la_cache_key(self) -> None:
        for declaration in declarations():
            nombres = {p.name for p in declaration.params}
            assert nombres == {
                "vol_pct",
                "range_pct",
                "close_rejection",
                "excess_cap",
                "strength_min",
            }
            assert declaration.overridable_params == ()
            assert nombres <= set(declaration.cache_key_schema)

    def test_consumes_no_incluye_candle_open(self) -> None:
        # ENMIENDA P08c-DET-01 (P1). ClimaxCandle no tiene campo open: la direccion sale
        # SOLO de la posicion del cierre en el rango (rev 3, H2). Declarar candle.open
        # seria una arista MUERTA del DAG -- decir que se usa algo que no se lee.
        for declaration in declarations():
            assert set(declaration.consumes) == {
                "market.footprint",
                "candle.high",
                "candle.low",
                "market.close",
            }
            assert "candle.open" not in declaration.consumes
