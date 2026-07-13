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

import json
import logging
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

CORRELATION_HEADER = "x-correlation-id"

_LOGGER = logging.getLogger("ce_v5.api")
_NextCall = Callable[[Request], Awaitable[Response]]

REDACTED = "[REDACTADO]"

# Fragmentos que delatan un secreto en el NOMBRE de un campo. La disciplina no puede
# depender de que nadie se equivoque nunca: si alguien pasa por error una contrasena o
# un token a log_event, se REDACTA en vez de escribirse. Un log es un fichero que acaba
# copiado y abierto por quien no deberia.
_PROHIBIDOS = ("password", "token", "authorization", "cookie", "secret", "hash")


def _redactar(campos: dict[str, object]) -> dict[str, object]:
    return {
        clave: (
            REDACTED
            if any(prohibido in clave.lower() for prohibido in _PROHIBIDOS)
            else valor
        )
        for clave, valor in campos.items()
    }


def log_event(event: str, **campos: object) -> None:
    """Emite una linea JSON. Redacta activamente cualquier campo sospechoso."""
    _LOGGER.info(json.dumps({"event": event, **_redactar(campos)}, sort_keys=True))


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
