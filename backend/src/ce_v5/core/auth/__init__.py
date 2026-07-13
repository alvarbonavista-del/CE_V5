"""Nucleo de autenticacion (P06b). Reglas neutras, sin infraestructura."""

from ce_v5.core.auth.config import AuthConfig, AuthConfigError
from ce_v5.core.auth.email import normalize_email
from ce_v5.core.auth.passwords import Argon2PasswordHasher
from ce_v5.core.auth.ports import (
    CredentialReader,
    PasswordHasher,
    RegisteredUser,
    RotationOutcome,
    RotationResult,
    SessionStore,
    StoredCredential,
    UserRegistrar,
)
from ce_v5.core.auth.service import (
    AuthError,
    AuthService,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    IssuedSession,
    RefreshTokenReuseError,
)
from ce_v5.core.auth.sessions import RefreshToken, hash_refresh_token, new_refresh_token
from ce_v5.core.auth.tokens import (
    AccessTokenService,
    AuthenticatedPrincipal,
    InvalidAccessTokenError,
)

__all__ = [
    "AccessTokenService",
    "Argon2PasswordHasher",
    "AuthConfig",
    "AuthConfigError",
    "AuthError",
    "AuthService",
    "AuthenticatedPrincipal",
    "CredentialReader",
    "EmailAlreadyRegisteredError",
    "InvalidAccessTokenError",
    "InvalidCredentialsError",
    "InvalidRefreshTokenError",
    "IssuedSession",
    "PasswordHasher",
    "RefreshToken",
    "RefreshTokenReuseError",
    "RegisteredUser",
    "RotationOutcome",
    "RotationResult",
    "SessionStore",
    "StoredCredential",
    "UserRegistrar",
    "hash_refresh_token",
    "new_refresh_token",
    "normalize_email",
]
