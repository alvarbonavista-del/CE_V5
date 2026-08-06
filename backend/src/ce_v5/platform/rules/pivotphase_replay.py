"""Replay determinista de pivotphase desde series materializadas (P08c P5 T4c).

replay_from_series es la LOGICA PURA del replay (DICTAMEN P08c-PIVOT-07 Q1): dado el
conjunto de series ya materializadas y el PivotState del snapshot ancla, recorre las
barras oldest->newest manteniendo las ventanas trailing de NORM_WINDOW, arma BarSignals
(gate de fase 1) y ConfidenceInputs (F1/F2/F3/F4/F6/F7 con input vivo; solo F5 = None),
hila el estado por evaluate_bar (P3) y gradua por compute_confidence (P4), y emite
(phase, confidence) por barra. Sin BD: el glue (replay_pivotphase) materializa y
persiste.

Ventanas INCLUSIVE hasta la barra i (Q2). Estrategia A (DICTAMEN P08c-PIVOT-09): la FSM
avanza DESDE IDLE sobre toda la ventana; las primeras `lookback` barras (NORM_WINDOW)
AVANZAN la FSM y ceban las ventanas pero NO se emiten. Como las secuencias del FSM son
bounded (< NORM_WINDOW), el bootstrap-desde-IDLE con ese lookback reconstruye el estado
entero (el snapshot es as-of/auditoria, no continuidad). WARM-UP natural (Q3): sin
distribucion suficiente, impulse_score None y los factores no evaluables aportan 0.
Determinista, solo Decimal.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ce_v5.platform.rules.pivotphase import (
    BEARISH,
    BULLISH,
    AbsorptionZone,
    BarSignals,
    Phase,
    PivotParams,
    PivotState,
    VpTouch,
    evaluate_bar,
)
from ce_v5.platform.rules.pivotphase_confidence import (
    ConfidenceInputs,
    ConfidenceParams,
    FactorInput,
    VpContextInput,
    compute_confidence,
)
from ce_v5.platform.rules.pivotphase_signals import (
    cvd_divergence_feature,
    cvd_divergence_magnitudes,
    effort_result_feature,
    exhaustion_feature,
    normalize_impulse_score,
)


@dataclass(frozen=True, slots=True)
class ReplaySeries:
    """Series materializadas ALINEADAS barra a barra, de la misma longitud."""

    price: tuple[Decimal, ...]
    delta: tuple[Decimal, ...]
    delta_momentum: tuple[Decimal, ...]
    price_range: tuple[Decimal, ...]
    vp_poc: tuple[Decimal, ...]
    vp_vah: tuple[Decimal, ...]
    vp_val: tuple[Decimal, ...]
    vp_hvn: tuple[Decimal, ...]
    vp_lvn: tuple[Decimal, ...]
    absorption_bid: tuple[Decimal, ...]
    absorption_ask: tuple[Decimal, ...]
    climax_top: tuple[Decimal, ...]
    climax_bottom: tuple[Decimal, ...]
    void_bull: tuple[Decimal, ...]
    void_bear: tuple[Decimal, ...]
    notrade_score: tuple[Decimal, ...]
    cvd: tuple[Decimal, ...]

    def __post_init__(self) -> None:
        lengths = {
            len(self.price),
            len(self.delta),
            len(self.delta_momentum),
            len(self.price_range),
            len(self.vp_poc),
            len(self.vp_vah),
            len(self.vp_val),
            len(self.vp_hvn),
            len(self.vp_lvn),
            len(self.absorption_bid),
            len(self.absorption_ask),
            len(self.climax_top),
            len(self.climax_bottom),
            len(self.void_bull),
            len(self.void_bear),
            len(self.notrade_score),
            len(self.cvd),
        }
        if len(lengths) != 1:
            msg = "las series de ReplaySeries deben tener la misma longitud."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class BarOutcome:
    """Salida por barra emitida del replay: fase (0-5) y confianza 0-100."""

    phase: int
    confidence: Decimal


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """Resultado del replay: outcomes por barra EMITIDA + estado final de la FSM.

    final_state = PivotState tras la ULTIMA barra recorrida (la vigente); el glue lo
    serializa para el snapshot (as-of / auditoria).
    """

    outcomes: tuple[BarOutcome, ...]
    final_state: PivotState


def _nearest_vp_touch(
    price: Decimal, poc: Decimal, vah: Decimal, val: Decimal
) -> VpTouch:
    """Nivel VP mas cercano al precio entre {poc, vah, val} (Q6). Empate: poc."""
    candidates = (("poc", poc), ("vah", vah), ("val", val))
    best_type, best_price = min(candidates, key=lambda c: abs(price - c[1]))
    return VpTouch(level_type=best_type, level_price=best_price)


def _absorption_raw(
    direction: str, bid_strength: Decimal, ask_strength: Decimal
) -> Decimal | None:
    """El raw de F1: la fuerza de absorcion del lado que SOPORTA el pivote esperado.

    LA ORIENTACION NO ES OBVIA Y AQUI ESTA LA TRAMPA. PivotState.direction es la del
    IMPULSO, no la del pivote: el propio modulo lo dice ("BULLISH = impulso alcista ->
    pivote esperado bearish"). Asi que:

      impulso BULLISH -> se espera un TECHO -> lo confirma la absorcion de COMPRADORES
        (AbsorptionSide.ASK: agresion compradora que no logra avanzar) -> ask_strength.
      impulso BEARISH -> se espera un SUELO -> lo confirma la absorcion de VENDEDORES
        (AbsorptionSide.BID: agresion vendedora que no logra hundir) -> bid_strength.

    Es la MISMA regla de contrariedad que ya aplica el gate de fase 3 de la FSM
    (is_counter_zone: con impulso BULLISH se exige zona BEARISH). Tomar el lado
    contrario seria peor que no medir: puntuaria como soporte justo la agresion que
    empuja el precio en la direccion del impulso.

    Sin direccion (FSM en IDLE, direction == "") no hay pivote que soportar: None, y F1
    aporta 0 en esa barra. Es la misma convencion conservadora de F2/F4.
    """
    if direction == BULLISH:
        return ask_strength
    if direction == BEARISH:
        return bid_strength
    return None


def _absorption_zone(
    price: Decimal, bid_strength: Decimal, ask_strength: Decimal
) -> AbsorptionZone | None:
    """La AbsorptionZone de la barra para el gate de fase 3, o None sin absorcion.

    ZONE_TYPE ESTA INVERTIDO RESPECTO DEL LADO, y no es un descuido (P08c-CONF-04): el
    dominio de zone_type son las constantes BULLISH/BEARISH y describen QUE HACE la zona
    al precio, no quien agredio.

      ASK (agresion COMPRADORA absorbida, el precio no logra subir) = TECHO = zona que
        empuja a la BAJA -> zone_type = BEARISH.
      BID (agresion VENDEDORA absorbida, el precio no logra hundirse) = SUELO = zona que
        empuja al ALZA -> zone_type = BULLISH.

    Asi encaja con is_counter_zone de _on_encounter, que con impulso BULLISH (espera
    TECHO) exige una zona BEARISH; es la misma orientacion que ya usa _absorption_raw
    para F1. Tomar el lado tal cual haria que ninguna zona casara nunca.

    ZONE_PRICE = el CIERRE de la barra (P08c-CONF-04). detect_absorption no produce un
    nivel: razona sobre escalares de la barra (volumen, delta, span, desplazamiento) y
    devuelve (detected, side, strength) sin precio. El cierre es el unico precio con
    significado disponible en la barra donde la absorcion ocurrio, y es el MISMO que
    alimenta BarSignals.price, asi que zona y precio viven en la misma escala -- que es
    lo que la comparacion de _on_encounter (|zone_price - level| / level) necesita.

    A lo sumo un lado es > 0 por CONSTRUCCION: absorption.bid/ask_strength salen de un
    unico AbsorptionSignal con un unico `side`, y cada fuente publica 0 cuando la
    absorcion no es de su lado. No se comprueba aqui porque no es una alineacion entre
    dos lecturas independientes (eso si se verifica, en _read_detector_window): es una
    invariante de un solo computo aguas arriba.
    """
    if ask_strength > 0:
        return AbsorptionZone(
            zone_price=price, zone_type=BEARISH, zone_strength=ask_strength
        )
    if bid_strength > 0:
        return AbsorptionZone(
            zone_price=price, zone_type=BULLISH, zone_strength=bid_strength
        )
    return None


def _toxicity_raw(
    climax_top: Decimal,
    climax_bottom: Decimal,
    void_bull: Decimal,
    void_bear: Decimal,
    notrade_score: Decimal,
) -> Decimal:
    """El raw de F7: la intensidad de "algo va mal" en la barra, en [0,1].

    max(climax, void, notrade) -- reduccion RATIFICADA (P08c-CONF-01), semilla [A
    CALIBRAR AHP] como todo lo demas del modelo. Los tres insumos ya escalan distinto y
    se homogeneizan ANTES del max, no despues:

      climax = max(climax_top_strength, climax_bottom_strength)   -- ya en [0,1]
      void   = max(void_snap_bullish, void_snap_bearish)          -- ya en {0,1}
      notrade = notrade.score / 100  -- 0-100 (tope 65 sin L2) -> [0,0.65]

    RAW ORIENTADO A TOXICIDAD, NO A SOPORTE: mayor raw = mas evidencia de que la barra
    es ruidosa (climax de agotamiento, snap de void, entorno no-trade). El modelo (P4)
    es quien invierte esto a confianza -- descending=True en compute_confidence --,
    aqui NO se resta ni se invierte nada: seria una segunda inversion.

    max() y no promedio ni suma: basta con que UN detector dispare fuerte para que la
    barra sea sospechosa; promediar diluiria una senal fuerte con dos flojas, y sumar
    podria superar 1 sin razon (los tres pueden coincidir en la misma barra, como probo
    P08c-DET-01 paso b con absorcion+climax en la misma ventana).
    """
    climax = max(climax_top, climax_bottom)
    void = max(void_bull, void_bear)
    notrade = notrade_score / Decimal(100)
    return max(climax, void, notrade)


def _project_confidence(phase: int, confidence: Decimal | None) -> Decimal:
    """Proyeccion Q2 opcion (d): IDLE o NOT_EVALUABLE -> 0; en otro caso, el valor."""
    if phase == int(Phase.IDLE) or confidence is None:
        return Decimal(0)
    return confidence


def replay_from_series(
    series: ReplaySeries,
    snapshot_state: PivotState,
    params: PivotParams,
    conf_params: ConfidenceParams,
    norm_window: int,
    lookback: int,
) -> ReplayResult:
    """Recorre las barras, hila el estado y emite (phase, confidence) por barra emitida.

    La FSM AVANZA en TODAS las barras (DICTAMEN P08c-PIVOT-09, Estrategia A): las
    primeras `lookback` barras avanzan la FSM y ceban las ventanas, pero NO se emiten ni
    gradua su confianza; a partir de `lookback` se emite. Ventanas trailing INCLUSIVE de
    norm_window. Devuelve el estado final para el snapshot. Ver docstring del modulo.
    """
    abs_delta: list[Decimal] = []
    f1_raws: list[Decimal] = []
    f2_raws: list[Decimal] = []
    f3_raws: list[Decimal] = []
    f4_raws: list[Decimal] = []
    f7_raws: list[Decimal] = []
    # F3 (P08c-CONF-03) se precomputa de UNA pasada sobre la ventana entera y no barra a
    # barra: los pivotes de una divergencia necesitan `strength` barras a cada lado, asi
    # que no son conocibles con solo el prefijo. Lo que SI se decide dentro del bucle es
    # cual de las divergencias de esa barra SOPORTA el pivote, porque eso depende de la
    # direccion vigente de la FSM. Precomputar no rompe el determinismo: la deteccion
    # solo mira close y cvd, ninguno de los dos depende del estado.
    divergencias = cvd_divergence_magnitudes(series.price, series.cvd)
    state = snapshot_state
    outcomes: list[BarOutcome] = []
    for i in range(len(series.delta)):
        delta = series.delta[i]
        abs_delta.append(abs(delta))
        window_abs = abs_delta[-norm_window:]
        f2_raw = exhaustion_feature(delta, window_abs)
        f4_raw = effort_result_feature(delta, series.price_range[i])
        if f2_raw is not None:
            f2_raws.append(f2_raw)
        if f4_raw is not None:
            f4_raws.append(f4_raw)
        bar = BarSignals(
            price=series.price[i],
            delta=delta,
            impulse_score=normalize_impulse_score(delta, window_abs),
            delta_momentum=series.delta_momentum[i],
            vp_touch=_nearest_vp_touch(
                series.price[i], series.vp_poc[i], series.vp_vah[i], series.vp_val[i]
            ),
            # VIVA desde P08c-CONF-04 (paso 3d). Hasta 3c iba None a proposito: faltaba
            # el mandato de que precio usar como nivel de zona, y fabricarlo sin
            # ratificar habria sido inventar semantica. Ratificado el cierre como
            # zone_price, el camino completo 0->5 ya confirma en vivo.
            absorption=_absorption_zone(
                series.price[i], series.absorption_bid[i], series.absorption_ask[i]
            ),
        )
        state, _event = evaluate_bar(state, bar, params)
        # F1 se calcula DESPUES de avanzar la FSM porque necesita la direccion VIGENTE
        # del impulso, y se acumula ANTES del corte de lookback para que su distribucion
        # llegue cebada a la primera barra emitida (igual que f2_raws/f4_raws).
        f1_raw = _absorption_raw(
            state.direction, series.absorption_bid[i], series.absorption_ask[i]
        )
        if f1_raw is not None:
            f1_raws.append(f1_raw)
        # F7 (P08c-CONF-01 paso 3b): a diferencia de F1/F2/F4, _toxicity_raw es TOTAL --
        # un max() sobre cinco Decimal ya materializados, sin division ni rango
        # degenerado que pueda devolver None. Los detectores de P08c-DET-01 sirven 0
        # como HECHO ("no paso nada"), no como hueco, asi que aqui no hay insumo
        # ausente que envolver en Optional: se acumula y se usa sin condicional.
        f7_raw = _toxicity_raw(
            series.climax_top[i],
            series.climax_bottom[i],
            series.void_bull[i],
            series.void_bear[i],
            series.notrade_score[i],
        )
        f7_raws.append(f7_raw)
        # F3, como F1, se orienta con la direccion VIGENTE (post evaluate_bar) y se
        # acumula antes del corte de lookback para llegar cebado a la primera emitida.
        f3_raw = cvd_divergence_feature(state.direction, divergencias.get(i, {}))
        f3_raws.append(f3_raw)
        if i < lookback:
            continue
        inputs = ConfidenceInputs(
            f1=(
                FactorInput(raw=f1_raw, distribution=tuple(f1_raws[-norm_window:]))
                if f1_raw is not None
                else None
            ),
            f2=(
                FactorInput(raw=f2_raw, distribution=tuple(f2_raws[-norm_window:]))
                if f2_raw is not None
                else None
            ),
            f3=FactorInput(raw=f3_raw, distribution=tuple(f3_raws[-norm_window:])),
            f4=(
                FactorInput(raw=f4_raw, distribution=tuple(f4_raws[-norm_window:]))
                if f4_raw is not None
                else None
            ),
            f6=VpContextInput(
                price=series.price[i],
                hvn_price=series.vp_hvn[i],
                lvn_price=series.vp_lvn[i],
            ),
            f7=FactorInput(raw=f7_raw, distribution=tuple(f7_raws[-norm_window:])),
        )
        result = compute_confidence(inputs, conf_params)
        outcomes.append(
            BarOutcome(
                phase=state.phase,
                confidence=_project_confidence(state.phase, result.confidence),
            )
        )
    return ReplayResult(outcomes=tuple(outcomes), final_state=state)
