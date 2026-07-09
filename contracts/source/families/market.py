"""Tipos de evento de vela de la familia market (ADR-007).

Taxonomia de los tres tipos de vela y su semantica de madurez:
- market.candle_updated   -> vela provisional (en formacion).
- market.candle_closed    -> vela cerrada (definitiva del intervalo).
- market.candle_corrected -> correccion de una vela ya cerrada.

El payload concreto (OHLCV, timeframe) lo define la ingesta de mercado
(P07) extendiendo MaturityAwarePayload; aqui solo vive la taxonomia de
tipos, coherente con ADR-004 (la accion la nombra su familia).
"""

from enum import StrEnum


class MarketCandleEventType(StrEnum):
    """Tipos de evento de vela (market.*), ADR-007."""

    CANDLE_UPDATED = "market.candle_updated"
    CANDLE_CLOSED = "market.candle_closed"
    CANDLE_CORRECTED = "market.candle_corrected"
