"""Log estructurado sin secretos, compartido por TODO proceso (P06b, dictamen CSA J).

Vive en CORE, no en entrypoints.api, porque la disciplina de no filtrar secretos por el
log no es de la API: es de la plataforma. La API, el worker de ingesta y el motor de
reglas escriben logs, y los tres deben redactar igual. Mientras esta funcion vivio en
entrypoints/api/observability.py, usarla desde un worker habria arrastrado FastAPI y
Starlette al proceso del worker -- una dependencia web dentro de un proceso que no sirve
HTTP. entrypoints.api sigue reexportandola, asi que su superficie no cambia.

QUE NO SE REGISTRA, JAMAS: contrasenas, refresh tokens, access tokens, cabeceras
Authorization, cookies completas, ni hashes Argon2id. Un log es un fichero que acaba
copiado, enviado por correo y abierto por quien no deberia. Lo que no esta en el log no
se puede filtrar desde el log.
"""

from __future__ import annotations

import json
import logging

_LOGGER = logging.getLogger("ce_v5")

REDACTED = "[REDACTADO]"

# Fragmentos que delatan un secreto en el NOMBRE de un campo. La disciplina no puede
# depender de que nadie se equivoque nunca: si alguien pasa por error una contrasena o
# un token a log_event, se REDACTA en vez de escribirse.
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


__all__ = ["REDACTED", "log_event"]
