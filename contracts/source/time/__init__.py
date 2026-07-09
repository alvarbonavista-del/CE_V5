"""Modelo temporal canonico (ADR-007): tiempo en UTC epoch ms, enums de
madurez y politicas, watermark basico. La semantica de asignacion e
herencia de los tres tiempos vive con el envelope (ADR-003) y el Clock
inyectable en backend core/clock. No contiene logica que produzca o
consuma eventos.
"""

from source.time.enums import LateEventPolicy, MaturityState, OutOfOrderPolicy
from source.time.timestamp import EpochMillis, to_iso8601
from source.time.watermark import StreamTimePolicy, Watermark

__all__ = [
    "EpochMillis",
    "LateEventPolicy",
    "MaturityState",
    "OutOfOrderPolicy",
    "StreamTimePolicy",
    "Watermark",
    "to_iso8601",
]
