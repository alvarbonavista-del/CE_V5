"""La IP efectiva de quien llama (P06b, dictamen CSA c/A).

Es el UNICO sitio de la API que decide la IP. Nadie mas mira cabeceras: si cada modulo
decidiese por su cuenta, bastaria con que uno se fiara de X-Forwarded-For para que el
limitador y el geo-bloqueo quedasen inutiles.
"""

from __future__ import annotations

from fastapi import Request

from ce_v5.entrypoints.api.config import ApiConfig

_FORWARDED_FOR = "x-forwarded-for"


def client_ip(request: Request, config: ApiConfig) -> str | None:
    """IP efectiva segun la cadena de proxies CONFIABLE, o None si no se conoce."""
    conexion = request.client.host if request.client is not None else None

    # Sin proxies propios delante, X-Forwarded-For es puro dicho del cliente: se IGNORA.
    if config.trusted_proxy_count == 0:
        return conexion

    cadena = [
        parte.strip()
        for parte in request.headers.get(_FORWARDED_FOR, "").split(",")
        if parte.strip()
    ]
    # Cadena mas corta de lo que dicta la topologia: la cabecera es sospechosa (o falta
    # un proxy). No se adivina: se cae a la IP de la conexion, que nadie falsifica.
    if len(cadena) < config.trusted_proxy_count:
        return conexion
    # El elemento N-esimo empezando por el FINAL: lo escribio nuestro proxy mas externo,
    # asi que es la ultima entrada que el cliente NO pudo falsificar.
    return cadena[-config.trusted_proxy_count]
