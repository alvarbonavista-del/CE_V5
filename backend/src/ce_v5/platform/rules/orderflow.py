"""Orderflow determinista desde footprint: delta y delta_momentum (P08c).

Fuentes DERIVADAS de market.footprint. DETERMINISTAS (no detectores): DEC-AHP-01 NO
aplica -- como vp.poc/vah/val, su prueba es formula + fixtures, no un analisis
historico previo. El SCORE compuesto de orderflow (impulso/absorcion/agotamiento,
0-100, con pesos) SI es detector estadistico: va con su AHP en la capa de detectores,
NO aqui.

- orderflow.delta [PARIDAD v4]: delta de la barra = volumen agresor comprador - vendedor
  (el bar_delta del footprint). POINT_LOCAL: el delta de T sale SOLO de los trades de T.
- orderflow.delta_momentum [PARIDAD v4]: cambio del delta entre barras consecutivas,
  delta[T] - delta[T-1] (v4: delta - delta_prev). WINDOWED: depende de la ventana
  [T-1, T], no de su propio valor anterior.

ADITIVO como rawfootprint/vp: solo declara las fuentes y da el compute puro; el registro
en el catalogo vivo del worker y el materializer son del paso del SourceMaterializer +
CE-14, no de aqui.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from ce_v5.platform.rules.rawfootprint import MARKET_FOOTPRINT_SOURCE_ID
from source.datasource import (
    DataSourceDeclaration,
    HistoryUnit,
    MemoryModel,
    Servibility,
    SharingScope,
    SourceType,
)
from source.families.market import Timeframe
from source.rules.scalar import ScalarType

if TYPE_CHECKING:
    from collections.abc import Sequence

ORDERFLOW_DELTA_SOURCE_ID = "orderflow.delta"
ORDERFLOW_DELTA_MOMENTUM_SOURCE_ID = "orderflow.delta_momentum"

# Dimensiones de la cache_key: las cuatro del footprint del que derivan. Sin parametros
# (delta y delta_momentum no tienen tunables) y sin formula_version (agregacion lossless
# determinista, como el footprint).
_ORDERFLOW_CACHE_KEY_SCHEMA: tuple[str, ...] = (
    "exchange",
    "symbol",
    "market_type",
    "timeframe",
)


def orderflow_delta_declaration() -> DataSourceDeclaration:
    """orderflow.delta: delta agresor por barra (bar_delta del footprint)."""
    return DataSourceDeclaration(
        source_id=ORDERFLOW_DELTA_SOURCE_ID,
        source_type=SourceType.OBSERVABLE,
        servibility=Servibility.CONTINUOUS,
        # POINT_LOCAL: el delta de la barra T sale SOLO de los trades de T (como el
        # footprint del que deriva); una correccion de T se propaga por ventana acotada.
        memory_model=MemoryModel.POINT_LOCAL,
        value_type=ScalarType.DECIMAL,
        evaluation_contexts=tuple(tf.value for tf in Timeframe),
        history_units=(HistoryUnit.BARS,),
        shared_evaluation=True,
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=_ORDERFLOW_CACHE_KEY_SCHEMA,
        consumes=(MARKET_FOOTPRINT_SOURCE_ID,),
    )


def orderflow_delta_momentum_declaration() -> DataSourceDeclaration:
    """orderflow.delta_momentum: cambio del delta entre barras, delta[T]-delta[T-1]."""
    return DataSourceDeclaration(
        source_id=ORDERFLOW_DELTA_MOMENTUM_SOURCE_ID,
        source_type=SourceType.OBSERVABLE,
        servibility=Servibility.CONTINUOUS,
        # WINDOWED: el valor de T depende de la ventana [T-1, T] (delta de T y de T-1),
        # no de su propio valor anterior -> NO-CONFORME para correccion en v5.0, pero
        # recomputable por ventana acotada (como fija el enum MemoryModel).
        memory_model=MemoryModel.WINDOWED,
        value_type=ScalarType.DECIMAL,
        evaluation_contexts=tuple(tf.value for tf in Timeframe),
        history_units=(HistoryUnit.BARS,),
        shared_evaluation=True,
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=_ORDERFLOW_CACHE_KEY_SCHEMA,
        # Deriva de la serie de delta: el DAG es footprint -> delta -> delta_momentum.
        consumes=(ORDERFLOW_DELTA_SOURCE_ID,),
    )


def compute_delta_momentum(deltas: Sequence[Decimal]) -> tuple[Decimal, ...]:
    """Cambio del delta entre barras consecutivas (oldest->newest), [PARIDAD v4].

    momentum[i] = delta[i] - delta[i-1]. El primer elemento no tiene barra previa en la
    ventana: momentum[0] = 0 (paridad v4, donde para la primera vela delta_prev = delta,
    asi que el momento es cero). Serie de la MISMA longitud que la entrada, alineada
    barra a barra. Ventana vacia -> tupla vacia.
    """
    if not deltas:
        return ()
    momentum: list[Decimal] = [Decimal(0)]
    momentum.extend(
        current - previous
        for previous, current in zip(deltas, deltas[1:], strict=False)
    )
    return tuple(momentum)
