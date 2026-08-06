"""Imbalance DIAGONAL apilado sobre las celdas del footprint (P08c-CONF-05).

Alimenta F5 del modelo de confianza de pivotphase, el ultimo factor que quedaba con
peso 0. Dos fuentes servibles, una por lado: imbalance.buy_stack (pila de compra,
evidencia de SUELO) e imbalance.sell_stack (pila de venta, evidencia de TECHO).

POINT_LOCAL, no WINDOWED (DICTAMEN P08c-CONF-05): la pila se mide dentro de la vela, con
ratio y minimo FIJOS, asi que el valor de T sale del footprint de T y de nada mas. No
hay
umbral adaptativo ni percentil que pida ventana -- a diferencia de absorption (umbral
adaptativo), climax (percentiles), notrade (min-max) o void (caduca en r_bars).
Declararla WINDOWED habria dejado una ventana escrita en el contrato que el nucleo no
lee, y habria cegado las primeras barras con un warm-up sin motivo. Mismo modelo y misma
forma que footprint.price_range y orderflow.delta.

DIAGONAL Y NO VERTICAL: se compara la agresion COMPRADORA de un nivel contra la
VENDEDORA del nivel de ABAJO (y al reves), porque son las dos caras de la misma
oportunidad de cruce -- el ask de P y el bid de P-1 --. Comparar el mismo nivel consigo
mismo mediria otra cosa (el delta de la celda, que ya sirve orderflow.delta).

Determinista, solo Decimal (ADR-007).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

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

    from source.families.footprint import FootprintCell

IMBALANCE_BUY_STACK_SOURCE_ID = "imbalance.buy_stack"
IMBALANCE_SELL_STACK_SOURCE_ID = "imbalance.sell_stack"

# Semillas del PRE-REGISTRO F5 (P08c-CONF-05). [A CALIBRAR AHP], no verdades: no hay
# semilla v4 documentada para el imbalance apilado -- a diferencia de notrade o
# absorption, que citan fichero y linea de v4 --, asi que estas dos las fija el
# pre-registro y las revisara la calibracion.
IMBALANCE_RATIO = Decimal("3.0")
MIN_STACK = 3

# Dimensiones de la cache_key: las cuatro del footprint del que deriva + los dos
# parametros que cambian el veredicto.
_IMBALANCE_CACHE_KEY_SCHEMA: tuple[str, ...] = (
    "exchange",
    "symbol",
    "market_type",
    "timeframe",
    "imbalance_ratio",
    "min_stack",
)


def _stack_strength(run_length: int, min_stack: int) -> Decimal:
    """Fuerza [0,1] de una corrida: min(1, run / min_stack). min_stack <= 0 -> 0."""
    if min_stack <= 0 or run_length <= 0:
        return Decimal(0)
    if run_length >= min_stack:
        return Decimal(1)
    return Decimal(run_length) / Decimal(min_stack)


def detect_stacked_imbalance(
    cells: Sequence[FootprintCell],
    ratio: Decimal = IMBALANCE_RATIO,
    min_stack: int = MIN_STACK,
) -> tuple[Decimal, Decimal]:
    """(buy_stack, sell_stack) en [0,1] de las celdas de UNA barra.

    EL PUENTE DE VOCABULARIO, que es donde esto se invierte si se lee deprisa: el
    pre-registro habla de ask_vol/bid_vol y la celda expone buy_volume/sell_volume. Son
    lo mismo visto desde los dos lados del cruce:

      ask_vol[P] = volumen negociado CONTRA el ask en P = agresion COMPRADORA en P
                 = cells[i].buy_volume
      bid_vol[P] = volumen negociado CONTRA el bid en P = agresion VENDEDORA en P
                 = cells[i].sell_volume

    Es la misma convencion que ya usa absorption (side ASK cuando delta > 0, es decir
    cuando manda la agresion compradora).

    IMBALANCE DE COMPRA en el nivel i: buy_volume[i] >= ratio * sell_volume[i-1], con
    buy_volume[i] > 0. El `> 0` NO es adorno: sin el, un nivel vacio contra otro vacio
    daria 0 >= 0 == True y una barra muerta puntuaria como pila perfecta. Con el
    denominador a 0 y numerador vivo SI cuenta (agresion sin nada enfrente es el caso
    mas desequilibrado que hay), que es lo que fija el pre-registro.

    IMBALANCE DE VENTA en el nivel i: sell_volume[i] >= ratio * buy_volume[i+1], con
    sell_volume[i] > 0. Simetrico: mira al nivel de ARRIBA.

    Se devuelve la corrida MAXIMA de niveles consecutivos por lado, no la ultima ni la
    suma: "apilado" es precisamente que el desequilibrio se sostenga por varios niveles
    seguidos, y la mas larga es la que mejor describe la barra.

    ADYACENCIA POR CELDA, NO POR TICK: el contrato garantiza celdas ascendentes y sin
    nivel repetido, pero NO que no haya huecos -- un precio sin ningun trade no genera
    celda y el payload no lleva el tick size --. Asi que "consecutivo" es consecutivo
    entre celdas CON ACTIVIDAD. La alternativa (tratar el hueco como volumen 0) volveria
    automaticamente desequilibrado todo nivel por encima de un hueco; esta es la lectura
    conservadora.

    Sin celdas o con una sola -> (0, 0): no hay pareja diagonal que comparar.
    """
    if len(cells) < 2:
        return Decimal(0), Decimal(0)

    mejor_compra = 0
    mejor_venta = 0
    corrida_compra = 0
    corrida_venta = 0
    for i, celda in enumerate(cells):
        # Compra: contra el nivel de ABAJO (existe para todo i > 0).
        if (
            i > 0
            and celda.buy_volume > 0
            and (celda.buy_volume >= ratio * cells[i - 1].sell_volume)
        ):
            corrida_compra += 1
            mejor_compra = max(mejor_compra, corrida_compra)
        else:
            corrida_compra = 0
        # Venta: contra el nivel de ARRIBA (existe para todo i < len - 1).
        if (
            i < len(cells) - 1
            and celda.sell_volume > 0
            and (celda.sell_volume >= ratio * cells[i + 1].buy_volume)
        ):
            corrida_venta += 1
            mejor_venta = max(mejor_venta, corrida_venta)
        else:
            corrida_venta = 0

    return (
        _stack_strength(mejor_compra, min_stack),
        _stack_strength(mejor_venta, min_stack),
    )


def _decimal_param(name: str, default: Decimal) -> ParamSpec:
    """ParamSpec decimal con su semilla del pre-registro."""
    return ParamSpec(
        name=name,
        value_type=ScalarType.DECIMAL,
        default=ScalarValue(scalar_type=ScalarType.DECIMAL, decimal_value=default),
    )


def _int_param(name: str, default: int) -> ParamSpec:
    """ParamSpec entero con su semilla del pre-registro."""
    return ParamSpec(
        name=name,
        value_type=ScalarType.INTEGER,
        default=ScalarValue(scalar_type=ScalarType.INTEGER, integer_value=default),
    )


def _imbalance_declaration(source_id: str) -> DataSourceDeclaration:
    """Declaracion comun de las dos imbalance.* (P08c-CONF-05).

    POINT_LOCAL: ver el docstring del modulo. consumes = market.footprint y nada mas:
    el nucleo solo mira celdas, no toca OHLC, asi que declarar candle.* seria arista
    muerta del DAG (mismo criterio que excluyo candle.open de climax.* en DET-01 b).
    market.footprint es NON_SERVIBLE (un conjunto de celdas no es un escalar), asi que
    no se pide por dispatch: el materializador lo lee por su cuenta, el patron de void
    con select_lvn_price y de MACD con sus EMAs.

    overridable_params vacio: las dos semillas entran en la cache_key y no se dejan
    mover por llamada; el dia que la calibracion las toque, cambia la clave y con ella
    la identidad del valor cacheado.
    """
    return DataSourceDeclaration(
        source_id=source_id,
        source_type=SourceType.OBSERVABLE,
        servibility=Servibility.CONTINUOUS,
        memory_model=MemoryModel.POINT_LOCAL,
        value_type=ScalarType.DECIMAL,
        evaluation_contexts=tuple(tf.value for tf in Timeframe),
        history_units=(HistoryUnit.BARS,),
        params=(
            _decimal_param("imbalance_ratio", IMBALANCE_RATIO),
            _int_param("min_stack", MIN_STACK),
        ),
        overridable_params=(),
        shared_evaluation=True,
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=_IMBALANCE_CACHE_KEY_SCHEMA,
        consumes=(MARKET_FOOTPRINT_SOURCE_ID,),
    )


def imbalance_buy_stack_declaration() -> DataSourceDeclaration:
    """imbalance.buy_stack: fuerza [0,1] de la pila COMPRADORA de la barra."""
    return _imbalance_declaration(IMBALANCE_BUY_STACK_SOURCE_ID)


def imbalance_sell_stack_declaration() -> DataSourceDeclaration:
    """imbalance.sell_stack: fuerza [0,1] de la pila VENDEDORA de la barra."""
    return _imbalance_declaration(IMBALANCE_SELL_STACK_SOURCE_ID)


def declarations() -> tuple[DataSourceDeclaration, ...]:
    """Declaraciones que este modulo publica al catalogo vivo (discovery)."""
    return (
        imbalance_buy_stack_declaration(),
        imbalance_sell_stack_declaration(),
    )
