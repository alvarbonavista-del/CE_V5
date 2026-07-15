"""Ingestor de market data publico: entrypoint del Componente (ADR-009)."""

from ce_v5.components.market_ingestor_public.component import (
    PublicMarketIngestorComponent,
    TickReport,
    build,
)

__all__ = ["PublicMarketIngestorComponent", "TickReport", "build"]
