"""Rango de precio del footprint por barra (footprint.price_range, P08c P5 T4a).

Escalar derivado del footprint: el SPAN de precio de la barra = max - min del precio de
sus celdas. POINT_LOCAL (el valor de T depende SOLO del footprint de T, como
orderflow.delta): una correccion de la barra invalida una ventana acotada, recomputable.
Lo consume F4 del modelo de confianza de pivotphase (|delta| / rango = esfuerzo vs
resultado) y lo reaprovechara absorption cuando se active (DICTAMEN P08c-PIVOT-06 Q1).
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
    Servibility,
    SharingScope,
    SourceType,
)
from source.families.market import Timeframe
from source.rules.scalar import ScalarType

if TYPE_CHECKING:
    from source.families.footprint import FootprintPayload

FOOTPRINT_PRICE_RANGE_SOURCE_ID = "footprint.price_range"

# Dimensiones de la cache_key: las cuatro del footprint del que deriva. Sin parametros.
_FOOTPRINT_RANGE_CACHE_KEY_SCHEMA: tuple[str, ...] = (
    "exchange",
    "symbol",
    "market_type",
    "timeframe",
)


def price_range(footprint: FootprintPayload) -> Decimal:
    """Span de precio de la barra: max - min del precio de las celdas del footprint.

    Sin celdas -> 0 (barra sin niveles); una sola celda -> 0. Es el extract POINT_LOCAL
    que consume F4 (|delta| / rango). Determinista.
    """
    prices = [cell.price for cell in footprint.cells]
    if not prices:
        return Decimal(0)
    return max(prices) - min(prices)


def footprint_price_range_declaration() -> DataSourceDeclaration:
    """footprint.price_range: span de precio del footprint por barra (POINT_LOCAL)."""
    return DataSourceDeclaration(
        source_id=FOOTPRINT_PRICE_RANGE_SOURCE_ID,
        source_type=SourceType.OBSERVABLE,
        servibility=Servibility.CONTINUOUS,
        memory_model=MemoryModel.POINT_LOCAL,
        value_type=ScalarType.DECIMAL,
        evaluation_contexts=tuple(tf.value for tf in Timeframe),
        history_units=(HistoryUnit.BARS,),
        shared_evaluation=True,
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=_FOOTPRINT_RANGE_CACHE_KEY_SCHEMA,
        consumes=(MARKET_FOOTPRINT_SOURCE_ID,),
    )


def declarations() -> tuple[DataSourceDeclaration, ...]:
    """Declaraciones que este modulo publica al catalogo vivo (discovery)."""
    return (footprint_price_range_declaration(),)
