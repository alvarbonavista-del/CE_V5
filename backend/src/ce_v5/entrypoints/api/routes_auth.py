"""Rutas de autenticacion e identidad (P06b, ADR-019, CA-08).

Los modelos de peticion y respuesta son EXCLUSIVAMENTE los contratos de source.api: la
API no inventa formas propias (ADR-006). Un campo que no este en el contrato se rechaza
con 422, y por eso el cliente no puede colar un tenant en el cuerpo.

EL REFRESH TOKEN SOLO VIAJA POR COOKIE httpOnly: se ESCRIBE con set_refresh_cookie y se
LEE de request.cookies. Nunca del cuerpo, nunca de la query, nunca en la respuesta.

CSRF: refresh y logout se autentican POR COOKIE, y una cookie la envia el navegador
solo, sin preguntar. Por eso EXIGEN el token de doble envio (verify_csrf) ANTES de nada.
login y register no lo necesitan: quien llama debe aportar la contrasena, que ninguna
pagina ajena conoce. El token CSRF viaja en su cookie (legible por nuestro JavaScript) y
NO en el cuerpo: el contrato de SessionResponse no lo tiene y no debe tenerlo.

FUERA DE ALCANCE PARA SIEMPRE (DOC_ROADMAP, ficha P06b): estos endpoints NO evaluan
reglas de negocio, NO ejecutan ordenes y NO deciden politica. La API es una puerta:
autentica, resuelve identidad y delega. Lo demas es de otras piezas.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from ce_v5.core.auth.service import InvalidRefreshTokenError, IssuedSession
from ce_v5.entrypoints.api.client_ip import client_ip
from ce_v5.entrypoints.api.composition import ApiContext
from ce_v5.entrypoints.api.cookies import (
    REFRESH_COOKIE_NAME,
    clear_csrf_cookie,
    clear_refresh_cookie,
    set_csrf_cookie,
    set_refresh_cookie,
)
from ce_v5.entrypoints.api.csrf import new_csrf_token, verify_csrf
from ce_v5.entrypoints.api.security import Context, OptionalPrincipal, Principal
from source.api import LoginRequest, MeResponse, RegisterRequest, SessionResponse

router = APIRouter(prefix="/v1")


def _session_response(
    issued: IssuedSession, context: ApiContext, response: Response
) -> SessionResponse:
    """Entrega el access token en el cuerpo; el refresh y el CSRF, en sus cookies."""
    secure = context.api_config.cookie_secure
    ttl = context.config.refresh_ttl_seconds
    set_refresh_cookie(response, issued.refresh_token, ttl, secure)
    # Token CSRF NUEVO en cada emision: si uno se filtrase, su vida es la de esa sesion.
    set_csrf_cookie(response, new_csrf_token(), ttl, secure)
    return SessionResponse(
        access_token=issued.access_token,
        expires_in_seconds=context.config.access_ttl_seconds,
        user_id=str(issued.user_id),
    )


def _refresh_token_from_cookie(request: Request) -> str:
    """El refresh token SOLO se acepta desde la cookie httpOnly (ADR-019)."""
    token = request.cookies.get(REFRESH_COOKIE_NAME, "")
    if not token:
        raise InvalidRefreshTokenError("No hay cookie de refresh.")
    return token


@router.post(
    "/auth/register",
    status_code=status.HTTP_201_CREATED,
    response_model=SessionResponse,
)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    context: Context,
) -> SessionResponse:
    """Alta de cuenta: usuario, tenant y pertenencia en una sola transaccion."""
    issued = context.auth.register(
        payload.email, payload.password, client_ip(request, context.api_config)
    )
    return _session_response(issued, context, response)


@router.post("/auth/login", response_model=SessionResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    context: Context,
) -> SessionResponse:
    """Entrada al sistema. El fallo no dice nunca por que fallo."""
    issued = context.auth.login(
        payload.email, payload.password, client_ip(request, context.api_config)
    )
    return _session_response(issued, context, response)


@router.post("/auth/refresh", response_model=SessionResponse)
def refresh(
    request: Request,
    response: Response,
    context: Context,
) -> SessionResponse:
    """Rota el refresh token: el viejo muere y nace otro (cookie nueva)."""
    # ANTES de nada: esta peticion se autentica por cookie, y una cookie la manda el
    # navegador solo. Sin la mitad que una pagina ajena no puede leer, no se sigue.
    verify_csrf(request, context.api_config)
    issued = context.auth.refresh(
        _refresh_token_from_cookie(request), client_ip(request, context.api_config)
    )
    return _session_response(issued, context, response)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    context: Context,
    principal: OptionalPrincipal,
) -> None:
    """Cierra la sesion revocando la familia entera y borrando las cookies.

    El access token es OPCIONAL: quien lo perdio debe poder salir igual. Si lo trae y es
    valido, la traza tiene dueno y va a la auditoria por sujeto.
    """
    verify_csrf(request, context.api_config)
    context.auth.logout(
        _refresh_token_from_cookie(request),
        None if principal is None else principal.user_id,
    )
    secure = context.api_config.cookie_secure
    clear_refresh_cookie(response, secure)
    clear_csrf_cookie(response, secure)


@router.get("/me", response_model=MeResponse)
def me(principal: Principal, context: Context) -> MeResponse:
    """Quien eres y en que tenant operas, SEGUN EL BACKEND.

    El tenant NO lo manda el cliente en ninguna parte: lo resuelve el backend desde la
    pertenencia del principal autenticado (ADR-011). Una query o una cabecera que
    pretendan imponer identidad o tenant se IGNORAN: aqui no se leen.
    """
    with context.scoped_db.transaction(principal.user_id) as scoped:
        tenant_id = scoped.context.tenant_id
    return MeResponse(user_id=str(principal.user_id), tenant_id=str(tenant_id))
