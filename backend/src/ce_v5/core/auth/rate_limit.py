"""Limitador de intentos de autenticacion (P06b, CA-10; dictamen CSA A/G/H).

POR QUE NO SE BLOQUEA LA CUENTA: bloquear tras N fallos permitiria que un atacante DEJE
FUERA al usuario legitimo fallando adrede contra su email: una denegacion de servicio
regalada. Se usa RETARDO PROGRESIVO con ventanas cortas y decaimiento. Nunca hay bloqueo
permanente: el contador caduca solo.

RETARDO PROGRESIVO REAL: cada fallo por encima del umbral planta una llave de bloqueo
que dura 2, 4, 8... segundos (con tope). No es un muro fijo de cinco minutos: es una
cuesta cada vez mas empinada, y se deshace sola cuando el atacante para. Un humano que
se equivoca dos veces apenas lo nota; una maquina que prueba mil claves se ahoga.

UN CONTADOR POR ACCION: login, registro y refresh se cuentan APARTE. Mezclarlos seria
injusto: los fallos de login de alguien frenarian su renovacion de sesion, que no ha
hecho nada malo.

POR QUE NI EL EMAIL NI LA IP VIAJAN EN CLARO: las claves llevan HUELLAS (HMAC-SHA256 con
un secreto del entorno, jamas del repositorio). Quien se asome al almacen no obtiene una
lista de clientes ni un mapa de quien se conecta desde donde.

TRES DIMENSIONES, PORQUE HAY TRES ATAQUES (dictamen CSA c): por IP (uno prueba mil
claves), por CUENTA (mil maquinas contra una victima) y por IP+CUENTA (ataque dirigido).
Un solo contador dejaria pasar dos de los tres.

POR QUE NO HAY CONTADOR GLOBAL (el CSA lo RECOMIENDA, no lo exige; se descarta con
motivo): un limite global es una palanca de DoS regalada: un atacante barato lo dispara
y deja fuera a TODOS los usuarios a la vez. Es la misma trampa (b) del dictamen, pero a
escala de plataforma. El contador por subred/ASN si seria util, pero hoy no hay forma
fiable de extraer ese dato (el propio dictamen admite no bloquear por ello en P06b).
Ambos quedan registrados en el barrido de seguridad con este motivo.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

DIGEST_SECRET_ENV_VAR = "CE_V5_RATE_LIMIT_SECRET"
_MIN_SECRET_LENGTH = 32

# Acciones protegidas. Cada una cuenta por separado (ver docstring).
ACTION_LOGIN = "login"
ACTION_REGISTER = "register"
ACTION_REFRESH = "refresh"
ACTION_REALTIME = "realtime"

# Nombres de las tres dimensiones (dictamen CSA punto c).
DIMENSION_IP = "ip"
DIMENSION_ACCOUNT = "account"
DIMENSION_IP_ACCOUNT = "ip_account"

# Segmento de las llaves de bloqueo, para distinguirlas de los contadores.
BLOCK_MARKER = "block"


class RateLimitConfigError(RuntimeError):
    """Error de configuracion del limitador."""


class RateLimiterUnavailableError(RuntimeError):
    """El limitador no responde: NO se autentica a nadie (fail-closed, dictamen CSA d).

    Sin contador no hay limite, y sin limite la fuerza bruta es gratis. Preferimos
    denegar unos minutos a dejar entrar a cualquiera. Al usuario se le da una respuesta
    GENERICA; el motivo real (rate_limiter_unavailable) se queda dentro.

    Lleva la ACCION que se estaba intentando: el log debe decir QUE se estaba haciendo,
    no solo por que ruta entro.
    """

    def __init__(self, action: str) -> None:
        super().__init__(
            "El limitador de intentos no responde: no se autentica a nadie."
        )
        self.action = action


@dataclass(frozen=True, slots=True)
class RateLimitRule:
    """Cuantos fallos se toleran en una ventana, para UNA dimension."""

    max_failures: int
    window_seconds: int


@dataclass(frozen=True, slots=True)
class RateLimitConfig:
    """Umbrales y secreto de las huellas.

    IP+CUENTA es el mas ESTRECHO (5): esa combinacion es la firma exacta de un ataque
    dirigido contra una persona concreta, y un humano no falla cinco veces seguidas su
    propia clave desde su propia maquina sin darse cuenta.
    CUENTA es intermedio (10): protege a una victima contra muchas maquinas.
    IP es el mas ANCHO (30): detras de una sola IP puede haber una oficina entera, o un
    NAT de operador; apretar aqui castigaria a inocentes.
    """

    digest_secret: str
    by_ip: RateLimitRule = field(
        default_factory=lambda: RateLimitRule(max_failures=30, window_seconds=300)
    )
    by_account: RateLimitRule = field(
        default_factory=lambda: RateLimitRule(max_failures=10, window_seconds=300)
    )
    by_ip_account: RateLimitRule = field(
        default_factory=lambda: RateLimitRule(max_failures=5, window_seconds=300)
    )
    max_retry_after_seconds: int = 300

    def __post_init__(self) -> None:
        if len(self.digest_secret) < _MIN_SECRET_LENGTH:
            raise RateLimitConfigError(
                f"El secreto de las huellas debe tener al menos {_MIN_SECRET_LENGTH} "
                "caracteres: con uno debil, quien se asome al almacen podria "
                "recomputar las huellas y reconstruir emails e IPs."
            )
        for rule in (self.by_ip, self.by_account, self.by_ip_account):
            if rule.max_failures <= 0 or rule.window_seconds <= 0:
                raise RateLimitConfigError(
                    "max_failures y window_seconds deben ser positivos."
                )
        if self.max_retry_after_seconds <= 0:
            raise RateLimitConfigError("max_retry_after_seconds debe ser positivo.")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> RateLimitConfig:
        """Lee el secreto del entorno. Falla si falta o es corto."""
        env: Mapping[str, str] = os.environ if environ is None else environ
        secret = env.get(DIGEST_SECRET_ENV_VAR, "").strip()
        if not secret:
            raise RateLimitConfigError(
                f"Falta la variable de entorno {DIGEST_SECRET_ENV_VAR} con el secreto "
                "de las huellas del limitador."
            )
        return cls(digest_secret=secret)

    def rule_for(self, dimension: str) -> RateLimitRule:
        """La regla de una dimension."""
        if dimension == DIMENSION_IP:
            return self.by_ip
        if dimension == DIMENSION_ACCOUNT:
            return self.by_account
        return self.by_ip_account


def digest(value: str, secret: str) -> str:
    """Huella HMAC-SHA256. Ni el email ni la IP llegan en claro al almacen."""
    return hmac.new(
        secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def retry_after_seconds(failures: int, rule: RateLimitRule, cap: int) -> int:
    """Cuanto dura el bloqueo tras ``failures`` fallos. 0 si aun no se paso del umbral.

    Por encima del umbral crece exponencialmente (2^exceso) hasta un tope. NUNCA es
    infinito: la llave de bloqueo caduca sola, asi que nadie queda fuera para siempre.
    """
    exceso = failures - rule.max_failures
    if exceso <= 0:
        return 0
    espera: int = 2**exceso
    return min(espera, cap)


@dataclass(frozen=True, slots=True)
class Attempt:
    """Un intento contra una accion protegida, YA anonimizado.

    account_digest es None cuando la accion no tiene cuenta asociada (el refresh viaja
    en una cookie: no sabemos de quien es hasta validarla, y no vamos a validarla antes
    de aplicar el limite).
    """

    action: str
    ip_digest: str | None
    account_digest: str | None = None


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Que hacer con este intento, y por que dimension."""

    allowed: bool
    retry_after_seconds: int = 0
    dimension: str = ""  # "ip", "account" o "ip_account".


def decide(
    counters: Mapping[str, int],
    blocks: Mapping[str, int],
    config: RateLimitConfig,
) -> RateLimitDecision:
    """Deniega si hay un bloqueo VIVO en alguna dimension.

    ``blocks`` lleva el TTL restante en segundos de cada llave de bloqueo. Si no hay
    bloqueo, se permite: los contadores se conservan (no se miran aqui) solo para
    calcular el siguiente escalon cuando vuelva a fallar.
    """
    peor = RateLimitDecision(allowed=True)
    # Orden de especificidad: ante empate manda la dimension mas estrecha, que es la que
    # mejor describe lo que esta pasando.
    for dimension in (DIMENSION_IP_ACCOUNT, DIMENSION_ACCOUNT, DIMENSION_IP):
        ttl = blocks.get(dimension)
        if ttl is None or ttl <= 0:
            continue
        if ttl > peor.retry_after_seconds:
            peor = RateLimitDecision(
                allowed=False, retry_after_seconds=ttl, dimension=dimension
            )
    return peor


@runtime_checkable
class AuthRateLimiter(Protocol):
    """Puerto del limitador. La implementacion vive en infra (Redis)."""

    def check(self, attempt: Attempt) -> RateLimitDecision: ...

    def register_failure(self, attempt: Attempt) -> None: ...

    def reset(self, attempt: Attempt) -> None: ...
