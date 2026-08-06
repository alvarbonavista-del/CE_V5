"""Computo puro del EMA (P08b). SIN dependencia del substrato.

Convencion FUNDADA (periferico de investigacion EMA, ratificada en P08b-08:
convergencia de 3 lineas + prueba logica por contradiccion + ficha A-1.4):
TradingView siembra ta.ema con el PRIMER VALOR DE LA FUENTE (src), NO con SMA de
N. INVARIANTE DISTINTIVO DE LA SEMILLA: EMA[0] == src[0]; el EMA produce VALOR
DESDE LA BARRA 0 (sin tramo None de warm-up, a diferencia del RSI). alpha =
2/(period+1); EMA[i] = alpha*src[i] + (1-alpha)*EMA[i-1].

Contexto Decimal PINNEADO (prec 34, ROUND_HALF_EVEN) para reproducibilidad
bit-a-bit. Cualquier cambio de semilla/formula/contexto sube EMA_FORMULA_VERSION
(ADR-008); el UNICO punto que depende de la semilla es EMA[0]==src[0], aislado y
verificado por el candado golden (condicion P08b-08). El warm-up es PARAMETRO
calibrado aguas abajo (I-01 B4), no un tramo None.
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

EMA_FORMULA_VERSION = 1

_EMA_PRECISION = 34
_EMA_ROUNDING = ROUND_HALF_EVEN
_ONE = Decimal(1)
_TWO = Decimal(2)


def ema(src: Sequence[Decimal], period: int) -> tuple[Decimal, ...]:
    """Serie EMA alineada 1:1 con `src` (oldest->newest).

    Semilla = primer valor de la fuente: resultado[0] == src[0] (VALOR DESDE LA
    BARRA 0; sin None de warm-up). alpha = 2/(period+1).
    """
    if period < 1:
        msg = "ema exige period >= 1."
        raise ValueError(msg)
    n = len(src)
    if n == 0:
        return ()
    with localcontext() as ctx:
        ctx.prec = _EMA_PRECISION
        ctx.rounding = _EMA_ROUNDING
        alpha = _TWO / (Decimal(period) + _ONE)
        one_minus = _ONE - alpha
        out: list[Decimal] = [src[0]]
        prev = src[0]
        for i in range(1, n):
            prev = alpha * src[i] + one_minus * prev
            out.append(prev)
    return tuple(out)


def ema_from_anchor(
    anchor: Decimal, closes: Sequence[Decimal], period: int
) -> tuple[Decimal, ...]:
    """La CONTINUACION del EMA desde un valor ancla: un valor por cierre de `closes`.

    MISMA recurrencia y MISMO contexto pinneado que ema() -- alpha, one_minus y la linea
    de recurrencia estan copiadas de ella, no reescritas --, que es lo que hace que
    replayar desde un snapshot de valor de una serie BIT A BIT identica a recomputarla
    desde el origen (GATE ADR-007). Si alguien las separase, el candado de equivalencia
    de test_ema.py y el GATE de integracion lo cazan.

    NO siembra nada: `anchor` ES el EMA de la barra ANTERIOR a closes[0], asi que
    resultado[0] ya es una barra NUEVA de la serie y len(resultado) == len(closes). La
    equivalencia con ema() esta clavada en los tests:
    ema(src, period) == (src[0], *ema_from_anchor(src[0], src[1:], period)).
    """
    if period < 1:
        msg = "ema_from_anchor exige period >= 1."
        raise ValueError(msg)
    with localcontext() as ctx:
        ctx.prec = _EMA_PRECISION
        ctx.rounding = _EMA_ROUNDING
        alpha = _TWO / (Decimal(period) + _ONE)
        one_minus = _ONE - alpha
        out: list[Decimal] = []
        prev = anchor
        for close in closes:
            prev = alpha * close + one_minus * prev
            out.append(prev)
    return tuple(out)


EMA_SOURCE_ID = "ema.value"

# period POR DEFECTO de ema.value (dictamen P08b-LOTE3-01 Q1). Es el default DECLARADO
# (viaja en la ParamSpec y lo hereda el materializador), no una constante de
# materializacion: una regla puede pedir otro period por override y entra en la
# cache_key, porque ema(9) y ema(21) son series DISTINTAS.
EMA_PERIOD_DEFAULT = 20


def ema_declaration() -> DataSourceDeclaration:
    """ema.value: EMA del cierre (RECURSIVE, CONTINUOUS), period en la cache_key.

    Cara DECLARATIVA de este modulo (el computo es ema()/ema_from_anchor, arriba: una
    sola fuente de verdad de la formula, prec=34). CONTINUOUS desde P08b-LOTE3-01: la
    propagacion de params (MAT-05 Q2) ya esta cableada y EmaRecursiveSpec pliega el
    ema() de este mismo modulo desde el snapshot de la 0023, asi que el validador del
    Bloque 3 ya puede aceptarla como termino de regla. Cierra el flip ADITIVO que dejo
    anunciado el dictamen P08b-INT-06 (OPCION D + A2): la fuente no cambia de identidad
    ni de cache_key, solo deja de estar vetada. period es parametro OVERRIDABLE con
    default 20 (Q1) y entra en la cache_key. consumes=(market.close,): EMA deriva de la
    serie de cierres, su padre logico inmediato (dictamen INT-06-A1).
    """
    return DataSourceDeclaration(
        source_id=EMA_SOURCE_ID,
        source_type=SourceType.OBSERVABLE,
        # CONTINUOUS: hay materializador (EmaRecursiveSpec) y period default real, que
        # eran las DOS condiciones que el dictamen INT-06 puso al flip. El EMA da valor
        # desde la barra 0 (sin tramo None de warm-up), asi que es continua de verdad.
        servibility=Servibility.CONTINUOUS,
        # RECURSIVE: EMA[T] depende de EMA[T-1]. Una correccion en k contamina todo lo
        # posterior -> NO-CONFORME para correccion en v5.0 (como cvd INTEGRATOR).
        memory_model=MemoryModel.RECURSIVE,
        value_type=ScalarType.DECIMAL,
        evaluation_contexts=tuple(tf.value for tf in Timeframe),
        history_units=(HistoryUnit.BARS,),
        params=(
            ParamSpec(
                name="period",
                value_type=ScalarType.INTEGER,
                # Default REAL (Q1): 20. El EMA no tiene un period "natural" impuesto
                # por la formula, pero servir una fuente CONTINUOUS exige un valor
                # concreto que servir cuando la regla no pide ninguno; 20 es el que
                # fija el dictamen. Quien quiera otro lo pide por override.
                default=ScalarValue(
                    scalar_type=ScalarType.INTEGER,
                    integer_value=EMA_PERIOD_DEFAULT,
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
    return (ema_declaration(),)
