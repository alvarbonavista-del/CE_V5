"""Puertos del nucleo de autenticacion (P06b). Sin infraestructura."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

ACTIVE_STATUS = "active"


@runtime_checkable
class PasswordHasher(Protocol):
    """Hash irreversible de una contrasena y su verificacion."""

    def hash(self, password: str) -> str: ...

    def verify(self, password_hash: str, password: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class RegisteredUser:
    """Usuario recien creado, con su tenant ya resuelto por el backend."""

    user_id: UUID
    tenant_id: UUID


@runtime_checkable
class UserRegistrar(Protocol):
    """Alta ATOMICA de usuario + tenant + pertenencia."""

    def register(self, email: str, password_hash: str) -> RegisteredUser: ...


@dataclass(frozen=True, slots=True)
class StoredCredential:
    """Lo MINIMO que la ventanilla devuelve de un usuario para poder autenticarlo."""

    user_id: UUID
    password_hash: str
    status: str


class RotationOutcome(StrEnum):
    """Desenlace de un intento de rotacion de refresh token."""

    ROTATED = "rotated"
    INVALID = "invalid"
    EXPIRED = "expired"
    REUSE_DETECTED = "reuse_detected"


@dataclass(frozen=True, slots=True)
class RotationResult:
    """Resultado de rotar: que paso y, si hubo suerte, para quien."""

    outcome: RotationOutcome
    user_id: UUID | None = None
    session_id: UUID | None = None


@runtime_checkable
class CredentialReader(Protocol):
    """Lectura de la credencial de UN email. Nunca enumera usuarios."""

    def credential_for_email(self, email: str) -> StoredCredential | None: ...


@runtime_checkable
class SessionStore(Protocol):
    """Almacen de sesiones de refresh. Guarda HUELLAS, nunca tokens."""

    def create_session(
        self, user_id: UUID, refresh_token_hash: str, expires_at_ms: int
    ) -> UUID: ...

    def rotate_session(
        self,
        refresh_token_hash: str,
        new_refresh_token_hash: str,
        expires_at_ms: int,
    ) -> RotationResult: ...

    def revoke_family(self, refresh_token_hash: str) -> int: ...
