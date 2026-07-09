"""Watermark basico y politicas temporales por stream (ADR-007).

El watermark AVANZADO de replay es P03; aqui solo el modelo basico: hasta
que instante un stream se considera completo, y las politicas de evento
tardio / fuera de orden declaradas por stream o consumidor.
"""

from pydantic import BaseModel, ConfigDict, Field

from source.time.enums import LateEventPolicy, OutOfOrderPolicy
from source.time.timestamp import EpochMillis


class Watermark(BaseModel):
    """Marca de agua basica de un stream (ADR-007).

    watermark_time es el instante (UTC epoch ms) hasta el cual el stream
    identificado por stream_key se considera completo: eventos con tiempo
    anterior o igual ya no deberian llegar salvo correccion.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    stream_key: str = Field(min_length=1)
    watermark_time: EpochMillis


class StreamTimePolicy(BaseModel):
    """Politicas temporales declaradas por un stream o consumidor (ADR-007)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    stream_key: str = Field(min_length=1)
    late_event_policy: LateEventPolicy
    out_of_order_policy: OutOfOrderPolicy
