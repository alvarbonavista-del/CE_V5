"""Registro local del connector real de OKX Spot (T-03).

El 'kind' y la factory viven junto al adaptador, en su propia carpeta. El composition
root las enchufa; aqui no se conoce el puerto (infra no importa platform: el connector
satisface MarketDataSourcePort por FORMA).
"""

from __future__ import annotations

from ce_v5.infra.connectors.okx.connector import OkxSpotConnector

KIND = "okx"


def create() -> OkxSpotConnector:
    """Construye el connector REAL de OKX Spot (feed publico, sin credenciales)."""
    return OkxSpotConnector()
