"""Las cookies de la puerta (P06b, ADR-019: regla dura; dictamen CSA B).

REFRESH (httpOnly):
httponly=True   -> el JavaScript NO puede leerla (ni con document.cookie). Si manana
                   inyectan un script en la pagina, podran robar el pase de 15 minutos,
                   pero NO la llave que renueva la sesion.
secure          -> solo viaja por HTTPS: nadie la lee en una wifi de aeropuerto. Es
                   configurable SOLO para poder desarrollar sin HTTPS; el guardia de
                   arranque impide desactivarlo en produccion.
samesite=strict -> no se envia desde otro sitio web: mata el CSRF sobre /v1/auth.
path            -> solo se envia a /v1/auth: el resto de la API ni la recibe.

CSRF (legible por JavaScript, a proposito): ver set_csrf_cookie.
"""

from __future__ import annotations

from fastapi import Response

REFRESH_COOKIE_NAME = "ce_v5_refresh"
REFRESH_COOKIE_PATH = "/v1/auth"

CSRF_COOKIE_NAME = "ce_v5_csrf"
CSRF_HEADER_NAME = "x-csrf-token"


def set_refresh_cookie(
    response: Response, token: str, max_age_seconds: int, secure: bool
) -> None:
    """Entrega el refresh token SOLO por cookie httpOnly. Nunca en el cuerpo."""
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        max_age=max_age_seconds,
        httponly=True,
        secure=secure,
        samesite="strict",
        path=REFRESH_COOKIE_PATH,
    )


def clear_refresh_cookie(response: Response, secure: bool) -> None:
    """Borra la cookie al salir."""
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        httponly=True,
        secure=secure,
        samesite="strict",
        path=REFRESH_COOKIE_PATH,
    )


def set_csrf_cookie(
    response: Response, token: str, max_age_seconds: int, secure: bool
) -> None:
    """La cookie CSRF NO es httpOnly, y eso es DELIBERADO.

    El JavaScript de NUESTRA pagina debe poder leerla para reenviarla en una cabecera.
    El JavaScript de una pagina MALICIOSA no puede: el navegador no le deja leer cookies
    de otro dominio. Ahi esta la defensa: la web atacante puede provocar que el
    navegador ENVIE la cookie, pero no puede LEERLA para copiarla en la cabecera. Sin
    las dos mitades, la peticion se rechaza.

    No es un secreto de sesion: es una prueba de que quien llama puede LEER nuestro
    dominio. Por eso no ser httpOnly no la debilita.
    """
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        max_age=max_age_seconds,
        httponly=False,
        secure=secure,
        samesite="strict",
        path=REFRESH_COOKIE_PATH,
    )


def clear_csrf_cookie(response: Response, secure: bool) -> None:
    """Borra la cookie CSRF al salir."""
    response.delete_cookie(
        key=CSRF_COOKIE_NAME,
        httponly=False,
        secure=secure,
        samesite="strict",
        path=REFRESH_COOKIE_PATH,
    )
