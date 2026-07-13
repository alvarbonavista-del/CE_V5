"""Linea base de la puerta publica (P06b, dictamen CSA D/E).

CABECERAS: una API que devuelve JSON tambien puede ser abierta en un navegador. Estas
cabeceras impiden que el contenido se interprete como algo que no es, que la pagina se
incruste en un iframe ajeno (clickjacking), y que las respuestas de sesion se queden en
caches intermedias.

LIMITE DE CUERPO: sin limite, cualquiera puede mandar un JSON de un gigabyte y tumbar
el proceso. Se rechaza ANTES de leerlo, mirando Content-Length: leerlo para medirlo ya
seria haber perdido.

CONTENT-TYPE: si el endpoint espera JSON, un cuerpo con otro tipo es un error o un
sondeo. Se rechaza.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from ce_v5.entrypoints.api.config import ApiConfig
from source.api import ApiError

_NextCall = Callable[[Request], Awaitable[Response]]

# Metodos que pueden traer cuerpo.
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})
_JSON = "application/json"

_HSTS = "max-age=31536000; includeSubDomains"


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ApiError(code=code, message=message).model_dump(),
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Cabeceras de seguridad en TODAS las respuestas, incluidas las de error."""

    def __init__(self, app: ASGIApp, config: ApiConfig) -> None:
        super().__init__(app)
        self._config = config

    async def dispatch(self, request: Request, call_next: _NextCall) -> Response:
        response = await call_next(request)
        headers = response.headers
        headers["X-Content-Type-Options"] = "nosniff"
        headers["Referrer-Policy"] = "no-referrer"
        headers["X-Frame-Options"] = "DENY"
        headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'"
        )
        headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
        # La API sirve sesiones y capabilities: nada de esto vive en una cache.
        headers["Cache-Control"] = "no-store"
        if self._config.is_production:
            # Fuera de produccion NO se manda: en desarrollo sin HTTPS, HSTS dejaria el
            # navegador clavado en https para ese host durante un ano.
            headers["Strict-Transport-Security"] = _HSTS
        return response


class BodyLimitMiddleware(BaseHTTPMiddleware):
    """Rechaza los cuerpos gigantes ANTES de leerlos."""

    def __init__(self, app: ASGIApp, config: ApiConfig) -> None:
        super().__init__(app)
        self._config = config

    async def dispatch(self, request: Request, call_next: _NextCall) -> Response:
        if request.method in _BODY_METHODS:
            declarada = request.headers.get("content-length")
            if declarada is None:
                if request.headers.get("transfer-encoding"):
                    # Cuerpo en trozos, sin longitud declarada: no se puede aplicar el
                    # limite ANTES de leer, y leer para medir ya seria haber perdido.
                    return _error(
                        status.HTTP_411_LENGTH_REQUIRED,
                        "length_required",
                        "La peticion debe declarar Content-Length.",
                    )
            else:
                try:
                    tamano = int(declarada)
                except ValueError:
                    return _error(
                        status.HTTP_411_LENGTH_REQUIRED,
                        "length_required",
                        "Content-Length no es un numero.",
                    )
                if tamano > self._config.max_body_bytes:
                    return _error(
                        status.HTTP_413_CONTENT_TOO_LARGE,
                        "payload_too_large",
                        "El cuerpo de la peticion es demasiado grande.",
                    )
        return await call_next(request)


class JsonContentTypeMiddleware(BaseHTTPMiddleware):
    """Un cuerpo que no es JSON donde se espera JSON es un error o un sondeo."""

    async def dispatch(self, request: Request, call_next: _NextCall) -> Response:
        if request.method in _BODY_METHODS:
            declarada = request.headers.get("content-length")
            tiene_cuerpo = declarada is not None and declarada.strip() not in ("", "0")
            if tiene_cuerpo:
                content_type = request.headers.get("content-type", "")
                # El Content-Type puede traer parametros (charset): basta el tipo.
                if content_type.split(";")[0].strip().lower() != _JSON:
                    return _error(
                        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                        "unsupported_media_type",
                        "El cuerpo debe ser application/json.",
                    )
        return await call_next(request)
