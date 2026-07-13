"""Refresh token opaco y rotatorio (P06b, ADR-019).

EL TOKEN NUNCA SE GUARDA: en la base vive solo su HUELLA (SHA-256). Si alguien se
llevara la base entera, esas huellas NO le sirven para suplantar a nadie, porque de
la huella no se vuelve al token.

POR QUE SHA-256 Y NO ARGON2 AQUI: Argon2 es lento a proposito porque una contrasena
humana es adivinable. Este token NO es una contrasena: son 32 bytes aleatorios de un
generador criptografico, imposibles de adivinar por fuerza bruta. Meterle un hash
lento no anadiria seguridad y convertiria cada refresh en un coste artificial (un
autoataque de denegacion de servicio).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

_TOKEN_BYTES = 32


def hash_refresh_token(raw_token: str) -> str:
    """Huella del token que se guarda en la base. Nunca se guarda el token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RefreshToken:
    """Un refresh token recien emitido: lo que se entrega y lo que se guarda."""

    raw: str
    """Se entrega al cliente en cookie httpOnly. JAMAS se guarda ni se registra."""

    hash: str
    """Se guarda en la base. JAMAS se entrega al cliente."""


def new_refresh_token() -> RefreshToken:
    """Genera un refresh token aleatorio y su huella."""
    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    return RefreshToken(raw=raw, hash=hash_refresh_token(raw))
