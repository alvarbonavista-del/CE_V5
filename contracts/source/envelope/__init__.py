"""Envelope canonico unico (ADR-003)."""

from source.envelope.enums import Scope
from source.envelope.envelope import ENVELOPE_VERSION, Envelope
from source.envelope.payload import EventPayload

__all__ = ["ENVELOPE_VERSION", "Envelope", "EventPayload", "Scope"]
