"""La identidad de una peticion (P06b; obligacion vinculante de P05 y P06).

LA IDENTIDAD SALE EXCLUSIVAMENTE DE LA SESION VERIFICADA. Nunca del body, ni de la
query, ni de una cabecera no autenticada, ni de un mensaje de WebSocket. Este modulo es
el UNICO sitio de la API que produce un AuthenticatedPrincipal, y solo lo hace tras
verificar la firma del token. Sin token valido: 401, fail-closed.

El TENANT no viaja en el token: lo resuelve el backend por la pertenencia (ADR-011).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from ce_v5.core.auth.tokens import AuthenticatedPrincipal, InvalidAccessTokenError
from ce_v5.entrypoints.api.composition import ApiContext

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="unauthorized",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_context(request: Request) -> ApiContext:
    """El contexto cableado que vive en la aplicacion."""
    context: ApiContext = request.app.state.context
    return context


def current_principal(request: Request) -> AuthenticatedPrincipal:
    """Identidad VERIFICADA del que llama. 401 si no la hay."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _UNAUTHORIZED
    try:
        return get_context(request).tokens.verify(token.strip())
    except InvalidAccessTokenError as exc:
        raise _UNAUTHORIZED from exc


def optional_principal(request: Request) -> AuthenticatedPrincipal | None:
    """La identidad VERIFICADA si la hay, o None. Nunca una identidad sin verificar.

    El logout se autentica POR COOKIE, asi que no exige token: quien pierde su access
    token debe poder cerrar sesion igual. Pero si trae uno VALIDO, la traza tiene dueno
    y puede ir a la auditoria por sujeto. Sin token valido no se inventa un dueno: se
    cierra la sesion igual y el hecho se queda sin fila.
    """
    try:
        return current_principal(request)
    except HTTPException:
        return None


# Inyeccion con Annotated (estilo moderno de FastAPI): la dependencia va en el TIPO, no
# en un valor por defecto. Todos los routers comparten estos dos alias, de modo que la
# identidad de una peticion se produce SIEMPRE por el mismo camino verificado.
Context = Annotated[ApiContext, Depends(get_context)]
Principal = Annotated[AuthenticatedPrincipal, Depends(current_principal)]
OptionalPrincipal = Annotated[
    AuthenticatedPrincipal | None, Depends(optional_principal)
]
