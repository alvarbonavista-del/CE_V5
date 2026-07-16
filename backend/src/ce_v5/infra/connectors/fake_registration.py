"""Registro local del datasource FAKE (arranque local sin red, T-03-A).

No produce datos reales: existe para ver que el worker levanta sin tocar la red.
"""

from __future__ import annotations

from ce_v5.infra.connectors.fake_market import FakeMarketDataSource

KIND = "fake"


def create() -> FakeMarketDataSource:
    """Construye el FakeMarketDataSource vacio (jamas datos reales)."""
    return FakeMarketDataSource()
