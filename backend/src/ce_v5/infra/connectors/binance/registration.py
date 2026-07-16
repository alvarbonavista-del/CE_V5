"""Registro local del connector real de Binance Spot (T-03-A).

El 'kind' y la factory viven junto al adaptador, en su propia carpeta. El composition
root las enchufa; aqui no se conoce el puerto (el contrato de capas prohibe que infra
importe platform: el connector satisface MarketDataSourcePort por FORMA).
"""

from __future__ import annotations

from ce_v5.infra.connectors.binance.connector import BinanceSpotConnector

KIND = "binance"


def create() -> BinanceSpotConnector:
    """Construye el connector REAL de Binance Spot (feed publico, sin credenciales)."""
    return BinanceSpotConnector()
