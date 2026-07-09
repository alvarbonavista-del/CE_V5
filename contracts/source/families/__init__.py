"""Familias dominio.accion (ADR-004): taxonomia base cerrada."""

from source.families.families import EVENT_TYPE_PATTERN, Family, validate_event_type

__all__ = ["EVENT_TYPE_PATTERN", "Family", "validate_event_type"]
