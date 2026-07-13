"""Tests del token de acceso (P06b, ADR-019). Reloj SIMULADO real de core/clock.

AVISO SOBRE EL TIEMPO (importante para leer estos tests): el Clock inyectado gobierna
la EMISION (que iat y que exp se escriben en el token), pero NO la VERIFICACION:
PyJWT compara exp contra el reloj REAL del sistema y no admite que se le inyecte otro.
Por eso el SimulatedClock se ancla al instante real:

- Camino feliz: se ancla al AHORA real, de modo que el exp emitido cae en el futuro
  real y la verificacion pasa.
- Caducidad: se ancla al PASADO (mas de un TTL atras), de modo que el token nace ya
  caducado para el reloj real. Se eligio esta via, y no advance() del reloj simulado,
  porque avanzar el simulado NO mueve el reloj con el que PyJWT decide, y no se usa
  sleep() porque un test no debe tardar lo que tarda un TTL.
"""

import base64
import json
import time
from uuid import UUID, uuid4

import jwt
import pytest

from ce_v5.core.auth import AccessTokenService, AuthConfig, InvalidAccessTokenError
from ce_v5.core.auth.config import TOKEN_AUDIENCE, TOKEN_ISSUER
from ce_v5.core.clock import SimulatedClock

_SECRETO = "secreto-de-test-de-32-caracteres-o-mas"
_OTRO_SECRETO = "otro-secreto-de-test-de-32-caracteres"
_CONFIG = AuthConfig(jwt_secret=_SECRETO, access_ttl_seconds=900)


def _now_ms() -> int:
    """Instante real en epoch ms: es el que usa PyJWT para juzgar exp."""
    return int(time.time() * 1000)


def _service(clock: SimulatedClock) -> AccessTokenService:
    return AccessTokenService(_CONFIG, clock)


def test_issue_y_verify_devuelven_el_mismo_usuario() -> None:
    user_id = uuid4()
    service = _service(SimulatedClock(start_ms=_now_ms()))
    principal = service.verify(service.issue(user_id))
    assert principal.user_id == user_id


def test_firma_sustituida_falla() -> None:
    """Una firma que no es la nuestra debe rechazarse SIEMPRE.

    Se sustituye la firma ENTERA por relleno, en vez de tocarle el ultimo caracter:
    en base64url el ultimo caracter arrastra bits de relleno que no se leen, asi que
    cambiarlo puede decodificar la MISMA firma y el test seria intermitente.
    """
    service = _service(SimulatedClock(start_ms=_now_ms()))
    token = service.issue(uuid4())
    cabecera, cuerpo, firma = token.split(".")
    manipulado = f"{cabecera}.{cuerpo}.{'A' * len(firma)}"
    with pytest.raises(InvalidAccessTokenError):
        service.verify(manipulado)


def test_cuerpo_manipulado_falla() -> None:
    """Cambiar el contenido invalida la firma: no se puede reescribir el sujeto."""
    service = _service(SimulatedClock(start_ms=_now_ms()))
    token = service.issue(uuid4())
    cabecera, cuerpo, firma = token.split(".")
    otro_cuerpo = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "iss": TOKEN_ISSUER,
                    "aud": TOKEN_AUDIENCE,
                    "sub": str(uuid4()),
                    "iat": _now_ms() // 1000,
                    "exp": _now_ms() // 1000 + 900,
                    "typ": "access",
                }
            ).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    with pytest.raises(InvalidAccessTokenError):
        service.verify(f"{cabecera}.{otro_cuerpo}.{firma}")


def test_token_firmado_con_otro_secreto_falla() -> None:
    ajeno = AccessTokenService(
        AuthConfig(jwt_secret=_OTRO_SECRETO, access_ttl_seconds=900),
        SimulatedClock(start_ms=_now_ms()),
    )
    token = ajeno.issue(uuid4())
    with pytest.raises(InvalidAccessTokenError):
        _service(SimulatedClock(start_ms=_now_ms())).verify(token)


def test_token_caducado_falla() -> None:
    # Emitido con el reloj anclado DOS TTL en el pasado: su exp queda un TTL por
    # detras del ahora real, que es el que PyJWT compara.
    pasado_ms = _now_ms() - 2 * _CONFIG.access_ttl_seconds * 1000
    token = _service(SimulatedClock(start_ms=pasado_ms)).issue(uuid4())
    with pytest.raises(InvalidAccessTokenError):
        _service(SimulatedClock(start_ms=_now_ms())).verify(token)


def test_token_sin_firma_alg_none_falla() -> None:
    # Confusion de algoritmo: el atacante manda un token SIN firma. verify() exige
    # HS256, asi que lo rechaza en vez de tragarselo.
    ahora = _now_ms() // 1000
    token = jwt.encode(
        {
            "iss": "ce_v5",
            "aud": "ce_v5-api",
            "sub": str(uuid4()),
            "iat": ahora,
            "exp": ahora + 900,
            "typ": "access",
        },
        key="",
        algorithm="none",
    )
    with pytest.raises(InvalidAccessTokenError):
        _service(SimulatedClock(start_ms=_now_ms())).verify(token)


def test_token_de_otro_tipo_no_sirve_como_pase() -> None:
    ahora = _now_ms() // 1000
    token = jwt.encode(
        {
            "iss": "ce_v5",
            "aud": "ce_v5-api",
            "sub": str(uuid4()),
            "iat": ahora,
            "exp": ahora + 900,
            "typ": "refresh",
        },
        _SECRETO,
        algorithm="HS256",
    )
    with pytest.raises(InvalidAccessTokenError):
        _service(SimulatedClock(start_ms=_now_ms())).verify(token)


def test_el_token_no_lleva_el_tenant_dentro() -> None:
    # El tenant lo resuelve el backend en cada peticion desde la pertenencia
    # (ADR-011): si viajara en el token, una pertenencia revocada seguiria valiendo.
    service = _service(SimulatedClock(start_ms=_now_ms()))
    claims = jwt.decode(
        service.issue(uuid4()),
        options={"verify_signature": False},
    )
    assert not any("tenant" in claim.lower() for claim in claims)
    assert UUID(str(claims["sub"]))


def test_verify_devuelve_el_exp_del_token() -> None:
    # El exp viaja en el principal para que NADIE MAS tenga que decodificar el JWT: el
    # unico modulo que toca JWT es core/auth/tokens (el realtime lo consume de aqui).
    ahora = _now_ms() // 1000
    service = _service(SimulatedClock(start_ms=_now_ms()))
    principal = service.verify(service.issue(uuid4()))

    assert principal.expires_at_seconds >= ahora
    assert principal.expires_at_seconds <= ahora + _CONFIG.access_ttl_seconds + 1
