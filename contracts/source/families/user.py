"""Familia user.* : hechos del ciclo de vida de una cuenta (ADR-004).

La familia user.* ya estaba CERRADA en ADR-004 desde el principio; lo que no existia era
ninguno de sus tipos. Este es el primero, y lo emite P06b.

SIN DATOS PERSONALES EN EL PAYLOAD: el email NO viaja en el evento. Un evento se publica
en un bus, lo consumen procesos que hoy no existen y acaba en logs y en replays. Meter
ahi un email seria repartir datos personales por todo el sistema para siempre. El evento
dice QUE paso y A QUIEN (por identificador), no quien es.
"""

from enum import StrEnum

from source.envelope import EventPayload


class UserEventType(StrEnum):
    """Tipos de evento user.* (ADR-004)."""

    REGISTERED = "user.registered"


class UserRegisteredPayload(EventPayload):
    """Una cuenta nueva existe, con su tenant ya resuelto (alta atomica).

    Ni email ni ningun otro dato personal: solo los identificadores.
    """

    user_id: str
    tenant_id: str
