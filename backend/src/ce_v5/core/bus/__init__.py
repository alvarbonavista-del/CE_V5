"""EventBus port: broker-agnostic event transport (ADR-013).

Public surface of ``ce_v5.core.bus``. Concrete adapters (Redis Streams)
live under ``ce_v5.infra`` and are wired at the composition root.
"""

from __future__ import annotations

from ce_v5.core.bus.errors import (
    BusError,
    ConsumeError,
    PublishError,
    UnknownOffsetError,
)
from ce_v5.core.bus.message import (
    BusMessage,
    Delivery,
    DlqReason,
    Offset,
    ReceivedMessage,
)
from ce_v5.core.bus.ports import EventBus

__all__ = [
    "BusError",
    "BusMessage",
    "ConsumeError",
    "Delivery",
    "DlqReason",
    "EventBus",
    "Offset",
    "PublishError",
    "ReceivedMessage",
    "UnknownOffsetError",
]
