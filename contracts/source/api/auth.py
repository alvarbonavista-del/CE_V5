"""Contratos de autenticacion de la API (P06b, ADR-019).

EL REFRESH TOKEN NO APARECE EN NINGUNA RESPUESTA, Y ESO ES DELIBERADO: viaja en una
cookie httpOnly que el JavaScript no puede leer (ADR-019, regla dura). Si un dia alguien
intentase devolverlo en el JSON, el schema NO SE LO PERMITIRIA: los modelos prohiben
campos extra. La regla no vive en la buena memoria de nadie: vive en el contrato.

EL TENANT TAMPOCO ENTRA NUNCA EN UNA PETICION: lo resuelve el backend desde la
pertenencia del usuario autenticado (ADR-011, obligacion vinculante de P05). El cliente
no puede pedir "quiero ser este tenant".
"""

from pydantic import BaseModel, ConfigDict, Field

_MIN_PASSWORD_LENGTH = 12
_MAX_PASSWORD_LENGTH = 128


class RegisterRequest(BaseModel):
    """Alta de una cuenta nueva."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(
        min_length=_MIN_PASSWORD_LENGTH, max_length=_MAX_PASSWORD_LENGTH
    )
    """Longitud minima de 12: una clave corta se rompe por fuerza bruta aunque el hash
    sea bueno. Maximo de 128 para que nadie fuerce al servidor a hashear un texto
    gigante (seria un ataque de denegacion de servicio barato)."""


class LoginRequest(BaseModel):
    """Entrada al sistema con email y contrasena."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=_MAX_PASSWORD_LENGTH)
    """Sin minimo de longitud al ENTRAR: rechazar por corta una clave equivocada le
    diria al atacante algo sobre la clave real. Al entrar solo se comprueba si
    acierta."""


class SessionResponse(BaseModel):
    """Lo que se devuelve al entrar o renovar. SIN refresh token: va en cookie."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int = Field(ge=1)
    user_id: str


class MeResponse(BaseModel):
    """Quien eres y en que tenant operas, SEGUN EL BACKEND.

    El tenant no lo mando el cliente: lo resolvio el backend desde la pertenencia.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str
    tenant_id: str
