"""Contrato del canal realtime (P06b, ADR-019: RealtimeAuth y RealtimeCheckpoint).

EL TOKEN NO VIAJA EN LA URL, Y ESO ES DELIBERADO: las URLs quedan escritas en los logs
del servidor, en el historial del navegador y en la cabecera Referer que se manda a
terceros. Un token en la URL es un token publicado. Se manda en el PRIMER MENSAJE de la
conexion, que no queda registrado en ninguno de esos sitios.

EL CLIENTE NO IMPONE IDENTIDAD NI TENANT: los mensajes NO tienen (ni pueden tener)
campos user_id o tenant_id. El backend los deriva de la sesion verificada (obligacion
vinculante de P05/P06). Como los modelos prohiben campos extra, un cliente que lo
intente es rechazado por el propio contrato.

CHECKPOINT: el cliente dice por donde se quedo (un offset del bus) y el servidor reanuda
desde ahi. Sin checkpoint, una reconexion perderia eventos o los repetiria.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class RealtimeAuth(BaseModel):
    """Primer mensaje: la sesion. El token JAMAS en la URL."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["auth"]
    access_token: str


class RealtimeSubscribe(BaseModel):
    """Suscripcion a un topic, opcionalmente desde un checkpoint."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["subscribe"]
    topic: str
    checkpoint: str | None = None


class RealtimeAck(BaseModel):
    """Confirmacion del servidor: suscrito, y desde donde."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["ack"]
    topic: str
    checkpoint: str | None


class RealtimeErrorMessage(BaseModel):
    """Error del canal. Codigo estable; mensaje sin pistas."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["error"]
    code: str
    message: str


class RealtimeEvent(BaseModel):
    """Un evento del bus. El envelope se entrega TAL CUAL (ADR-013).

    El cliente consume el envelope canonico y no inventa campos: es el mismo
    contrato que viaja por el bus, no una version recortada que habria que mantener en
    dos sitios.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["event"]
    topic: str
    checkpoint: str
    envelope: dict[str, object]
