"""Contrato de error de la API (P06b, ADR-006).

Un codigo ESTABLE que el cliente puede interpretar, y un mensaje para el humano. El
mensaje NUNCA revela por que fallo una autenticacion (si el email existe, si la clave
era corta...): esas pistas son un regalo para quien ataca.
"""

from pydantic import BaseModel, ConfigDict


class ApiError(BaseModel):
    """Error devuelto por la API."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
