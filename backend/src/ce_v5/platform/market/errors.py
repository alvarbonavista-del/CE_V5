"""Errores y motivos de rechazo del registro de intereses (ADR-014, ADR-016).

Cada motivo existe por una razon concreta:

- UNKNOWN_EXCHANGE: se pide un flujo de un exchange que no conocemos; sin adaptador
  no hay nadie que lo traiga.
- UNKNOWN_INSTRUMENT: NO ES UNA COMODIDAD, ES UN CONTROL DE SEGURIDAD. Sin catalogo,
  cualquiera podria fabricar MarketStreamKeys arbitrarios y abrir streams infinitos:
  un DoS por cardinalidad, gratis y desde la puerta publica.
- INSTRUMENT_INACTIVE: el par existio pero esta delistado; suscribirse a el gastaria
  una conexion al exchange para no recibir nunca un dato.
- UNSUPPORTED_INTERVAL: el timeframe es valido en el vocabulario canonico pero ESE
  exchange no lo sirve; suponer que todos soportan lo mismo es el error que Central
  advirtio al prohibir copiar el barrido de un exchange a otro.
- SUBJECT_LIMIT_EXCEEDED: tope TECNICO de supervivencia por sujeto (no es la cuota
  comercial del plan, que es P11 + el gate).

El reason_code es DATO, no texto libre: la UI lo renderiza por i18n (ADR-016) y
jamas muestra una cadena hardcodeada del backend.
"""

from enum import StrEnum


class IntentRejectionReason(StrEnum):
    """Motivo por el que se rechaza un interes. Conjunto CERRADO (ADR-016)."""

    UNKNOWN_EXCHANGE = "unknown_exchange"
    UNKNOWN_INSTRUMENT = "unknown_instrument"
    INSTRUMENT_INACTIVE = "instrument_inactive"
    UNSUPPORTED_INTERVAL = "unsupported_interval"
    SUBJECT_LIMIT_EXCEEDED = "subject_limit_exceeded"


class MarketError(RuntimeError):
    """Error de la plataforma de market data (ADR-014)."""


class IntentRejected(MarketError):
    """El interes no se admite. Lleva el motivo como DATO, no como texto."""

    def __init__(self, reason: IntentRejectionReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason
