"""Limitador de intentos sobre Redis (P06b, CA-10; dictamen CSA punto 3).

ESTADO EFIMERO CON TTL, NO TABLA: un contador de intentos es un dato de segundos, no un
hecho del negocio. Redis lo caduca solo: el decaimiento sale gratis y no hay nada que
limpiar. Y evita crear otra tabla de identidad, que reabriria CA-07.

ATOMICIDAD REAL: contar e imponer la caducidad tiene que ser UNA operacion. Si fueran
dos (INCR y luego EXPIRE), un corte en medio dejaria un contador ETERNO: un usuario
frenado para siempre por un fallo de red. Se usa un script Lua, que Redis ejecuta entero
o nada.

EL BLOQUEO SE PLANTA EN LA MISMA PASADA QUE EL INCREMENTO: si se hiciera en una segunda
visita, quedaria una ventana entre "ya pasaste el umbral" y "ya estas frenado" por la
que un atacante colaria intentos gratis.

FAIL-CLOSED: cualquier error de Redis se convierte en RateLimiterUnavailableError. La
API deniega el login. Sin contador no hay limite.
"""

from __future__ import annotations

import redis

from ce_v5.core.auth.rate_limit import (
    BLOCK_MARKER,
    DIMENSION_ACCOUNT,
    DIMENSION_IP,
    DIMENSION_IP_ACCOUNT,
    Attempt,
    RateLimitConfig,
    RateLimitDecision,
    RateLimiterUnavailableError,
    decide,
    retry_after_seconds,
)

# INCR + EXPIRE en UNA operacion: Redis ejecuta el script entero o nada. El EXPIRE solo
# se pone en el primer fallo, para que la ventana no se renueve con cada intento (si no,
# un atacante constante mantendria el contador vivo para siempre).
_INCR_WITH_TTL = """
local c = redis.call('INCR', KEYS[1])
if c == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return c
"""

# Las dimensiones que un reset limpia: la de la IP SOLA no se toca (ver reset()).
_RESET_DIMENSIONS = (DIMENSION_ACCOUNT, DIMENSION_IP_ACCOUNT)


def _unavailable(attempt: Attempt) -> RateLimiterUnavailableError:
    """La excepcion lleva la ACCION: el log dice QUE se estaba haciendo."""
    return RateLimiterUnavailableError(attempt.action)


class RedisAuthRateLimiter:
    """Cumple el puerto AuthRateLimiter sobre Redis."""

    def __init__(
        self,
        client: redis.Redis,
        config: RateLimitConfig,
        prefix: str = "ce_v5:ratelimit",
    ) -> None:
        self._client = client
        self._config = config
        self._prefix = prefix
        self._incr = client.register_script(_INCR_WITH_TTL)

    def _suffixes(self, attempt: Attempt) -> dict[str, str]:
        """El sufijo de cada dimension aplicable a este intento.

        Sin IP conocida no hay dimension de IP; sin cuenta (el refresh no la tiene hasta
        validar la cookie) no hay dimension de cuenta. No se inventan claves.
        """
        suffixes: dict[str, str] = {}
        if attempt.ip_digest is not None:
            suffixes[DIMENSION_IP] = f"ip:{attempt.ip_digest}"
        if attempt.account_digest is not None:
            suffixes[DIMENSION_ACCOUNT] = f"account:{attempt.account_digest}"
        if attempt.ip_digest is not None and attempt.account_digest is not None:
            suffixes[DIMENSION_IP_ACCOUNT] = (
                f"ip_account:{attempt.ip_digest}:{attempt.account_digest}"
            )
        return suffixes

    def _counter_keys(self, attempt: Attempt) -> dict[str, str]:
        return {
            dimension: f"{self._prefix}:{attempt.action}:{suffix}"
            for dimension, suffix in self._suffixes(attempt).items()
        }

    def _block_keys(self, attempt: Attempt) -> dict[str, str]:
        return {
            dimension: f"{self._prefix}:{attempt.action}:{BLOCK_MARKER}:{suffix}"
            for dimension, suffix in self._suffixes(attempt).items()
        }

    def check(self, attempt: Attempt) -> RateLimitDecision:
        """LEE contadores y TTL de los bloqueos (sin incrementar) y delega en decide().

        Un intento que aun no ha fallado no puede contar como fallo: consultar el limite
        no puede ser, en si mismo, un fallo.
        """
        counter_keys = self._counter_keys(attempt)
        block_keys = self._block_keys(attempt)
        dimensiones = list(counter_keys)
        if not dimensiones:
            return RateLimitDecision(allowed=True)
        try:
            valores = self._client.mget([counter_keys[d] for d in dimensiones])
            pipe = self._client.pipeline()
            for dimension in dimensiones:
                pipe.ttl(block_keys[dimension])
            ttls = pipe.execute()
        except redis.RedisError as exc:
            raise _unavailable(attempt) from exc

        counters = {
            dimension: int(valor)
            for dimension, valor in zip(dimensiones, valores, strict=True)
            if valor is not None
        }
        # TTL negativo = la clave no existe o no caduca: no hay bloqueo vivo.
        blocks = {
            dimension: int(ttl)
            for dimension, ttl in zip(dimensiones, ttls, strict=True)
            if isinstance(ttl, int) and ttl > 0
        }
        return decide(counters, blocks, self._config)

    def register_failure(self, attempt: Attempt) -> None:
        """Incrementa (Lua) y, si se paso del umbral, PLANTA la llave de bloqueo."""
        counter_keys = self._counter_keys(attempt)
        block_keys = self._block_keys(attempt)
        try:
            for dimension, key in counter_keys.items():
                rule = self._config.rule_for(dimension)
                failures = int(self._incr(keys=[key], args=[rule.window_seconds]))
                espera = retry_after_seconds(
                    failures, rule, self._config.max_retry_after_seconds
                )
                if espera > 0:
                    # Mismo paso que el incremento: no queda ventana por la que colarse.
                    self._client.set(block_keys[dimension], failures, ex=espera)
        except redis.RedisError as exc:
            raise _unavailable(attempt) from exc

    def reset(self, attempt: Attempt) -> None:
        """Un login CORRECTO limpia contadores y bloqueos de cuenta e ip_account.

        NO limpia el contador de la IP sola: si una IP acumula fallos contra muchas
        cuentas, acertar una no debe borrarle el historial (seria la salida facil del
        atacante que ya tiene una cuenta valida).
        """
        counter_keys = self._counter_keys(attempt)
        block_keys = self._block_keys(attempt)
        a_borrar = [
            key
            for dimension in _RESET_DIMENSIONS
            for key in (counter_keys.get(dimension), block_keys.get(dimension))
            if key is not None
        ]
        if not a_borrar:
            return
        try:
            self._client.delete(*a_borrar)
        except redis.RedisError as exc:
            raise _unavailable(attempt) from exc
