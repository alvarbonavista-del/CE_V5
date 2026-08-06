"""Computo puro del MACD (P08b). SIN dependencia del substrato.

Reutiliza el EMA fundado (indicators.ema): macd = EMA(fast) - EMA(slow);
signal = EMA(signal_period, macd); histogram = macd - signal (x1, NO x2:
convencion TradingView fundada en P08b-02). Defaults (12, 26, 9).

Como ambas EMAs siembran en el primer src (EMA[0]==src[0], P08b-08),
macd[0] == 0 -> INVARIANTE DE SEMILLA distintivo del MACD (signal[0]==0,
histogram[0]==0). Aislado y clavado por el candado golden.

Todo bajo el MISMO contexto Decimal pinneado que el EMA (prec 34,
ROUND_HALF_EVEN) -- las restas incluidas -- para reproducibilidad bit-a-bit.
Cambio de formula/contexto sube MACD_FORMULA_VERSION (ADR-008). warm-up =
parametro calibrado.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import Enum

from ce_v5.platform.rules.indicators.ema import ema, ema_from_anchor
from ce_v5.platform.rules.rawclose import MARKET_CLOSE_SOURCE_ID
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

MACD_FORMULA_VERSION = 1

_MACD_PRECISION = 34
_MACD_ROUNDING = ROUND_HALF_EVEN

# Parametrizacion POR DEFECTO (12, 26, 9): la convencion firmada en P08b-02. Son los
# defaults DECLARADOS (viajan en las ParamSpec y los hereda el materializador) Y los de
# la funcion pura de abajo -- una sola fuente para los dos, para que no puedan divergir.
# No son constantes de materializacion: una regla puede pedir otra parametrizacion por
# override, y los tres entran en la cache_key porque macd(12,26,9) y macd(5,35,5) son
# series DISTINTAS.
MACD_FAST_DEFAULT = 12
MACD_SLOW_DEFAULT = 26
MACD_SIGNAL_DEFAULT = 9


@dataclass(frozen=True, slots=True)
class Macd:
    """Series MACD alineadas 1:1 con los cierres (oldest->newest)."""

    macd: tuple[Decimal, ...]
    signal: tuple[Decimal, ...]
    histogram: tuple[Decimal, ...]


def macd(
    closes: Sequence[Decimal],
    fast: int = MACD_FAST_DEFAULT,
    slow: int = MACD_SLOW_DEFAULT,
    signal_period: int = MACD_SIGNAL_DEFAULT,
) -> Macd:
    """MACD sobre `closes` (oldest->newest).

    macd = EMA(fast) - EMA(slow); signal = EMA(signal_period, macd);
    histogram = macd - signal (x1). Valores desde la barra 0 (via EMA);
    macd[0] == signal[0] == histogram[0] == 0 (invariante de semilla).
    """
    for name, p in (("fast", fast), ("slow", slow), ("signal_period", signal_period)):
        if p < 1:
            msg = f"macd exige {name} >= 1."
            raise ValueError(msg)
    with localcontext() as ctx:
        ctx.prec = _MACD_PRECISION
        ctx.rounding = _MACD_ROUNDING
        ema_fast = ema(closes, fast)
        ema_slow = ema(closes, slow)
        macd_line = tuple(f - s for f, s in zip(ema_fast, ema_slow, strict=True))
        signal = ema(macd_line, signal_period)
        histogram = tuple(m - g for m, g in zip(macd_line, signal, strict=True))
    return Macd(macd=macd_line, signal=signal, histogram=histogram)


def _validar_periodos(fast: int, slow: int, signal_period: int) -> None:
    """El MISMO dominio que macd(): cada periodo >= 1, con su mismo mensaje."""
    for name, p in (("fast", fast), ("slow", slow), ("signal_period", signal_period)):
        if p < 1:
            msg = f"macd exige {name} >= 1."
            raise ValueError(msg)


# El estado del MACD por barra son las TRES EMAs internas, y las TRES salidas publicas
# salen de ellas: (ema_fast, ema_slow, ema_signal, line, signal, histogram).
MacdState = tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Decimal]


def macd_seed(close: Decimal) -> MacdState:
    """El estado del MACD en la barra 0 y sus tres salidas, desde el primer cierre.

    Las dos EMAs del precio siembran en el propio cierre (EMA[0] == src[0], invariante
    P08b-08), asi que line[0] = close - close = 0, la EMA de la senal siembra en ese
    mismo 0 e histogram[0] = 0: el INVARIANTE DE SEMILLA del MACD. NO hay warm-up -- hay
    valor desde la barra 0 --, a diferencia del RSI.

    Los ceros se CALCULAN (close - close), no se escriben como Decimal(0). No es
    puntilloso: en Decimal el exponente se propaga (5.5 + 0.00 da 5.50, y 5.5 + 0 da
    5.5), asi que una semilla con el exponente equivocado se arrastraria a toda la serie
    y el replay dejaria de ser bit-exacto respecto de macd(). Esta funcion reproduce las
    MISMAS operaciones que macd() hace en su barra 0.
    """
    with localcontext() as ctx:
        ctx.prec = _MACD_PRECISION
        ctx.rounding = _MACD_ROUNDING
        ema_fast = close
        ema_slow = close
        line = ema_fast - ema_slow
        # ema() siembra en su primer src, y el primer src de la senal ES line[0].
        ema_signal = line
        histogram = line - ema_signal
    return (ema_fast, ema_slow, ema_signal, line, ema_signal, histogram)


def macd_step(
    ema_fast: Decimal,
    ema_slow: Decimal,
    ema_signal: Decimal,
    close: Decimal,
    fast: int = MACD_FAST_DEFAULT,
    slow: int = MACD_SLOW_DEFAULT,
    signal_period: int = MACD_SIGNAL_DEFAULT,
) -> MacdState:
    """Un paso del MACD: del estado en T-1 y el cierre de T, al estado y salidas en T.

    Las dos EMAs del precio avanzan sobre `close`; line es su diferencia; la EMA de la
    senal avanza sobre esa line RECIEN calculada (no sobre la de la barra anterior: es
    el encadenamiento que hace macd()); histogram = line - signal (x1, convencion
    TradingView de P08b-02).

    LA RECURRENCIA NO SE REESCRIBE AQUI: se llama a ema_from_anchor, que ES la recursion
    de ema() bajo su contexto pinneado (el mismo prec 34 que este modulo). Copiar la
    formula habria dejado dos sitios que mantener sincronizados; llamarla deja UNO. Las
    restas van bajo el contexto pinneado de este modulo, igual que en macd().
    """
    _validar_periodos(fast, slow, signal_period)
    with localcontext() as ctx:
        ctx.prec = _MACD_PRECISION
        ctx.rounding = _MACD_ROUNDING
        next_fast = ema_from_anchor(ema_fast, (close,), fast)[0]
        next_slow = ema_from_anchor(ema_slow, (close,), slow)[0]
        line = next_fast - next_slow
        next_signal = ema_from_anchor(ema_signal, (line,), signal_period)[0]
        histogram = line - next_signal
    return (next_fast, next_slow, next_signal, line, next_signal, histogram)


class MacdOutput(Enum):
    """Cual de las tres salidas del MACD emite una fuente.

    Las tres comparten UN estado (las tres EMAs internas) y un paso de calculo; lo unico
    que las distingue es que proyeccion de ese paso publican. Por eso el enum vive aqui,
    junto a la funcion pura, y no en el cableado: es una propiedad del indicador.
    """

    LINE = "line"
    SIGNAL = "signal"
    HISTOGRAM = "histogram"


def select_output(state: MacdState, output: MacdOutput) -> Decimal:
    """La salida `output` de un estado devuelto por macd_seed/macd_step."""
    _, _, _, line, signal, histogram = state
    if output is MacdOutput.LINE:
        return line
    if output is MacdOutput.SIGNAL:
        return signal
    return histogram


MACD_LINE_SOURCE_ID = "macd.line"
MACD_SIGNAL_SOURCE_ID = "macd.signal"
MACD_HISTOGRAM_SOURCE_ID = "macd.histogram"


def _macd_declaration(source_id: str) -> DataSourceDeclaration:
    """Declaracion comun de macd.line/macd.signal/macd.histogram (P08b-LOTE3-01).

    Las tres tienen la MISMA forma a proposito: salen del mismo estado, con los mismos
    tres params y la misma cache_key. Lo unico que cambia es el source_id -- y, en el
    cableado, que proyeccion emite su materializador.

    CONTINUOUS: hay materializador (MacdRecursiveSpec, replay desde el snapshot de la
    0026) y defaults reales. Y es continua de verdad: el MACD da valor desde la barra 0
    (macd[0] == 0, invariante de semilla), sin el tramo de warm-up que si tiene el RSI.
    RECURSIVE: por dentro son tres EMAs encadenadas, cada una dependiente de su T-1.
    consumes=(market.close,): el MACD deriva de la serie de cierres.
    """
    return DataSourceDeclaration(
        source_id=source_id,
        source_type=SourceType.OBSERVABLE,
        servibility=Servibility.CONTINUOUS,
        memory_model=MemoryModel.RECURSIVE,
        value_type=ScalarType.DECIMAL,
        evaluation_contexts=tuple(tf.value for tf in Timeframe),
        history_units=(HistoryUnit.BARS,),
        params=(
            ParamSpec(
                name="fast",
                value_type=ScalarType.INTEGER,
                default=ScalarValue(
                    scalar_type=ScalarType.INTEGER,
                    integer_value=MACD_FAST_DEFAULT,
                ),
            ),
            ParamSpec(
                name="slow",
                value_type=ScalarType.INTEGER,
                default=ScalarValue(
                    scalar_type=ScalarType.INTEGER,
                    integer_value=MACD_SLOW_DEFAULT,
                ),
            ),
            ParamSpec(
                name="signal",
                value_type=ScalarType.INTEGER,
                default=ScalarValue(
                    scalar_type=ScalarType.INTEGER,
                    integer_value=MACD_SIGNAL_DEFAULT,
                ),
            ),
        ),
        overridable_params=("fast", "slow", "signal"),
        shared_evaluation=True,
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=("exchange", "symbol", "timeframe", "fast", "slow", "signal"),
        consumes=(MARKET_CLOSE_SOURCE_ID,),
    )


def macd_line_declaration() -> DataSourceDeclaration:
    """macd.line: EMA(fast) - EMA(slow) sobre el cierre."""
    return _macd_declaration(MACD_LINE_SOURCE_ID)


def macd_signal_declaration() -> DataSourceDeclaration:
    """macd.signal: EMA(signal) sobre la macd.line."""
    return _macd_declaration(MACD_SIGNAL_SOURCE_ID)


def macd_histogram_declaration() -> DataSourceDeclaration:
    """macd.histogram: macd.line - macd.signal (x1, convencion TradingView)."""
    return _macd_declaration(MACD_HISTOGRAM_SOURCE_ID)


def declarations() -> tuple[DataSourceDeclaration, ...]:
    """Declaraciones que este modulo publica al catalogo vivo (discovery, MAT-02)."""
    return (
        macd_line_declaration(),
        macd_signal_declaration(),
        macd_histogram_declaration(),
    )
