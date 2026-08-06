"""Tests del nucleo PURO de la FSM de pivote 0-5 (pivotphase, P08c P3).

Senales INYECTADAS a mano (BarSignals construidos en el test), sin BD, sin IO, sin
catalogo vivo: el nucleo se prueba en aislamiento total. Cubre las dos secuencias
COMPLETAS (bullish y bearish, 0->1->2->3->4->5 CONFIRMED con vuelta a IDLE), las CUATRO
invalidaciones con gatillo vivo, la paridad de los 11 params, la mordida (mutar un
umbral rompe la confirmacion), el determinismo, el gate estructural de absorcion y el
NOT_EVALUABLE de impulse_score.

PHASE3_ZONE_BREAK YA TIENE GATILLO VIVO (P08c-CONF-04): la cuarta invalidacion se cubre
con secuencia real en las dos direcciones, con la orientacion probada por su lado malo y
con la mordida del 11o param. Aqui ya no queda ningun test saltado.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from ce_v5.platform.rules.pivotphase import (
    BEARISH,
    BULLISH,
    PIVOTPHASE_CONFIDENCE_SOURCE_ID,
    PIVOTPHASE_PHASE_SOURCE_ID,
    AbsorptionZone,
    BarSignals,
    Invalidation,
    Phase,
    PivotEvent,
    PivotParams,
    PivotState,
    VpTouch,
    declarations,
    evaluate_bar,
)
from source.datasource import MemoryModel, Servibility, SourceType
from source.rules.scalar import ScalarType

_D = Decimal
_PARAMS = PivotParams()
_LEVEL = _D("100")


def _run(
    bars: list[BarSignals],
    params: PivotParams = _PARAMS,
    state: PivotState | None = None,
) -> tuple[PivotState, list[PivotEvent]]:
    """Pasa la secuencia de barras por la FSM y devuelve (estado_final, eventos)."""
    current = state if state is not None else PivotState()
    events: list[PivotEvent] = []
    for bar in bars:
        current, event = evaluate_bar(current, bar, params)
        events.append(event)
    return current, events


# --- Secuencias COMPLETAS -------------------------------------------------------


def _bullish_sequence() -> list[BarSignals]:
    """0->1->2->3->4->5: impulso alcista, encuentro en VAH, absorcion bajista, flip."""
    return [
        # Fase 1: dos velas de impulso alcista con score >= 70.
        BarSignals(price=_D("99"), delta=_D("100"), impulse_score=_D("80")),
        BarSignals(price=_D("99.5"), delta=_D("120"), impulse_score=_D("85")),
        # Fase 2: toque contra-direccional del nivel VP (precio pegado al VAH).
        BarSignals(
            price=_LEVEL,
            delta=_D("10"),
            vp_touch=VpTouch(level_type="vah", level_price=_LEVEL),
        ),
        # Fase 3: zona de absorcion BAJISTA pegada al nivel (contra el impulso).
        BarSignals(
            price=_D("100.1"),
            delta=_D("10"),
            absorption=AbsorptionZone(
                zone_price=_LEVEL, zone_type="bearish", zone_strength=_D("0.8")
            ),
        ),
        # Fase 4: tres velas con |delta| < 0.5 * pico (pico = 120 -> umbral 60).
        BarSignals(price=_D("100.1"), delta=_D("10")),
        BarSignals(price=_D("100.1"), delta=_D("8")),
        BarSignals(price=_D("100.1"), delta=_D("5")),
        # Fase 5: dos velas con delta invertido (flip bajista).
        BarSignals(price=_D("99.9"), delta=_D("-30")),
        BarSignals(price=_D("99.7"), delta=_D("-40")),
    ]


def _bearish_sequence() -> list[BarSignals]:
    """Simetrica: impulso bajista, encuentro en VAL, absorcion alcista, flip alcista."""
    return [
        BarSignals(price=_D("101"), delta=_D("-100"), impulse_score=_D("80")),
        BarSignals(price=_D("100.5"), delta=_D("-120"), impulse_score=_D("85")),
        BarSignals(
            price=_LEVEL,
            delta=_D("-10"),
            vp_touch=VpTouch(level_type="val", level_price=_LEVEL),
        ),
        BarSignals(
            price=_D("99.9"),
            delta=_D("-10"),
            absorption=AbsorptionZone(
                zone_price=_LEVEL, zone_type="bullish", zone_strength=_D("0.8")
            ),
        ),
        BarSignals(price=_D("99.9"), delta=_D("-10")),
        BarSignals(price=_D("99.9"), delta=_D("-8")),
        BarSignals(price=_D("99.9"), delta=_D("-5")),
        BarSignals(price=_D("100.1"), delta=_D("30")),
        BarSignals(price=_D("100.3"), delta=_D("40")),
    ]


def test_secuencia_completa_bullish_confirma_y_vuelve_a_idle() -> None:
    final, events = _run(_bullish_sequence())
    kinds = [(e.kind, e.phase) for e in events]
    assert kinds == [
        ("none", int(Phase.IDLE)),
        ("advance", int(Phase.IMPULSE)),
        ("advance", int(Phase.ENCOUNTER)),
        ("advance", int(Phase.ABSORPTION)),
        ("none", int(Phase.IDLE)),
        ("none", int(Phase.IDLE)),
        ("advance", int(Phase.EXHAUSTION)),
        ("none", int(Phase.IDLE)),
        ("confirmed", int(Phase.FLIP)),
    ]
    assert events[-1].direction == BULLISH
    # Tras confirmar, la FSM vuelve a IDLE limpia (lista para la siguiente secuencia).
    assert final == PivotState()
    assert final.phase == int(Phase.IDLE)


def test_secuencia_completa_bearish_confirma_y_vuelve_a_idle() -> None:
    final, events = _run(_bearish_sequence())
    kinds = [(e.kind, e.phase) for e in events]
    assert kinds == [
        ("none", int(Phase.IDLE)),
        ("advance", int(Phase.IMPULSE)),
        ("advance", int(Phase.ENCOUNTER)),
        ("advance", int(Phase.ABSORPTION)),
        ("none", int(Phase.IDLE)),
        ("none", int(Phase.IDLE)),
        ("advance", int(Phase.EXHAUSTION)),
        ("none", int(Phase.IDLE)),
        ("confirmed", int(Phase.FLIP)),
    ]
    assert events[-1].direction == BEARISH
    assert final == PivotState()


def test_estado_intermedio_recuerda_lo_de_cada_fase() -> None:
    """El snapshot lleva la memoria de secuencia (pico, nivel, zona) para el replay."""
    bars = _bullish_sequence()
    state, _ = _run(bars[:4])
    assert state.phase == int(Phase.ABSORPTION)
    assert state.direction == BULLISH
    assert state.phase1_peak_delta == _D("120")
    assert state.phase2_level_price == _LEVEL
    assert state.phase2_level_type == "vah"
    assert state.phase3_zone_price == _LEVEL
    assert state.phase3_zone_strength == _D("0.8")


# --- Invalidaciones -------------------------------------------------------------


def test_invalidacion_phase2_price_break_vuelve_a_idle() -> None:
    """En fase 2 el precio rompe el nivel por encima del umbral (0.3%) -> invalida."""
    bars = [*_bullish_sequence()[:3], BarSignals(price=_D("101"), delta=_D("10"))]
    final, events = _run(bars)
    assert events[-1] == PivotEvent(
        "invalidated",
        int(Phase.ENCOUNTER),
        BULLISH,
        Invalidation.PHASE2_PRICE_BREAK,
    )
    assert final == PivotState()


def test_invalidacion_phase2_price_break_bearish() -> None:
    """Simetrico: en bajista rompe por debajo de level * (1 - 0.003) = 99.7."""
    bars = [*_bearish_sequence()[:3], BarSignals(price=_D("99"), delta=_D("-10"))]
    final, events = _run(bars)
    assert events[-1].reason == Invalidation.PHASE2_PRICE_BREAK
    assert events[-1].direction == BEARISH
    assert final == PivotState()


def test_invalidacion_phase4_delta_regrowth_vuelve_a_idle() -> None:
    """En fase 4 el delta re-crece en la direccion original (> pico * drop * 2)."""
    # 7 barras dejan la FSM en EXHAUSTION; luego delta = 150 > 120 * 0.5 * 2 = 120.
    bars = [*_bullish_sequence()[:7], BarSignals(price=_D("100.1"), delta=_D("150"))]
    final, events = _run(bars)
    assert events[-1] == PivotEvent(
        "invalidated",
        int(Phase.EXHAUSTION),
        BULLISH,
        Invalidation.PHASE4_DELTA_REGROWTH,
    )
    assert final == PivotState()


def test_invalidacion_phase5_timeout_vuelve_a_idle() -> None:
    """Sin flip suficiente en phase5_timeout_candles (5) velas -> timeout a la 6a."""
    quiet = BarSignals(price=_D("100.1"), delta=_D("10"))  # ni flip ni re-crecimiento
    bars = [*_bullish_sequence()[:7], *([quiet] * 6)]
    final, events = _run(bars)
    # Las cinco primeras velas de fase 5 no producen evento; la sexta invalida.
    assert [e.kind for e in events[7:]] == [
        "none",
        "none",
        "none",
        "none",
        "none",
        "invalidated",
    ]
    assert events[-1].reason == Invalidation.PHASE5_TIMEOUT
    assert final == PivotState()


def test_invalidacion_phase3_zone_break() -> None:
    """En fase 3 el precio ATRAVIESA la zona siguiendo el impulso (> 0.3%) -> invalida.

    Con la zona en 100 y umbral 0.003, el corte esta en 100.3: 100.4 la deja atras. El
    muro que justificaba la fase 3 no existia, asi que la secuencia muere.
    """
    bars = [*_bullish_sequence()[:4], BarSignals(price=_D("100.4"), delta=_D("10"))]
    final, events = _run(bars)
    assert events[-1] == PivotEvent(
        "invalidated",
        int(Phase.ABSORPTION),
        BULLISH,
        Invalidation.PHASE3_ZONE_BREAK,
    )
    assert final == PivotState()


def test_invalidacion_phase3_zone_break_bearish() -> None:
    """Simetrico: en bajista la zona se rompe por DEBAJO de 100 * (1 - 0.003) = 99.7."""
    bars = [*_bearish_sequence()[:4], BarSignals(price=_D("99.6"), delta=_D("-10"))]
    final, events = _run(bars)
    assert events[-1].reason == Invalidation.PHASE3_ZONE_BREAK
    assert events[-1].direction == BEARISH
    assert final == PivotState()


def test_phase3_zone_break_solo_rompe_EN_EL_SENTIDO_DEL_IMPULSO() -> None:
    """LA ORIENTACION, y aqui es donde muerde de verdad.

    state.direction es la del IMPULSO. Con impulso BULLISH se espera un TECHO, asi que
    el precio ALEJANDOSE HACIA ABAJO de la zona es el pivote FUNCIONANDO, no rompiendose
    -- por lejos que caiga. Si la desigualdad estuviera invertida, este caso invalidaria
    justo la secuencia que va bien, y las dos pruebas de arriba seguirian en verde.
    """
    lejos_a_la_baja = BarSignals(price=_D("90"), delta=_D("10"))
    state, events = _run([*_bullish_sequence()[:4], lejos_a_la_baja])
    assert events[-1].reason != Invalidation.PHASE3_ZONE_BREAK
    assert state.phase == int(Phase.ABSORPTION)
    # Y en bajista, el espejo: subir MUY por encima de la zona no rompe un SUELO.
    lejos_al_alza = BarSignals(price=_D("110"), delta=_D("-10"))
    state_b, events_b = _run([*_bearish_sequence()[:4], lejos_al_alza])
    assert events_b[-1].reason != Invalidation.PHASE3_ZONE_BREAK
    assert state_b.phase == int(Phase.ABSORPTION)


def test_una_secuencia_sana_no_emite_phase3_zone_break() -> None:
    """Sin falsos positivos: el camino completo 0->5 nunca dispara la invalidacion."""
    assert Invalidation.PHASE3_ZONE_BREAK == "phase3_zone_break"
    _, events = _run(_bullish_sequence())
    assert events[-1].kind == "confirmed"
    assert all(e.reason != Invalidation.PHASE3_ZONE_BREAK for e in events)


def test_param_phase3_break_threshold_gobierna_la_rotura_de_zona() -> None:
    """MUERDE con el 11o param: con 0.003 el precio 100.2 aguanta; con 0.001 rompe."""
    bars = [*_bullish_sequence()[:4], BarSignals(price=_D("100.2"), delta=_D("10"))]
    state, events = _run(bars)
    assert events[-1].kind == "none"
    assert state.phase == int(Phase.ABSORPTION)
    strict_state, strict = _run(
        bars, replace(_PARAMS, phase3_break_threshold=_D("0.001"))
    )
    assert strict[-1].reason == Invalidation.PHASE3_ZONE_BREAK
    assert strict_state == PivotState()


# --- Paridad de los parametros --------------------------------------------------


def test_semillas_de_paridad_v4() -> None:
    """Los 11 params tienen exactamente sus semillas, en Decimal.

    Diez son [PARIDAD v4]; phase3_break_threshold es el 11o (P08c-CONF-04) y es [A
    CALIBRAR AHP] -- no hay semilla v4 documentada para la rotura de zona de fase 3 --,
    igualada de arranque a phase2_break_threshold.
    """
    p = PivotParams()
    assert p.phase1_impulse_min == _D("70")
    assert p.phase1_min_candles == 2
    assert p.phase2_near_tolerance == _D("0.001")
    assert p.phase2_break_threshold == _D("0.003")
    assert p.phase3_zone_match == _D("0.002")
    assert p.phase3_break_threshold == _D("0.003")
    assert p.phase4_exhaustion_min == _D("60")
    assert p.phase4_min_candles == 3
    assert p.phase4_delta_drop == _D("0.5")
    assert p.phase5_flip_min_candles == 2
    assert p.phase5_timeout_candles == 5


def test_param_phase1_min_candles_gobierna_el_arranque() -> None:
    params = replace(_PARAMS, phase1_min_candles=3)
    bars = _bullish_sequence()
    state, events = _run(bars[:2], params)
    assert events[1].kind == "none"  # con 3 velas exigidas, dos no bastan
    assert state.phase == int(Phase.IDLE)
    assert state.impulse_count == 2


def test_param_phase2_break_threshold_gobierna_la_ruptura() -> None:
    """Con el umbral por defecto 100.2 NO rompe; bajandolo a 0.001, SI."""
    bars = [*_bullish_sequence()[:3], BarSignals(price=_D("100.2"), delta=_D("10"))]
    _, events = _run(bars)
    assert events[-1].kind == "none"
    _, strict = _run(bars, replace(_PARAMS, phase2_break_threshold=_D("0.001")))
    assert strict[-1].reason == Invalidation.PHASE2_PRICE_BREAK


def test_param_phase3_zone_match_gobierna_la_cercania_de_la_zona() -> None:
    """Una zona a 0.5% del nivel esta FUERA del 0.2% por defecto: no avanza."""
    far = BarSignals(
        price=_D("100.1"),
        delta=_D("10"),
        absorption=AbsorptionZone(
            zone_price=_D("100.5"), zone_type="bearish", zone_strength=_D("0.8")
        ),
    )
    state, events = _run([*_bullish_sequence()[:3], far])
    assert events[-1].kind == "none"
    assert state.phase == int(Phase.ENCOUNTER)
    wide, wide_events = _run(
        [*_bullish_sequence()[:3], far], replace(_PARAMS, phase3_zone_match=_D("0.01"))
    )
    assert wide_events[-1] == PivotEvent("advance", int(Phase.ABSORPTION), BULLISH)
    assert wide.phase == int(Phase.ABSORPTION)


def test_param_phase4_delta_drop_y_min_candles_gobiernan_el_agotamiento() -> None:
    """Con drop 0.05 el umbral cae a 6: las velas de delta 10 y 8 ya no cuentan."""
    params = replace(_PARAMS, phase4_delta_drop=_D("0.05"))
    state, events = _run(_bullish_sequence()[:7], params)
    assert [e.kind for e in events[4:]] == ["none", "none", "none"]
    assert state.phase == int(Phase.ABSORPTION)
    assert state.exhaustion_count == 1  # solo la vela de delta 5 (< 6) computo
    # Y con min_candles=1 basta la primera vela mermada para pasar a fase 4.
    quick, quick_events = _run(
        _bullish_sequence()[:5], replace(_PARAMS, phase4_min_candles=1)
    )
    assert quick_events[-1] == PivotEvent("advance", int(Phase.EXHAUSTION), BULLISH)
    assert quick.phase == int(Phase.EXHAUSTION)


def test_param_phase5_flip_min_candles_gobierna_la_confirmacion() -> None:
    """Con 3 velas de flip exigidas, las dos de la secuencia ya no confirman."""
    params = replace(_PARAMS, phase5_flip_min_candles=3)
    state, events = _run(_bullish_sequence(), params)
    assert events[-1].kind == "none"
    assert state.phase == int(Phase.EXHAUSTION)
    assert state.flip_count == 2


def test_param_phase5_timeout_candles_gobierna_el_timeout() -> None:
    quiet = BarSignals(price=_D("100.1"), delta=_D("10"))
    bars = [*_bullish_sequence()[:7], *([quiet] * 3)]
    _, events = _run(bars)
    assert events[-1].kind == "none"  # con timeout 5, tres velas quietas no bastan
    _, short = _run(bars, replace(_PARAMS, phase5_timeout_candles=2))
    assert short[-1].reason == Invalidation.PHASE5_TIMEOUT


def test_phase4_exhaustion_min_no_es_gate_estructural() -> None:
    """Decision 6b: el gate de fase 4 es el delta menguante, NO el proxy notrade.

    Mutar phase4_exhaustion_min a cualquier valor NO altera la secuencia: se conserva
    inyectable solo para el factor F2 de la confianza (P4).
    """
    baseline, base_events = _run(_bullish_sequence())
    for value in (_D("0"), _D("100"), _D("99999")):
        mutated, events = _run(
            _bullish_sequence(), replace(_PARAMS, phase4_exhaustion_min=value)
        )
        assert mutated == baseline
        assert events == base_events


def test_phase2_near_tolerance_no_es_gate_estructural() -> None:
    """phase2_near_tolerance tampoco se lee: fase 2 usa phase3_zone_match.

    Se conserva por paridad de los params v4; se eleva a Central en el informe de P3.
    """
    baseline, base_events = _run(_bullish_sequence())
    mutated, events = _run(
        _bullish_sequence(), replace(_PARAMS, phase2_near_tolerance=_D("0.5"))
    )
    assert mutated == baseline
    assert events == base_events


# --- Mordida, determinismo y gates de no-evaluable -------------------------------


def test_muerde_mutar_phase1_impulse_min_rompe_la_confirmacion() -> None:
    """El test MUERDE: con el umbral de impulso a 999 la FSM no arranca ni confirma."""
    sane, sane_events = _run(_bullish_sequence())
    assert sane_events[-1].kind == "confirmed"
    assert sane == PivotState()
    mutated, events = _run(
        _bullish_sequence(), replace(_PARAMS, phase1_impulse_min=_D("999"))
    )
    assert all(e.kind == "none" for e in events)
    assert mutated == PivotState()
    assert mutated.phase == int(Phase.IDLE)


def test_determinismo_dos_ejecuciones_identicas() -> None:
    """Mismo estado + misma barra + mismos params -> mismo resultado, bit a bit."""
    first_state, first_events = _run(_bullish_sequence())
    second_state, second_events = _run(_bullish_sequence())
    assert first_state == second_state
    assert first_events == second_events
    # Y a nivel de barra suelta: evaluate_bar no muta el estado de entrada.
    state = PivotState(
        phase=int(Phase.IMPULSE), direction=BULLISH, phase1_peak_delta=_D("120")
    )
    bar = _bullish_sequence()[2]
    out_a, event_a = evaluate_bar(state, bar, _PARAMS)
    out_b, event_b = evaluate_bar(state, bar, _PARAMS)
    assert out_a == out_b
    assert event_a == event_b
    assert state.phase == int(Phase.IMPULSE)  # la entrada sigue intacta


def test_absorption_none_mantiene_la_fsm_en_fase_2() -> None:
    """Gate ESTRUCTURAL: absorcion DIFERIDA (None) -> la FSM no confirma en vivo."""
    bars = _bullish_sequence()
    sin_absorcion = replace(bars[3], absorption=None)
    state, events = _run([*bars[:3], sin_absorcion, *bars[4:]])
    assert state.phase == int(Phase.ENCOUNTER)
    assert all(e.kind != "confirmed" for e in events)
    assert all(e.kind != "advance" or e.phase < int(Phase.ABSORPTION) for e in events)


def test_impulse_score_none_no_arranca_la_fsm() -> None:
    """NOT_EVALUABLE: sin impulse_score (historia insuficiente) no se inventa fase 1."""
    bars = [
        BarSignals(price=_D("99"), delta=_D("100")),
        BarSignals(price=_D("99.5"), delta=_D("120")),
        BarSignals(price=_D("99.8"), delta=_D("140")),
    ]
    state, events = _run(bars)
    assert all(e.kind == "none" for e in events)
    assert state == PivotState()
    assert state.impulse_count == 0


def test_impulse_score_none_a_media_secuencia_resetea_el_conteo() -> None:
    """Una vela sin impulso corta la racha: el conteo vuelve a cero (no acumula)."""
    bars = [
        BarSignals(price=_D("99"), delta=_D("100"), impulse_score=_D("80")),
        BarSignals(price=_D("99.5"), delta=_D("120")),  # no evaluable -> corta
        BarSignals(price=_D("99.8"), delta=_D("140"), impulse_score=_D("80")),
    ]
    state, events = _run(bars)
    assert all(e.kind == "none" for e in events)
    assert state.phase == int(Phase.IDLE)
    assert state.impulse_count == 1  # volvio a empezar en la tercera vela


def test_vp_touch_none_mantiene_la_fsm_en_fase_1() -> None:
    """Sin toque de nivel VP la FSM se queda en impulso, actualizando el pico."""
    bars = [*_bullish_sequence()[:2], BarSignals(price=_D("99.9"), delta=_D("200"))]
    state, events = _run(bars)
    assert events[-1].kind == "none"
    assert state.phase == int(Phase.IMPULSE)
    assert state.phase1_peak_delta == _D("200")


# --- Declaracion ADR-008 (declarada, NO cableada) --------------------------------


def test_declarations_publica_phase_y_confidence() -> None:
    ids = {d.source_id for d in declarations()}
    assert ids == {PIVOTPHASE_PHASE_SOURCE_ID, PIVOTPHASE_CONFIDENCE_SOURCE_ID}


def test_declaracion_phase_forma_adr_008() -> None:
    phase = next(d for d in declarations() if d.source_id == PIVOTPHASE_PHASE_SOURCE_ID)
    assert phase.source_type is SourceType.OBSERVABLE
    assert phase.servibility is Servibility.CONTINUOUS
    assert phase.memory_model is MemoryModel.RECURSIVE
    assert phase.value_type is ScalarType.INTEGER
    assert phase.cache_key_schema == (
        "exchange",
        "symbol",
        "market_type",
        "timeframe",
        "reset_policy",
        "formula_version",
    )


def test_declaracion_confidence_solo_declarada() -> None:
    confidence = next(
        d for d in declarations() if d.source_id == PIVOTPHASE_CONFIDENCE_SOURCE_ID
    )
    assert confidence.memory_model is MemoryModel.RECURSIVE
    assert confidence.value_type is ScalarType.DECIMAL
    assert confidence.servibility is Servibility.CONTINUOUS


def test_consumes_incluye_absorption() -> None:
    """absorption.* ENTRA en P08c-CONF-01: el replay la materializa para F1.

    Dejo de ser una arista diferida cuando P08b entrego candle.open y esta pieza cableo
    absorption.bid/ask_strength. Se declara porque se LEE de verdad -- mismo criterio de
    DAG honesto que en los cuatro detectores. notrade.score sigue fuera (F7, 3c).
    """
    for declaration in declarations():
        assert declaration.consumes == (
            "market.close",
            "orderflow.delta",
            "orderflow.delta_momentum",
            "footprint.price_range",
            "vp.poc",
            "vp.vah",
            "vp.val",
            "vp.hvn",
            "vp.lvn",
            "absorption.bid_strength",
            "absorption.ask_strength",
            "climax.top_strength",
            "climax.bottom_strength",
            "void.snap_bullish",
            "void.snap_bearish",
            "notrade.score",
            "cvd.value",
            "imbalance.buy_stack",
            "imbalance.sell_stack",
        )
        # ENMIENDA DE DAG HONESTO (3b): notrade.footprint_ineff/.flow_dislocation/.state
        # NO se listan -- F7 solo lee notrade.score, que ya suma los otros dos bloques.
        assert "notrade.footprint_ineff" not in declaration.consumes
        assert "notrade.flow_dislocation" not in declaration.consumes
        assert "notrade.state" not in declaration.consumes


def test_declaracion_esta_cableada_en_el_discovery_vivo() -> None:
    """P5 (DICTAMEN PIVOT-10): el catalogo vivo ya incluye pivotphase.*."""
    from ce_v5.platform.rules.discovery import discover_declarations

    ids = {d.source_id for d in discover_declarations()}
    assert PIVOTPHASE_PHASE_SOURCE_ID in ids
    assert PIVOTPHASE_CONFIDENCE_SOURCE_ID in ids
