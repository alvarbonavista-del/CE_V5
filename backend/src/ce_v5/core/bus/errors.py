"""EventBus port errors (ADR-013)."""

from __future__ import annotations


class BusError(Exception):
    """Base class for all EventBus errors."""


class PublishError(BusError):
    """A message could not be appended to the bus."""


class ConsumeError(BusError):
    """A message could not be fetched, claimed, acked or dead-lettered."""


class UnknownOffsetError(BusError):
    """Replay was asked to start from an offset no longer retained.

    Surfaced explicitly so the caller can rebuild from the canonical
    history in the DB or move the instance to FAILED/QUARANTINED, never
    advancing in silence (ADR-013).
    """
