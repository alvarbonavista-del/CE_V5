"""Token de acceso (JWT) del backend (P06b, ADR-019).

QUE LLEVA Y QUE NO: lleva el identificador de USUARIO y nada mas. NO lleva el tenant.
El tenant lo resuelve el BACKEND en cada peticion desde la pertenencia (ADR-011,
obligacion vinculante de P05): si viajara en el token, seria un dato que el sistema
arrastra sin volver a comprobar, y una pertenencia revocada seguiria concediendo
acceso hasta que caducara el pase.

VIDA CORTA a proposito (15 min por defecto): es lo que acota el dano de un token
robado, ya que un JWT no se puede "apagar" a distancia.

ALGORITMO EXPLICITO: al verificar se exige HS256 y se rechaza cualquier otro. Es la
defensa contra el ataque clasico de confusion de algoritmo ("alg: none"), en el que
un atacante manda un token SIN FIRMA y una libreria mal configurada se lo traga.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import jwt

from ce_v5.core.auth.config import TOKEN_AUDIENCE, TOKEN_ISSUER, AuthConfig
from ce_v5.core.clock.protocol import Clock

_ALGORITHM = "HS256"
_TOKEN_TYPE = "access"
_REQUIRED_CLAIMS = ["exp", "iat", "sub", "aud", "iss"]


class AccessTokenError(RuntimeError):
    """Error relativo al token de acceso."""


class InvalidAccessTokenError(AccessTokenError):
    """El token no es valido: firma, caducidad, emisor, destinatario o tipo."""


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Identidad VERIFICADA por el backend.

    Es la UNICA fuente de identidad admitida en toda la API (obligacion vinculante de
    P05 y P06): jamas se construye desde el body, la query, una cabecera no
    autenticada ni un mensaje de WebSocket.

    expires_at_seconds es el exp del token YA VERIFICADO. Viaja aqui para que nadie
    mas tenga que volver a decodificar el JWT: el UNICO modulo que toca JWT es este. El
    canal realtime lo necesita para cerrar la conexion cuando el pase caduca, y un
    segundo sitio manipulando tokens es un segundo sitio donde equivocarse.
    """

    user_id: UUID
    expires_at_seconds: int


class AccessTokenService:
    """Emite y verifica tokens de acceso."""

    def __init__(self, config: AuthConfig, clock: Clock) -> None:
        self._config = config
        self._clock = clock

    def issue(self, user_id: UUID) -> str:
        """Emite un token de acceso de vida corta para un usuario ya autenticado."""
        now_seconds = self._clock.now_ms() // 1000
        claims: dict[str, object] = {
            "iss": TOKEN_ISSUER,
            "aud": TOKEN_AUDIENCE,
            "sub": str(user_id),
            "iat": now_seconds,
            "exp": now_seconds + self._config.access_ttl_seconds,
            "jti": str(uuid4()),
            "typ": _TOKEN_TYPE,
        }
        return jwt.encode(claims, self._config.jwt_secret, algorithm=_ALGORITHM)

    def verify(self, token: str) -> AuthenticatedPrincipal:
        """Verifica el token y devuelve la identidad. Lanza si NO es valido."""
        try:
            claims = jwt.decode(
                token,
                self._config.jwt_secret,
                algorithms=[_ALGORITHM],
                audience=TOKEN_AUDIENCE,
                issuer=TOKEN_ISSUER,
                options={"require": _REQUIRED_CLAIMS},
            )
        except jwt.PyJWTError as exc:
            raise InvalidAccessTokenError("Token de acceso no valido.") from exc
        if claims.get("typ") != _TOKEN_TYPE:
            raise InvalidAccessTokenError(
                "El token no es de tipo acceso: un refresh no sirve como pase."
            )
        try:
            user_id = UUID(str(claims["sub"]))
        except ValueError as exc:
            raise InvalidAccessTokenError("El sujeto del token no es un uuid.") from exc
        try:
            expires_at = int(claims["exp"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidAccessTokenError("El token no declara un exp valido.") from exc
        return AuthenticatedPrincipal(user_id=user_id, expires_at_seconds=expires_at)
