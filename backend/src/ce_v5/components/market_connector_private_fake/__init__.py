"""Connector privado BYOC (FAKE): entrypoint del Componente (ADR-009)."""

from ce_v5.components.market_connector_private_fake.component import (
    ConnectorStatus,
    PrivateMarketConnectorFake,
    build,
)

__all__ = ["ConnectorStatus", "PrivateMarketConnectorFake", "build"]
