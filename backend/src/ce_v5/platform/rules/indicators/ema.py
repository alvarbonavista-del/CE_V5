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
from source.rules.scalar import ScalarType

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


EMA_SOURCE_ID = "ema.value"


def ema_declaration() -> DataSourceDeclaration:
    """ema.value: EMA del cierre (RECURSIVE), period en la cache_key.

    Cara DECLARATIVA de este modulo (el computo es ema(), arriba: una sola fuente de
    verdad de la formula, prec=34). NON_SERVIBLE en v5.0 (dictamen P08b-INT-06
    OPCION D + A2): descubrible y con cache_key/DAG validos, pero el validador del
    Bloque 3 la rechaza como termino de regla hasta que la propagacion de params
    (MAT-05 Q2) traiga el materializador y el period default real. Ese dia: flip
    ADITIVO a CONTINUOUS + un materializador que pliega el ema() de este mismo
    modulo. consumes=(market.close,): EMA deriva de la serie de cierres, su padre
    logico inmediato (dictamen INT-06-A1).
    """
    return DataSourceDeclaration(
        source_id=EMA_SOURCE_ID,
        source_type=SourceType.OBSERVABLE,
        # NON_SERVIBLE: se calcula (ema()) pero NO se combina en reglas todavia; el
        # Bloque 3 la rechaza como termino. Sin materializador en v5.0 (MAT-05 Q4:
        # un solo period materializable y el EMA no tiene period natural). Flip
        # ADITIVO a CONTINUOUS cuando propaguen params (MAT-05 Q2, dictamen A2).
        servibility=Servibility.NON_SERVIBLE,
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
                # Sin default en v5.0 (dictamen INT-06 Q3): el EMA no tiene period
                # natural. NON_SERVIBLE + default=None es coherente: sin period fijo
                # no hay valor que servir. Al propagar params se fija default real y
                # flip a CONTINUOUS.
                default=None,
            ),
        ),
        shared_evaluation=True,
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=("exchange", "symbol", "timeframe", "period"),
        consumes=(MARKET_CLOSE_SOURCE_ID,),
    )


def declarations() -> tuple[DataSourceDeclaration, ...]:
    """Declaraciones que este modulo publica al catalogo vivo (discovery, MAT-02)."""
    return (ema_declaration(),)
