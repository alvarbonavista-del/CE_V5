"""Registro local del connector real de Bybit v5 Spot (T-03).

El 'kind' y la factory viven junto al adaptador. El composition root las enchufa; aqui
no se conoce el puerto (infra no importa platform: el connector lo satisface por FORMA).
"""

from __future__ import annotations

from ce_v5.infra.connectors.bybit.connector import BybitSpotConnector

KIND = "bybit"


def create() -> BybitSpotConnector:
    """Construye el connector REAL de Bybit v5 Spot (feed publico, sin credenciales)."""
    return BybitSpotConnector()
