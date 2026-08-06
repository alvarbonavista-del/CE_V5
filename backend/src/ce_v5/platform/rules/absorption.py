"""Absorcion footprint-based (absorption.*, F1, P08c). Nucleo determinista del detector.

Implementa el detector del PRE-REGISTRO AHP de absorcion (firmado): agresion fuerte en
un nivel cuyo precio NO se mueve en proporcion (los pasivos absorben). Esfuerzo vs
resultado (Wyckoff / Bookmap). Alcance v5.0 = FOOTPRINT-BASED por vela cerrada
(DEC-ABSORCION-01); refill/iceberg (L2) es v5.1.

DETECTOR, no calibrador: aqui vive la LOGICA (dispara bien DADO un umbral) con las
SEMILLAS [PARIDAD v4] como PARAMETRO (AbsorptionParams). El numero final de cada umbral
lo fija la calibracion (walk-forward sobre corpus), DIFERIDA con dueno P08c: este modulo
NO calibra, solo detecta de forma determinista y reproducible.

VARIABLES (por vela, un flujo). Exactas del footprint: volume = buy+sell agresor;
delta = bar_delta. price_range = span de precio del footprint (max-min de sus celdas).
displacement = close - open de la vela (SIGNADO): el movimiento DENTRO de la vela, que
es lo que la absorcion pregunta (condicion C2 de Central; el proxy close-to-close seria
otra semantica). Las cuatro entran como escalares ya materializados; de donde salen
(footprint + candle.open/close) es de la capa de materializacion (CE-14).

DECLARACION DIFERIDA: la DataSource absorption.* (que declara consumes footprint +
candle.open/close) se hornea cuando candle.open (P08b) este mergeado, como F3 espera a
swing.*. Este modulo entrega ya el nucleo puro, independiente de esa espera.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, StrEnum
from typing import TYPE_CHECKING

from ce_v5.platform.rules.indicators.candle import CANDLE_OPEN_SOURCE_ID
from ce_v5.platform.rules.rawclose import MARKET_CLOSE_SOURCE_ID
from ce_v5.platform.rules.rawfootprint import MARKET_FOOTPRINT_SOURCE_ID
from source.datasource import (
    DataSourceDeclaration,
    HistoryUnit,
    MemoryModel,
    ParamSpec,
    Servibility,
    SharingScope,
    SourceType,
)
from source.families.market import Timeframe
from source.rules.scalar import ScalarType, ScalarValue

if TYPE_CHECKING:
    from collections.abc import Sequence


def _decimal_param(name: str, default: Decimal) -> ParamSpec:
    """ParamSpec decimal con su default (semilla [PARIDAD v4] del detector)."""
    return ParamSpec(
        name=name,
        value_type=ScalarType.DECIMAL,
        default=ScalarValue(scalar_type=ScalarType.DECIMAL, decimal_value=default),
    )


# Semillas [PARIDAD v4] (engines/l1/absorption_zone_engine.py). NO son verdades: son el
# punto de partida parametrizado; su valor final lo fija la calibracion (AHP), diferida.
_RATIO_FLOOR = Decimal("2.0")  # piso del umbral de ratio (_ABS_MIN_RATIO)
_PERCENTILE = Decimal("0.80")  # percentil del umbral adaptativo
_AGGRESSION_MIN_FRACTION = Decimal("0.10")  # |delta| > 0.10 * volume
_CONTAINMENT_MAX_FRACTION = Decimal("0.30")  # |displacement| < 0.30 * range
_WEIGHT_RATIO = Decimal("0.40")  # peso de abs_norm en la fuerza
_WEIGHT_DELTA = Decimal("0.35")  # peso de delta_norm en la fuerza
_WEIGHT_MOVE = Decimal("0.25")  # peso de move_norm en la fuerza
_STRENGTH_MIN = Decimal("0.30")  # fuerza minima para emitir (v4: 30/100)

# Ventana de normalizacion del umbral adaptativo (velas). [PARIDAD v4] = 100.
NORM_WINDOW = 100


class AbsorptionSide(StrEnum):
    """Lado de la absorcion (I-04 1.3)."""

    # Vendedores absorbidos (agresion vendedora sin caida) -> posible SUELO.
    BID = "bid"
    # Compradores absorbidos (agresion compradora sin avance) -> posible TECHO.
    ASK = "ask"


@dataclass(frozen=True, slots=True)
class AbsorptionParams:
    """Umbrales del detector como PARAMETRO (semillas [PARIDAD v4] por defecto).

    Se exponen para que la calibracion (AHP, diferida) los sustituya sin tocar la
    logica. Los pesos van FIJOS en paridad v4 por decision registrada (calibrar solo
    umbrales reduce grados de libertad y sobreajuste); reabrirlos exige justificacion
    del walk-forward.
    """

    ratio_floor: Decimal = _RATIO_FLOOR
    percentile: Decimal = _PERCENTILE
    aggression_min_fraction: Decimal = _AGGRESSION_MIN_FRACTION
    containment_max_fraction: Decimal = _CONTAINMENT_MAX_FRACTION
    weight_ratio: Decimal = _WEIGHT_RATIO
    weight_delta: Decimal = _WEIGHT_DELTA
    weight_move: Decimal = _WEIGHT_MOVE
    strength_min: Decimal = _STRENGTH_MIN


# Singleton de parametros por defecto (evita construir en el default de la firma, B008).
_DEFAULT_PARAMS = AbsorptionParams()


@dataclass(frozen=True, slots=True)
class AbsorptionSignal:
    """Veredicto de absorcion de una vela.

    detected=True solo si las cuatro condiciones se cumplen Y strength >= strength_min.
    strength en [0,1] (0 si no hay ni candidatura); side es el lado candidato (None si
    no se cumplen las condiciones estructurales).
    """

    detected: bool
    side: AbsorptionSide | None
    strength: Decimal


def adaptive_threshold(
    recent_ratios: Sequence[Decimal],
    params: AbsorptionParams = _DEFAULT_PARAMS,
) -> Decimal:
    """Umbral adaptativo de absorption_ratio: max(percentil_p, piso). [PARIDAD v4].

    Menos de 2 muestras -> el piso (no hay distribucion de la que sacar percentil). El
    indice del percentil es int(n * p) acotado a n-1 (mismo metodo determinista que v4).
    """
    if len(recent_ratios) < 2:
        return params.ratio_floor
    ordered = sorted(recent_ratios)
    index = int(len(ordered) * params.percentile)
    index = min(index, len(ordered) - 1)
    return max(ordered[index], params.ratio_floor)


def detect_absorption(
    *,
    volume: Decimal,
    delta: Decimal,
    price_range: Decimal,
    displacement: Decimal,
    threshold: Decimal,
    params: AbsorptionParams = _DEFAULT_PARAMS,
) -> AbsorptionSignal:
    """Detecta absorcion en una vela DADO su umbral de ratio. [PARIDAD v4].

    Cuatro condiciones: (a) volume/range > threshold; (b) |delta| > aggression*volume;
    (c) delta y displacement en direcciones OPUESTAS; (d) |displacement|
    containment*range. Lado: delta>0 y precio sin avanzar -> ASK (techo); si no -> BID
    (suelo). Fuerza = w_ratio*abs_norm + w_delta*delta_norm + w_move*move_norm; se emite
    solo si strength >= strength_min. Volumen o rango no positivos -> no hay absorcion.
    """
    no_signal = AbsorptionSignal(detected=False, side=None, strength=Decimal(0))
    if volume <= 0 or price_range <= 0:
        return no_signal

    ratio = volume / price_range
    structural = (
        ratio > threshold
        and abs(delta) > params.aggression_min_fraction * volume
        and delta * displacement < 0
        and abs(displacement) < params.containment_max_fraction * price_range
    )
    if not structural:
        return no_signal

    side = AbsorptionSide.ASK if delta > 0 and displacement <= 0 else AbsorptionSide.BID
    abs_norm = min(Decimal(1), (ratio - threshold) / threshold)
    delta_norm = min(Decimal(1), abs(delta) / volume)
    move_norm = max(Decimal(0), Decimal(1) - abs(displacement) / price_range)
    strength = (
        params.weight_ratio * abs_norm
        + params.weight_delta * delta_norm
        + params.weight_move * move_norm
    )
    if strength < params.strength_min:
        return AbsorptionSignal(detected=False, side=side, strength=strength)
    return AbsorptionSignal(detected=True, side=side, strength=strength)


# --- Cara SERVIBLE: dos fuentes por LADO (P08c-DET-01) --------------------------------
#
# El veredicto de absorcion es un TRIPLETE (detected, side, strength) y ningun
# ScalarType lo representa entero. Se sirve como DOS fuentes DECIMAL, una por lado:
# cada una publica la FUERZA cuando la absorcion es de SU lado, y 0 cuando no la hay
# o es del contrario.
# Asi una Rule pregunta "absorption.bid_strength > 0.5" sin necesitar un tipo compuesto,
# y el 0 es un hecho legitimo ("no hubo absorcion de este lado"), no un hueco.

ABSORPTION_BID_STRENGTH_SOURCE_ID = "absorption.bid_strength"
ABSORPTION_ASK_STRENGTH_SOURCE_ID = "absorption.ask_strength"


class AbsorptionOutput(Enum):
    """Cual de las DOS salidas servibles publica una fuente.

    Las dos salen del MISMO veredicto; lo unico que cambia es el lado que miran. Mismo
    patron que FibOutput/MacdOutput: el enum vive junto a la funcion pura porque es una
    propiedad del indicador, no del cableado.
    """

    BID_STRENGTH = "bid_strength"
    ASK_STRENGTH = "ask_strength"


_SIDE_BY_OUTPUT: dict[AbsorptionOutput, AbsorptionSide] = {
    AbsorptionOutput.BID_STRENGTH: AbsorptionSide.BID,
    AbsorptionOutput.ASK_STRENGTH: AbsorptionSide.ASK,
}


def absorption_output(signal: AbsorptionSignal, output: AbsorptionOutput) -> Decimal:
    """La fuerza de la absorcion si es del lado de `output`; 0 en cualquier otro caso.

    TRES situaciones colapsan al mismo 0, y es correcto que asi sea: no hubo absorcion,
    la hubo del lado contrario, o hubo candidatura estructural sin fuerza suficiente
    (detected=False con strength>0). En las tres, la respuesta a "cuanta absorcion de
    ESTE lado hay en esta barra" es ninguna. La fuerza sub-umbral NO se publica:
    emitirla convertiria el umbral del detector en decorativo.
    """
    if signal.detected and signal.side is _SIDE_BY_OUTPUT[output]:
        return signal.strength
    return Decimal(0)


def _absorption_declaration(source_id: str) -> DataSourceDeclaration:
    """Declaracion comun de las dos absorption.* (P08c-DET-01).

    WINDOWED: el veredicto de la barra T necesita el umbral ADAPTATIVO, que sale del
    percentil de los ratios de las barras previas -- una ventana ACOTADA (NORM_WINDOW),
    no la historia entera. Por eso no hay snapshot ni migracion: se recomputa por
    ventana, como vp.*.

    consumes DAG HONESTO (enmienda P08c-DET-01): market.footprint aporta el volumen
    agresor, el delta y el span; candle.open y market.close forman el displacement
    (close - open), que es la condicion de CONTENCION. Nada mas: no se declara lo que
    el nucleo no lee.

    PARAMS DEFAULT-ONLY: los UMBRALES calibrables (AHP diferido) viajan con su semilla
    [PARIDAD v4] y entran en la cache_key -- dos umbrales distintos son series distintas
    --, pero NO son overridables: el materializador llama al nucleo con sus defaults
    y un override compilaria sin que nadie lo leyera (MAT-05 Q2, mismo criterio que
    bin_count en vp.*). Los PESOS de la fuerza no se declaran: van FIJOS en paridad v4
    por decision registrada, como los ratios Fibonacci de fib.*.
    """
    return DataSourceDeclaration(
        source_id=source_id,
        source_type=SourceType.OBSERVABLE,
        servibility=Servibility.CONTINUOUS,
        memory_model=MemoryModel.WINDOWED,
        value_type=ScalarType.DECIMAL,
        evaluation_contexts=tuple(tf.value for tf in Timeframe),
        history_units=(HistoryUnit.BARS,),
        params=(
            _decimal_param("ratio_floor", _RATIO_FLOOR),
            _decimal_param("percentile", _PERCENTILE),
            _decimal_param("aggression_min_fraction", _AGGRESSION_MIN_FRACTION),
            _decimal_param("containment_max_fraction", _CONTAINMENT_MAX_FRACTION),
            _decimal_param("strength_min", _STRENGTH_MIN),
        ),
        shared_evaluation=True,
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=(
            "exchange",
            "symbol",
            "market_type",
            "timeframe",
            "ratio_floor",
            "percentile",
            "aggression_min_fraction",
            "containment_max_fraction",
            "strength_min",
        ),
        consumes=(
            MARKET_FOOTPRINT_SOURCE_ID,
            CANDLE_OPEN_SOURCE_ID,
            MARKET_CLOSE_SOURCE_ID,
        ),
    )


def absorption_bid_strength_declaration() -> DataSourceDeclaration:
    """absorption.bid_strength: fuerza de la absorcion de VENDEDORES (posible suelo)."""
    return _absorption_declaration(ABSORPTION_BID_STRENGTH_SOURCE_ID)


def absorption_ask_strength_declaration() -> DataSourceDeclaration:
    """absorption.ask_strength: fuerza de la absorcion de COMPRADORES (posible
    techo)."""
    return _absorption_declaration(ABSORPTION_ASK_STRENGTH_SOURCE_ID)


def declarations() -> tuple[DataSourceDeclaration, ...]:
    """Declaraciones que este modulo publica al catalogo vivo (discovery, MAT-02).

    Las dos entran a la vez: un lado sin el otro dejaria la mitad del veredicto sin
    forma de consultarse.
    """
    return (
        absorption_bid_strength_declaration(),
        absorption_ask_strength_declaration(),
    )
