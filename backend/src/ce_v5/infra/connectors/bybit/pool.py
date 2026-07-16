"""Reparto de suscripciones entre conexiones WebSocket de Bybit v5. PURO, SIN IO.

Limites PUBLICADOS por Bybit (verificados contra la doc vigente, no de memoria):
- Spot: hasta 10 args por PETICION de suscripcion (lo respeta el connector, en tandas).
- Tope de 21.000 caracteres de args por conexion; no mas de 500 conexiones cada 5 min.
- Bybit NO publica un tope explicito de topics por conexion: 200 por conexion y 20
  conexiones son techos PROPIOS conservadores.
"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass


class ExchangeLimitExceeded(RuntimeError):
    """La demanda no cabe en los limites del exchange. NO se abre nada."""


@dataclass(frozen=True, slots=True)
class BybitLimits:
    """Limites de Bybit (spot), con margen propio."""

    max_subscriptions_per_connection: int = 200
    max_connections: int = 20

    def capacity(self) -> int:
        return self.max_subscriptions_per_connection * self.max_connections


DEFAULT_LIMITS = BybitLimits()


class ConnectionPlanner:
    """Decide en QUE conexion vive cada suscripcion. Pura, sin IO.

    ESTABILIDAD ANTE CAMBIOS: una suscripcion que ya vive en una conexion SE QUEDA
    donde esta. Solo se reubica si su conexion desaparece. El coste de un alta debe ser
    proporcional al alta, no al total.
    """

    def __init__(self, limits: BybitLimits = DEFAULT_LIMITS) -> None:
        self._limits = limits
        self._asignacion: dict[str, int] = {}

    def assign(self, subscriptions: AbstractSet[str]) -> Mapping[int, list[str]]:
        """Reparte las suscripciones entre conexiones. Determinista y estable."""
        deseadas = set(subscriptions)
        if len(deseadas) > self._limits.capacity():
            msg = (
                f"exchange_limit_exceeded: {len(deseadas)} suscripciones no caben en "
                f"{self._limits.max_connections} conexiones de "
                f"{self._limits.max_subscriptions_per_connection}. NO se abre ninguna."
            )
            raise ExchangeLimitExceeded(msg)

        conexiones: dict[int, list[str]] = {}
        for nombre in sorted(deseadas):
            indice = self._asignacion.get(nombre)
            if indice is not None:
                conexiones.setdefault(indice, []).append(nombre)

        nuevas = sorted(deseadas - set(self._asignacion))
        for nombre in nuevas:
            indice = self._primer_hueco(conexiones)
            conexiones.setdefault(indice, []).append(nombre)

        self._asignacion = {
            nombre: indice
            for indice, nombres in conexiones.items()
            for nombre in nombres
        }
        return {indice: sorted(nombres) for indice, nombres in conexiones.items()}

    def _primer_hueco(self, conexiones: Mapping[int, list[str]]) -> int:
        for indice in range(self._limits.max_connections):
            if (
                len(conexiones.get(indice, []))
                < self._limits.max_subscriptions_per_connection
            ):
                return indice
        msg = "exchange_limit_exceeded: no queda ninguna conexion con hueco."
        raise ExchangeLimitExceeded(msg)

    def current(self) -> Mapping[str, int]:
        return dict(self._asignacion)
