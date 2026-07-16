"""Pruebas del ConnectorRegistry (T-03-A): mata el if-chain con un registro minimo,
fail-loud, que devuelve el puerto MarketDataSourcePort.
"""

from __future__ import annotations

import pytest

from ce_v5.entrypoints.worker_ingestion.connector_registry import (
    ConnectorRegistry,
    DuplicateConnectorKindError,
    UnknownConnectorKindError,
    build_default_registry,
)
from ce_v5.infra.connectors.binance.connector import BinanceSpotConnector
from ce_v5.infra.connectors.fake_market import FakeMarketDataSource

_PORT_METHODS = (
    "open",
    "close",
    "active",
    "poll",
    "fetch_recent",
    "list_instruments",
    "drain_reconnected",
    "supported_timeframes",
)


def test_resuelve_binance_por_kind() -> None:
    source = build_default_registry().resolve("binance")
    assert isinstance(source, BinanceSpotConnector)


def test_resuelve_fake_por_kind() -> None:
    source = build_default_registry().resolve("fake")
    assert isinstance(source, FakeMarketDataSource)


def test_kind_desconocido_falla_fuerte() -> None:
    with pytest.raises(UnknownConnectorKindError) as exc:
        build_default_registry().resolve("noexiste")
    assert "noexiste" in str(exc.value)


def test_colision_de_kind_rompe() -> None:
    registry = ConnectorRegistry()
    registry.register("dup", FakeMarketDataSource)
    with pytest.raises(DuplicateConnectorKindError):
        registry.register("dup", FakeMarketDataSource)


def test_resuelto_satisface_el_puerto() -> None:
    source = build_default_registry().resolve("binance")
    for metodo in _PORT_METHODS:
        assert callable(getattr(source, metodo))


def test_registro_por_defecto_expone_binance_y_fake() -> None:
    assert build_default_registry().kinds() == frozenset({"binance", "fake"})
