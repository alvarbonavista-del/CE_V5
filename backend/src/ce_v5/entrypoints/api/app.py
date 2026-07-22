"""La aplicacion FastAPI (P06b, ADR-002).

create_app RECIBE el contexto ya cableado en vez de construirlo: asi los tests inyectan
una base de pruebas sin tocar el entorno, y el proceso real lo construye en __main__.

TRADUCCION DE ERRORES SIN FILTRACIONES: los 401 de credenciales dicen TODOS lo mismo. Si
un email inexistente respondiera distinto que una contrasena equivocada, la API estaria
diciendo QUIEN tiene cuenta.

NO se monta CORS: no hay cliente web todavia (es P12a) y no se construye nada "por si
acaso".
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ce_v5.core.auth.rate_limit import RateLimiterUnavailableError
from ce_v5.core.auth.service import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    RefreshTokenReuseError,
)
from ce_v5.core.policy.cached_evaluator import PolicyDegradedError
from ce_v5.core.tenancy.errors import TenantResolutionError
from ce_v5.entrypoints.api.composition import ApiContext
from ce_v5.entrypoints.api.csrf import CsrfError
from ce_v5.entrypoints.api.middleware import (
    BodyLimitMiddleware,
    JsonContentTypeMiddleware,
    SecurityHeadersMiddleware,
)
from ce_v5.entrypoints.api.observability import (
    CorrelationIdMiddleware,
    correlation_id,
    log_event,
)
from ce_v5.entrypoints.api.realtime import router as realtime_router
from ce_v5.entrypoints.api.routes_auth import router as auth_router
from ce_v5.entrypoints.api.routes_capabilities import router as capabilities_router
from ce_v5.entrypoints.api.routes_market import router as market_router
from source.api import ApiError

# Mensaje UNICO para todo fallo de autenticacion: cubre los cuatro casos (usuario
# inexistente, clave equivocada, cuenta frenada y limitador caido) sin distinguirlos.
_AUTH_FAILED = "Credenciales invalidas o temporalmente no disponibles."

_LOGGER = logging.getLogger("ce_v5.api")


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ApiError(code=code, message=message).model_dump(),
    )


def create_app(context: ApiContext) -> FastAPI:
    """Construye la aplicacion con el contexto ya cableado."""
    app = FastAPI(title="Crypto Engine V5 API", version="0.0.0")
    app.state.context = context
    app.include_router(auth_router)
    app.include_router(capabilities_router)
    app.include_router(market_router)
    app.include_router(realtime_router)

    # El ULTIMO en anadirse es el MAS EXTERNO. SecurityHeaders va fuera de los otros dos
    # para que sus cabeceras salgan tambien en los 413/415 que ellos generan.
    app.add_middleware(JsonContentTypeMiddleware)
    app.add_middleware(BodyLimitMiddleware, config=context.api_config)
    app.add_middleware(SecurityHeadersMiddleware, config=context.api_config)
    # El MAS EXTERNO de todos: un error en cualquier otro middleware tambien debe salir
    # con su correlation_id, o no habria por donde seguirlo en el log.
    app.add_middleware(CorrelationIdMiddleware)

    if context.api_config.allowed_origins:
        # CORS SOLO si hay origenes declarados. Hoy no existe cliente web (es P12a), y
        # montar CORS "por si acaso" solo abre superficie: cada origen admitido es una
        # web mas a la que se le permite hablar con credenciales. El comodin lo prohibe
        # el guardia de arranque de ApiConfig.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(context.api_config.allowed_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["authorization", "content-type", "x-csrf-token"],
        )

    @app.exception_handler(InvalidCredentialsError)
    def _invalid_credentials(
        request: Request, exc: InvalidCredentialsError
    ) -> JSONResponse:
        return _error(status.HTTP_401_UNAUTHORIZED, "invalid_credentials", _AUTH_FAILED)

    @app.exception_handler(RateLimiterUnavailableError)
    def _rate_limiter_unavailable(
        request: Request, exc: RateLimiterUnavailableError
    ) -> JSONResponse:
        # La excepcion lleva la ACCION: el log dice QUE se estaba haciendo, no solo por
        # que ruta entro.
        context.auditor.limiter_unavailable(action=exc.action)
        # Fail-closed y GENERICO (dictamen CSA d): si el limitador no responde, no se
        # autentica a nadie. Al usuario se le da la misma respuesta que a una credencial
        # invalida; el motivo real (rate_limiter_unavailable) se registra DENTRO, nunca
        # se devuelve. Un 503 aqui le diria al atacante "el limitador esta caido, ataca
        # ahora".
        return _error(status.HTTP_401_UNAUTHORIZED, "invalid_credentials", _AUTH_FAILED)

    @app.exception_handler(InvalidRefreshTokenError)
    def _invalid_refresh(
        request: Request, exc: InvalidRefreshTokenError
    ) -> JSONResponse:
        return _error(
            status.HTTP_401_UNAUTHORIZED, "invalid_refresh_token", _AUTH_FAILED
        )

    @app.exception_handler(RefreshTokenReuseError)
    def _refresh_reused(request: Request, exc: RefreshTokenReuseError) -> JSONResponse:
        return _error(
            status.HTTP_401_UNAUTHORIZED, "refresh_token_reused", _AUTH_FAILED
        )

    @app.exception_handler(CsrfError)
    def _csrf_failed(request: Request, exc: CsrfError) -> JSONResponse:
        # Hecho PRE-identidad: no sabemos quien era (esa es justo la cuestion), asi que
        # va al LOG, nunca a la auditoria por sujeto.
        context.auditor.csrf_rejected(path=request.url.path)
        # Mensaje generico: el motivo exacto (falta la cabecera, no coincide, origen
        # ajeno) no le sirve de nada a quien llama legitimamente, y a quien sondea le
        # diria por donde seguir.
        return _error(
            status.HTTP_403_FORBIDDEN,
            "csrf_failed",
            "La peticion no acredita venir de este sitio.",
        )

    @app.exception_handler(Exception)
    def _internal_error(request: Request, exc: Exception) -> JSONResponse:
        # El stack trace se queda en el LOG. Al cliente solo le llega el
        # correlation_id: no necesita saber en que linea revento nada, y un atacante lo
        # agradeceria mucho.
        cid = correlation_id(request)
        _LOGGER.exception("error interno", extra={"correlation_id": cid})
        log_event("api.internal_error", correlation_id=cid, error=type(exc).__name__)
        return _error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            f"Error interno. Referencia: {cid}",
        )

    @app.exception_handler(EmailAlreadyRegisteredError)
    def _email_taken(
        request: Request, exc: EmailAlreadyRegisteredError
    ) -> JSONResponse:
        return _error(
            status.HTTP_409_CONFLICT,
            "email_taken",
            "Ya existe una cuenta con ese email.",
        )

    @app.exception_handler(PolicyDegradedError)
    def _policy_unavailable(request: Request, exc: PolicyDegradedError) -> JSONResponse:
        # La politica no se pudo recomputar. La API NO inventa una respuesta permisiva:
        # se declara incapaz (503). Fail-closed: un fallo jamas concede.
        return _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "policy_unavailable",
            "La politica no esta disponible en este momento.",
        )

    @app.exception_handler(TenantResolutionError)
    def _tenant_unresolved(
        request: Request, exc: TenantResolutionError
    ) -> JSONResponse:
        # Fail-closed (ADR-011): sin pertenencia valida no se opera. No es un 500: el
        # sistema funciona, es esta identidad la que no puede operar.
        return _error(
            status.HTTP_403_FORBIDDEN,
            "tenant_unresolved",
            "No hay un tenant resuelto para esta identidad.",
        )

    return app
