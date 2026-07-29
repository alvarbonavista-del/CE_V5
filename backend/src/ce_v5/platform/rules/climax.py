"""Climax de volumen footprint/candle-based (climax.*, F7, P08c). Nucleo determinista.

Implementa el detector del PRE-REGISTRO AHP CLIMAX REV 3 (firmado 2026-07-29): una
vela de AGOTAMIENTO -- volumen y rango excepcionales para su historia reciente con el
cierre RECHAZADO en el tercio opuesto al extremo alcanzado -- eleva P(giro genuino).
Alcance v5.0 = FOOTPRINT/CANDLE-BASED por vela cerrada, SIN estado, SIN L2.

DETECTOR, no calibrador: aqui vive la LOGICA (dispara bien DADO un umbral) con las
SEMILLAS [PARIDAD v4] como PARAMETRO (ClimaxParams). El numero final de cada umbral lo
fija la calibracion (walk-forward sobre corpus), DIFERIDA con dueno P08c: este modulo
NO calibra, solo detecta de forma determinista y reproducible.

ENTREGA EN DOS PARTES (dictamen Central sobre ELEVACION P08c-CLIMAX-01, H1 opcion A,
paridad con absorcion): este modulo entrega AHORA el nucleo puro (deteccion +
etiquetado) + sus tests. La DataSourceDeclaration climax.* (que declara consumes
footprint + candle.open/high/low/close) se DIFIERE hasta que esos campos de vela sean
servibles (P08b o tanda de campos de vela); hoy solo market.close esta declarado.

VARIABLES (por vela cerrada, un flujo). volume = buy+sell agresor del footprint;
high/low/close de la vela (candle.*). La direccion sale SOLO de la posicion del cierre
en el rango (rev 3, H2: v4 NO compara open contra close; open no interviene). De donde
salen (footprint + candle.*) es de la capa de materializacion (CE-14).

FORMA SERVIBLE (para la declaracion diferida): climax.strength_top y
climax.strength_bottom, Decimal en [0,1]; strength_top = strength si la vela es climax
de TECHO detectado, si no None; strength_bottom analogo para SUELO. El detalle de
nivel/toque/rotura es maquinaria de estado, no un escalar servible (misma linea que
absorcion).

DECIMAL PINNED: todo en Decimal bajo el contexto por defecto; sin round en la fuente
(round = presentacion). Mismo input -> mismo veredicto y misma fuerza, bit a bit.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# Version de la formula de deteccion. El golden se ata a esta cadena: si la formula
# cambia, la version sube y el golden se regenera. Alimentara el formula_version de la
# DataSourceDeclaration diferida (junto con las semillas, todas en la cache_key).
CLIMAX_FORMULA_VERSION = "climax.v1"

# Semillas [PARIDAD v4] (engines/l1/pivots/volume_climax_engine.py). NO son verdades:
# punto de partida parametrizado; su valor final lo fija la calibracion (AHP), diferida.
_VOL_PCT = Decimal("95")  # percentil de volumen requerido (_VOL_PCT)
_RANGE_PCT = Decimal("90")  # percentil de rango requerido (_RANGE_PCT)
_CLOSE_REJECTION = Decimal("0.33")  # tercio de rechazo del cierre (_CLOSE_REJECTION)
_EXCESS_CAP = Decimal("2")  # tope del exceso en la fuerza (rev 3, H3): 200%
_WEIGHT_VOL = Decimal("0.40")  # peso de vol_excess
_WEIGHT_RANGE = Decimal("0.30")  # peso de range_excess
_WEIGHT_REJ = Decimal("0.30")  # peso de rej_score
_STRENGTH_MIN = Decimal("0.30")  # fuerza minima para emitir (v4: 30/100)

# Ventana de normalizacion (velas) y minimos [PARIDAD v4]. Son CONSTANTES de modulo
# (como NORM_WINDOW en absorcion): cuando se hornee la declaracion, entran en la
# cache_key/formula_version junto con las semillas de ClimaxParams.
NORM_WINDOW = 100  # ventana rodante de percentiles (_NORM_WINDOW)
MIN_CANDLES = 20  # minimo de velas para evaluar (_MIN_CANDLES)
MIN_SAMPLES = 10  # minimo de muestras de volumen y de rango para el percentil

# Semillas de la ETIQUETA [PARIDAD v4 = FSM de pivote]. Solo fixtures/calibracion.
_LABEL_R_BARS = 5  # horizonte de confirmacion (PHASE5_TIMEOUT_CANDLES)
_CONFIRM_FLIP_CANDLES = 2  # velas de flip de delta que confirman (PHASE5_FLIP_MIN)
_INVALIDATION = Decimal("0.003")  # tolerancia de invalidacion (PHASE2_BREAK_THRESHOLD)


class ClimaxSide(StrEnum):
    """Lado del climax."""

    # Volumen/rango excepcional con cierre en el tercio INFERIOR -> reversion bajista.
    TOP = "top"
    # Volumen/rango excepcional con cierre en el tercio SUPERIOR -> reversion alcista.
    BOTTOM = "bottom"


@dataclass(frozen=True, slots=True)
class ClimaxParams:
    """Umbrales del detector como PARAMETRO (semillas [PARIDAD v4] por defecto).

    Se exponen para que la calibracion (AHP, diferida) los sustituya sin tocar la
    logica. Los pesos van FIJOS en paridad v4 por decision registrada (calibrar solo
    umbrales reduce grados de libertad y sobreajuste); reabrirlos exige justificacion
    del walk-forward.
    """

    vol_pct: Decimal = _VOL_PCT
    range_pct: Decimal = _RANGE_PCT
    close_rejection: Decimal = _CLOSE_REJECTION
    excess_cap: Decimal = _EXCESS_CAP
    weight_vol: Decimal = _WEIGHT_VOL
    weight_range: Decimal = _WEIGHT_RANGE
    weight_rej: Decimal = _WEIGHT_REJ
    strength_min: Decimal = _STRENGTH_MIN


# Singleton de parametros por defecto (evita construir en el default de la firma, B008).
_DEFAULT_PARAMS = ClimaxParams()


@dataclass(frozen=True, slots=True)
class ClimaxCandle:
    """Vela de entrada del detector: volumen agresor total + high/low/close.

    open NO se incluye (rev 3, H2): la direccion sale solo de la posicion del cierre,
    no de la relacion open/close. Anadirlo seria un campo muerto.
    """

    volume: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True, slots=True)
class ClimaxSignal:
    """Veredicto de climax de una vela.

    detected=True solo si las tres condiciones se cumplen Y strength >= strength_min.
    strength en [0,1] (0 si no hay ni candidatura); side es el lado candidato (None si
    no se cumplen las condiciones estructurales).
    """

    detected: bool
    side: ClimaxSide | None
    strength: Decimal


def _percentile(ordered: Sequence[Decimal], pct: Decimal) -> Decimal:
    """Percentil por interpolacion lineal sobre una secuencia YA ordenada ascendente.

    pct en [0, 100]. Metodo [PARIDAD v4] (volume_climax_engine._percentile, lineas
    293-309): k = (n-1)*pct/100; interpola linealmente entre los dos vecinos. El caller
    garantiza n >= 1.
    """
    n = len(ordered)
    if pct <= 0:
        return ordered[0]
    if pct >= 100:
        return ordered[-1]
    k = Decimal(n - 1) * pct / Decimal(100)
    floor = int(k)  # k >= 0 -> int() trunca = floor
    ceil = min(floor + 1, n - 1)
    if floor == ceil:
        return ordered[floor]
    lower = ordered[floor] * (Decimal(ceil) - k)
    upper = ordered[ceil] * (k - Decimal(floor))
    return lower + upper


def climax_thresholds(
    prior: Sequence[ClimaxCandle],
    params: ClimaxParams = _DEFAULT_PARAMS,
) -> tuple[Decimal, Decimal] | None:
    """Umbrales de volumen y de rango sobre la ventana PREVIA (window[:-1]), v4.

    El volumen usa TODAS las velas previas; el rango filtra rangos > 0 (una vela sin
    rango no aporta al percentil de rango). Devuelve None si hay < MIN_SAMPLES muestras
    de volumen o < MIN_SAMPLES de rango positivo (la ventana previa no es fiable aun).
    """
    volumes = sorted(candle.volume for candle in prior)
    if len(volumes) < MIN_SAMPLES:
        return None
    ranges = sorted(span for candle in prior if (span := candle.high - candle.low) > 0)
    if len(ranges) < MIN_SAMPLES:
        return None
    return _percentile(volumes, params.vol_pct), _percentile(ranges, params.range_pct)


def evaluate_climax(
    window: Sequence[ClimaxCandle],
    params: ClimaxParams = _DEFAULT_PARAMS,
) -> ClimaxSignal:
    """Evalua si la ULTIMA vela de la ventana es un climax. [AHP CLIMAX rev 3].

    window = hasta NORM_WINDOW velas del mismo flujo, oldest->newest; se evalua la
    ultima y los percentiles salen de las previas (window[:-1]). Tres condiciones en
    AND: (a) volumen > percentil vol_pct; (b) rango > percentil range_pct; (c) cierre
    en el tercio opuesto (pos_close <= close_rejection -> TOP; pos_close >=
    1-close_rejection -> BOTTOM). La direccion NO usa open (rev 3, H2). Fuerza =
    w_vol*vol_excess + w_range*range_excess + w_rej*rej_score, con vol/range_excess
    topados al 200% y divididos entre 2 (rev 3, H3); se emite si strength >=
    strength_min.
    """
    no_signal = ClimaxSignal(detected=False, side=None, strength=Decimal(0))
    if len(window) < MIN_CANDLES:
        return no_signal
    current = window[-1]
    price_range = current.high - current.low
    if price_range <= 0 or current.volume <= 0:
        return no_signal
    thresholds = climax_thresholds(window[:-1], params)
    if thresholds is None:
        return no_signal
    vol_threshold, range_threshold = thresholds
    # Condiciones (a) y (b): estricto ">" (AHP firmado campo 3).
    if current.volume <= vol_threshold or price_range <= range_threshold:
        return no_signal
    pos_close = (current.close - current.low) / price_range
    side = _classify(pos_close, params.close_rejection)
    if side is None:
        return no_signal
    strength = _strength(
        current.volume,
        price_range,
        vol_threshold,
        range_threshold,
        pos_close,
        side,
        params,
    )
    if strength < params.strength_min:
        return ClimaxSignal(detected=False, side=side, strength=strength)
    return ClimaxSignal(detected=True, side=side, strength=strength)


def _classify(pos_close: Decimal, close_rejection: Decimal) -> ClimaxSide | None:
    """Lado del climax por la posicion del cierre (rev 3, H2: solo pos_close)."""
    if pos_close <= close_rejection:
        return ClimaxSide.TOP
    if pos_close >= Decimal(1) - close_rejection:
        return ClimaxSide.BOTTOM
    return None


def _strength(
    volume: Decimal,
    price_range: Decimal,
    vol_threshold: Decimal,
    range_threshold: Decimal,
    pos_close: Decimal,
    side: ClimaxSide,
    params: ClimaxParams,
) -> Decimal:
    """Fuerza en [0,1]. [PARIDAD v4] con la normalizacion corregida (rev 3, H3)."""
    vol_ratio = volume / vol_threshold - Decimal(1)
    vol_excess = min(vol_ratio, params.excess_cap) / params.excess_cap
    range_ratio = price_range / range_threshold - Decimal(1)
    range_excess = min(range_ratio, params.excess_cap) / params.excess_cap
    if side is ClimaxSide.TOP:
        rejection = (params.close_rejection - pos_close) / params.close_rejection
    else:
        upper_edge = Decimal(1) - params.close_rejection
        rejection = (pos_close - upper_edge) / params.close_rejection
    rej_score = min(max(rejection, Decimal(0)), Decimal(1))
    return (
        params.weight_vol * vol_excess
        + params.weight_range * range_excess
        + params.weight_rej * rej_score
    )


@dataclass(frozen=True, slots=True)
class LabelParams:
    """Semillas de la etiqueta [PARIDAD v4 = FSM de pivote]. Solo fixtures."""

    r_bars: int = _LABEL_R_BARS
    confirm_flip_candles: int = _CONFIRM_FLIP_CANDLES
    invalidation: Decimal = _INVALIDATION


# Singleton de parametros de etiqueta por defecto (evita B008 en la firma).
_DEFAULT_LABEL_PARAMS = LabelParams()


@dataclass(frozen=True, slots=True)
class LabelCandle:
    """Vela POSTERIOR al climax, para etiquetar.

    delta = bar_delta del footprint (para el flip de confirmacion). high/low para la
    invalidacion por continuacion mas alla del extremo del climax.
    """

    delta: Decimal
    high: Decimal
    low: Decimal


def label_climax(
    *,
    side: ClimaxSide,
    climax_high: Decimal,
    climax_low: Decimal,
    subsequent: Sequence[LabelCandle],
    params: LabelParams = _DEFAULT_LABEL_PARAMS,
) -> int:
    """Etiqueta 1 (gira) / 0 (no gira) de un climax. [Etiqueta AHP CLIMAX rev 3].

    Dentro de r_bars velas posteriores: se CONFIRMA el giro cuando el delta agresor se
    invierte (TOP: delta < 0; BOTTOM: delta > 0) y se MANTIENE confirm_flip_candles
    velas CONSECUTIVAS, SIN que antes el precio CONTINUE mas alla del extremo del climax
    por > invalidation (TOP: high del climax; BOTTOM: low). Si el precio continua
    (invalidacion) antes del flip, o no se confirma en r_bars (timeout), la etiqueta es
    0. Solo se usa para fixtures deterministas y la futura calibracion, nunca en
    produccion.
    """
    flip_run = 0
    for candle in subsequent[: params.r_bars]:
        if side is ClimaxSide.TOP:
            if candle.high > climax_high * (Decimal(1) + params.invalidation):
                return 0
            flipped = candle.delta < 0
        else:
            if candle.low < climax_low * (Decimal(1) - params.invalidation):
                return 0
            flipped = candle.delta > 0
        if flipped:
            flip_run += 1
            if flip_run >= params.confirm_flip_candles:
                return 1
            continue
        flip_run = 0
    return 0
