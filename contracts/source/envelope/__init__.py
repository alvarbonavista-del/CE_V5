"""Envelope canonico unico (ADR-003)."""

from envelope.enums import Scope
from envelope.envelope import ENVELOPE_VERSION, Envelope
from envelope.payload import EventPayload

__all__ = ["ENVELOPE_VERSION", "Envelope", "EventPayload", "Scope"]
