"""Logs estructurados sin secretos (P06b, dictamen CSA J).

QUE NO SE REGISTRA, JAMAS: contrasenas, refresh tokens, access tokens, cabeceras
Authorization, cookies completas, ni hashes Argon2id. Un log es un fichero que acaba
copiado, enviado por correo y abierto por quien no deberia. Lo que no esta en el log no
se puede filtrar desde el log.

QUE SI SE REGISTRA: la HUELLA del email (nunca el email), la huella de la IP, un
correlation_id para poder seguir una peticion de punta a punta, y el motivo tecnico. Con
eso se investiga un incidente sin construir, de paso, una lista de clientes.

NADA DE STACK TRACES AL CLIENTE: un error interno devuelve un correlation_id y punto. El
detalle se queda en el log; el cliente no necesita saber en que linea revento nada, y un
atacante lo agradeceria mucho.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

# log_event y su redaccion viven en CORE: la disciplina de no filtrar secretos por el
# log la comparten la API y los workers, y un worker no debe arrastrar FastAPI para
# escribir una linea de log. Se REEXPORTAN aqui para no cambiar la superficie de la API.
from ce_v5.core.observability import REDACTED, log_event

CORRELATION_HEADER = "x-correlation-id"

_NextCall = Callable[[Request], Awaitable[Response]]


def correlation_id(request: Request) -> str:
    """El identificador de esta peticion, para seguirla de punta a punta."""
    valor: str = getattr(request.state, "correlation_id", "")
    return valor


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Un identificador por peticion, en el estado y en la respuesta."""

    async def dispatch(self, request: Request, call_next: _NextCall) -> Response:
        request.state.correlation_id = uuid4().hex
        response = await call_next(request)
        response.headers[CORRELATION_HEADER] = request.state.correlation_id
        return response


__all__ = [
    "CORRELATION_HEADER",
    "REDACTED",
    "CorrelationIdMiddleware",
    "correlation_id",
    "log_event",
]
