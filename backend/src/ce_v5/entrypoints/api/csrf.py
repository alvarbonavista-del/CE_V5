"""Proteccion CSRF por doble envio (P06b, dictamen CSA B/G).

EL ATAQUE: el navegador envia la cookie de refresh SOLA, sin preguntar. Si estas
logueado y visitas una pagina maliciosa, esa pagina puede provocar una llamada a
/v1/auth/refresh y tu navegador ADJUNTARA TU COOKIE. La peticion parece tuya porque,
tecnicamente, lo es.

LA DEFENSA: exigir ademas algo que la pagina atacante NO PUEDE LEER. Se entrega un token
en una cookie legible por JavaScript del PROPIO dominio, y se exige que venga repetido
en una cabecera. El atacante puede hacer que el navegador ENVIE la cookie; no puede
LEERLA para copiarla en la cabecera (el navegador se lo impide). Sin las dos mitades, se
rechaza.

COMPARACION EN TIEMPO CONSTANTE (dictamen G): se usa hmac.compare_digest. Un == normal
corta en el primer caracter distinto, y el TIEMPO de respuesta iria revelando el token
caracter a caracter.

ORIGIN/REFERER: ademas del token, se comprueba el origen declarado. Si viene un Origin
que no esta en la allowlist, se rechaza: nadie de fuera tiene nada que hacer aqui.
"""

from __future__ import annotations

import hmac
import secrets

from fastapi import Request

from ce_v5.entrypoints.api.config import ApiConfig
from ce_v5.entrypoints.api.cookies import CSRF_COOKIE_NAME, CSRF_HEADER_NAME

_ORIGIN = "origin"
_TOKEN_BYTES = 32


class CsrfError(RuntimeError):
    """La peticion no acredita venir de nuestro propio sitio."""


def new_csrf_token() -> str:
    """Token aleatorio de doble envio. No es un secreto de sesion."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def verify_csrf(request: Request, config: ApiConfig) -> None:
    """Exige token de cabecera == cookie (tiempo constante) y Origin admitido.

    Lanza CsrfError si no. Se aplica SOLO a los endpoints autenticados POR COOKIE que
    cambian estado (refresh y logout): son los unicos que un tercero podria disparar sin
    saber nada del usuario. login y register no lo necesitan (no hay cookie de sesion
    que el atacante pueda reutilizar: quien llama debe aportar la contrasena).
    """
    origin = request.headers.get(_ORIGIN)
    # Un Origin declarado que no esta en la allowlist no tiene nada que hacer aqui. Sin
    # Origin (llamadas que no vienen de un navegador) no se puede juzgar por este lado,
    # y manda el token.
    if origin is not None and origin not in config.allowed_origins:
        raise CsrfError("Origen no admitido.")

    cookie = request.cookies.get(CSRF_COOKIE_NAME)
    header = request.headers.get(CSRF_HEADER_NAME)
    if not cookie or not header:
        # Falta una de las dos mitades: la pagina atacante puede provocar el envio de la
        # cookie, pero jamas puede leerla para rellenar la cabecera.
        raise CsrfError("Falta el token CSRF.")
    if not hmac.compare_digest(cookie, header):
        raise CsrfError("El token CSRF no coincide.")
