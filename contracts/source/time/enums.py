"""Enums del modelo temporal (ADR-007)."""

from enum import StrEnum


class MaturityState(StrEnum):
    """Estado de madurez de un dato temporal (ADR-007).

    Se modela en el schema de las familias que lo necesitan (market.*,
    datasource.*), NO como campo universal del envelope.
    """

    PROVISIONAL = "provisional"
    CLOSED = "closed"
    CORRECTION = "correction"
    REEMISSION = "reemission"


class LateEventPolicy(StrEnum):
    """Politica ante un evento que llega tras el watermark (ADR-007)."""

    ACCEPT = "accept"
    REJECT_AFTER_WATERMARK = "reject_after_watermark"
    ROUTE_TO_CORRECTION = "route_to_correction"


class OutOfOrderPolicy(StrEnum):
    """Politica ante eventos fuera de orden en un stream (ADR-007)."""

    REORDER_BY_SEQUENCE = "reorder_by_sequence"
    BEST_EFFORT = "best_effort"
    DROP_OLDER = "drop_older"
