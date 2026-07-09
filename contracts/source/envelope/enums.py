"""Enums del envelope canonico (ADR-003)."""

from enum import StrEnum


class Scope(StrEnum):
    """Alcance del evento (ADR-003)."""

    PUBLIC_MARKET = "public_market"
    TENANT = "tenant"
    USER = "user"
    SYSTEM = "system"
