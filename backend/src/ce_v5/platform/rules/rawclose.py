"""DataSource de demostracion: precio de cierre crudo (market.close) de candle_closed.

Es la DataSource mas simple que demuestra el marco (ADR-008): continua, unidad bars,
value_type decimal, derivada de market.candle_closed. Se evalua SOBRE la vela CERRADA,
jamas provisional (invariante CA-P07-A). Los cuatro indicadores y el catalogo de
paridad-v4 NO son P08 (los disena I-02); esta es solo la demostracion del marco.

close_window extrae la ventana de cierres de una secuencia de velas cerradas del MISMO
flujo, ordenadas oldest->newest. El motor la pasa a las funciones canonicas continuas.
"""

from collections.abc import Sequence
from decimal import Decimal

from source.datasource import (
    DataSourceDeclaration,
    HistoryUnit,
    MemoryModel,
    Servibility,
    SharingScope,
    SourceType,
)
from source.families.market import CandleClosedPayload, Timeframe
from source.rules.scalar import ScalarType

MARKET_CLOSE_SOURCE_ID = "market.close"


def market_close_declaration() -> DataSourceDeclaration:
    """Declaracion de la DataSource de precio de cierre crudo (market.close)."""
    return DataSourceDeclaration(
        source_id=MARKET_CLOSE_SOURCE_ID,
        source_type=SourceType.OBSERVABLE,
        servibility=Servibility.CONTINUOUS,
        # POINT_LOCAL: el cierre de la barra T es el dato crudo de la barra T y no
        # depende de T-1. Por eso una correccion de T se propaga por VENTANA acotada
        # (CA-P08-08): es la unica clase de fuente que v5.0 sabe corregir.
        memory_model=MemoryModel.POINT_LOCAL,
        value_type=ScalarType.DECIMAL,
        evaluation_contexts=tuple(tf.value for tf in Timeframe),
        history_units=(HistoryUnit.BARS,),
        shared_evaluation=True,
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=("exchange", "symbol", "timeframe"),
    )


class CloseWindowError(RuntimeError):
    """La secuencia de velas no forma una ventana valida de un solo flujo."""


def close_window(candles: Sequence[CandleClosedPayload]) -> tuple[Decimal, ...]:
    """Ventana de cierres (oldest->newest) de un UNICO flujo de velas cerradas.

    Exige un solo flujo (mismo exchange/symbol/timeframe) y orden estricto creciente por
    open_time: una ventana mezclada o desordenada seria un dato corrupto, no un window.
    """
    if not candles:
        return ()
    first = candles[0]
    key = (first.exchange, first.symbol, first.timeframe)
    previous_open: int | None = None
    closes: list[Decimal] = []
    for candle in candles:
        if (candle.exchange, candle.symbol, candle.timeframe) != key:
            msg = "close_window exige un unico flujo (exchange/symbol/timeframe)."
            raise CloseWindowError(msg)
        if previous_open is not None and candle.open_time <= previous_open:
            msg = "close_window exige velas ordenadas por open_time creciente."
            raise CloseWindowError(msg)
        previous_open = candle.open_time
        closes.append(candle.close)
    return tuple(closes)
