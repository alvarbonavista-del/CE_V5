"""Computo puro del RSI de Wilder (P08b). SIN dependencia del substrato.

Determinista y reproducible BIT A BIT: computo FORWARD desde el reset fijo
de Wilder (semilla = media simple de los primeros N cambios), sobre cierres
CERRADOS oldest->newest. Convencion FIRMADA (P08b-02, fuente primaria
TradingView + Wikipedia): Wilder/RMA, semilla SMA de N. El contexto Decimal
esta PINNEADO (precision y redondeo fijos) para reproducibilidad entre
maquinas; cualquier cambio de contexto o formula EXIGE subir
RSI_FORMULA_VERSION (ADR-008).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

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

RSI_FORMULA_VERSION = 1

_RSI_PRECISION = 34
_RSI_ROUNDING = ROUND_HALF_EVEN
_HUNDRED = Decimal(100)
_ONE = Decimal(1)
_ZERO = Decimal(0)


def _rsi_from_avgs(avg_gain: Decimal, avg_loss: Decimal) -> Decimal:
    # "sin bajadas" -> 100 (mercado plano cae aqui por convencion); "sin
    # subidas" -> 0. El bug de KLineChart<v10 era devolver 0 en racha 100%
    # alcista; aqui es 100.
    if avg_loss == _ZERO:
        return _HUNDRED
    if avg_gain == _ZERO:
        return _ZERO
    rs = avg_gain / avg_loss
    return _HUNDRED - _HUNDRED / (_ONE + rs)


def wilder_rsi(closes: Sequence[Decimal], period: int) -> tuple[Decimal | None, ...]:
    """Serie RSI alineada 1:1 con `closes` (oldest->newest).

    None en el warm-up (sin `period` cambios aun); el primer valor valido
    cae en el indice `period` (hacen falta period+1 cierres). Semilla:
    media simple de los primeros `period` gains/losses. Recursion de Wilder:
    avg = (avg_prev*(period-1) + nuevo)/period.
    """
    if period < 1:
        msg = "wilder_rsi exige period >= 1."
        raise ValueError(msg)
    n = len(closes)
    result: list[Decimal | None] = [None] * n
    if n < period + 1:
        return tuple(result)
    with localcontext() as ctx:
        ctx.prec = _RSI_PRECISION
        ctx.rounding = _RSI_ROUNDING
        p = Decimal(period)
        gains: list[Decimal] = []
        losses: list[Decimal] = []
        for i in range(1, n):
            change = closes[i] - closes[i - 1]
            gains.append(change if change > _ZERO else _ZERO)
            losses.append(-change if change < _ZERO else _ZERO)
        avg_gain = sum(gains[:period], _ZERO) / p
        avg_loss = sum(losses[:period], _ZERO) / p
        result[period] = _rsi_from_avgs(avg_gain, avg_loss)
        for i in range(period, n - 1):
            avg_gain = (avg_gain * (p - _ONE) + gains[i]) / p
            avg_loss = (avg_loss * (p - _ONE) + losses[i]) / p
            result[i + 1] = _rsi_from_avgs(avg_gain, avg_loss)
    return tuple(result)


def rsi_seed(
    closes: Sequence[Decimal], period: int
) -> tuple[Decimal, Decimal, Decimal, Decimal] | None:
    """El ESTADO de Wilder en la barra `period`, o None si no hay historia para el.

    Devuelve (avg_gain, avg_loss, last_close, rsi): las dos medias suavizadas iniciales
    -- media SIMPLE de los primeros `period` gains/losses, que es la semilla firmada en
    P08b-02 --, el cierre de esa barra (closes[period]) y el RSI que sale de ese estado.
    None si len(closes) < period + 1: sin `period` cambios no hay semilla, que es el
    mismo warm-up que wilder_rsi expresa con None.

    Es el ARRANQUE del replay acotado. La aritmetica esta copiada de wilder_rsi y corre
    bajo el MISMO contexto pinneado, asi que sembrar aqui y avanzar con rsi_step da la
    serie BIT A BIT identica a wilder_rsi (GATE ADR-007). Si alguien separase las dos
    formulas, el candado de equivalencia de test_rsi.py lo caza.
    """
    if period < 1:
        msg = "rsi_seed exige period >= 1."
        raise ValueError(msg)
    if len(closes) < period + 1:
        return None
    with localcontext() as ctx:
        ctx.prec = _RSI_PRECISION
        ctx.rounding = _RSI_ROUNDING
        p = Decimal(period)
        gains: list[Decimal] = []
        losses: list[Decimal] = []
        for i in range(1, period + 1):
            change = closes[i] - closes[i - 1]
            gains.append(change if change > _ZERO else _ZERO)
            losses.append(-change if change < _ZERO else _ZERO)
        avg_gain = sum(gains[:period], _ZERO) / p
        avg_loss = sum(losses[:period], _ZERO) / p
        rsi = _rsi_from_avgs(avg_gain, avg_loss)
    return (avg_gain, avg_loss, closes[period], rsi)


def rsi_step(
    avg_gain: Decimal,
    avg_loss: Decimal,
    last_close: Decimal,
    close: Decimal,
    period: int,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Un paso de Wilder: del estado en T-1 y el cierre de T, al estado en T.

    Devuelve (avg_gain, avg_loss, last_close, rsi) YA en la barra nueva. last_close
    entra y sale porque gain[T] = close[T] - close[T-1] es un DIFERENCIAL: sin el cierre
    previo el paso no se puede dar, y por eso el cierre forma parte del estado
    persistido (rsi_snapshot, 0025) y no es un extra opcional.

    MISMA recurrencia y MISMO contexto pinneado que wilder_rsi -- el suavizado
    (avg_prev*(period-1) + nuevo)/period y el _rsi_from_avgs son los de arriba, copiados
    y no reescritos --, que es lo que hace el replay desde snapshot BIT A BIT igual a
    recomputar desde el origen (GATE ADR-007).
    """
    if period < 1:
        msg = "rsi_step exige period >= 1."
        raise ValueError(msg)
    with localcontext() as ctx:
        ctx.prec = _RSI_PRECISION
        ctx.rounding = _RSI_ROUNDING
        p = Decimal(period)
        change = close - last_close
        gain = change if change > _ZERO else _ZERO
        loss = -change if change < _ZERO else _ZERO
        next_gain = (avg_gain * (p - _ONE) + gain) / p
        next_loss = (avg_loss * (p - _ONE) + loss) / p
        rsi = _rsi_from_avgs(next_gain, next_loss)
    return (next_gain, next_loss, close, rsi)


RSI_SOURCE_ID = "rsi.value"

# period POR DEFECTO de rsi.value: el 14 de Wilder, el que trae la convencion firmada en
# P08b-02. Es el default DECLARADO (viaja en la ParamSpec y lo hereda el
# materializador), no una constante de materializacion: una regla puede pedir otro
# period por override, y entra en la cache_key porque rsi(7) y rsi(14) son series
# DISTINTAS.
RSI_PERIOD_DEFAULT = 14


def rsi_value_declaration() -> DataSourceDeclaration:
    """rsi.value: RSI de Wilder sobre el cierre (RECURSIVE), period en la cache_key.

    Cara DECLARATIVA de este modulo (el computo es wilder_rsi/rsi_seed/rsi_step, arriba:
    una sola fuente de verdad de la formula, prec=34). CONTINUOUS: hay materializador
    (RsiRecursiveSpec, replay desde el snapshot de la 0025) y period default real, que
    son las dos condiciones para servir. El warm-up NO la hace discontinua: es historia
    insuficiente al principio de la serie -- lo mismo que una ventana de cierres corta
    -- y el materializador lo expresa devolviendo MENOS valores, que el evaluador ya
    trata como NOT_EVALUABLE (K3). consumes=(market.close,): el RSI deriva de la serie
    de cierres, su padre logico inmediato.
    """
    return DataSourceDeclaration(
        source_id=RSI_SOURCE_ID,
        source_type=SourceType.OBSERVABLE,
        servibility=Servibility.CONTINUOUS,
        # RECURSIVE: el estado de T depende del de T-1 (suavizado de Wilder). Una
        # correccion en k contamina todo lo posterior -> NO-CONFORME para correccion en
        # v5.0 (como cvd INTEGRATOR y ema).
        memory_model=MemoryModel.RECURSIVE,
        value_type=ScalarType.DECIMAL,
        evaluation_contexts=tuple(tf.value for tf in Timeframe),
        history_units=(HistoryUnit.BARS,),
        params=(
            ParamSpec(
                name="period",
                value_type=ScalarType.INTEGER,
                default=ScalarValue(
                    scalar_type=ScalarType.INTEGER,
                    integer_value=RSI_PERIOD_DEFAULT,
                ),
            ),
        ),
        overridable_params=("period",),
        shared_evaluation=True,
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=("exchange", "symbol", "timeframe", "period"),
        consumes=(MARKET_CLOSE_SOURCE_ID,),
    )


def declarations() -> tuple[DataSourceDeclaration, ...]:
    """Declaraciones que este modulo publica al catalogo vivo (discovery, MAT-02)."""
    return (rsi_value_declaration(),)
