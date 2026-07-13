"""Hash de contrasenas con Argon2id (P06b).

Argon2id gano el Password Hashing Competition y es el estandar recomendado: es LENTO
y COSTOSO EN MEMORIA a proposito, para que probar millones de contrasenas robadas sea
caro. Se usan los parametros por defecto de la libreria (alineados con la RFC 9106).

Este modulo vive en el NUCLEO porque el hash es una funcion PURA (entra un texto, sale
otro): no toca disco, ni red, ni base de datos. La contrasena en claro no sale nunca
de aqui: la base de datos solo ve el hash (CA-07 p.6).

verify() devuelve False en lugar de propagar: un hash corrupto o una contrasena
equivocada son la MISMA respuesta para quien pregunta ("no", sin pistas).
"""

from __future__ import annotations

from argon2 import PasswordHasher as Argon2Backend
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)


class Argon2PasswordHasher:
    """Implementacion del puerto PasswordHasher con Argon2id."""

    def __init__(self, backend: Argon2Backend | None = None) -> None:
        self._backend = Argon2Backend() if backend is None else backend

    def hash(self, password: str) -> str:
        """Hash Argon2id de la contrasena. Nunca reversible."""
        return self._backend.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        """True si la contrasena corresponde al hash; False en cualquier otro caso."""
        try:
            self._backend.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
        return True
