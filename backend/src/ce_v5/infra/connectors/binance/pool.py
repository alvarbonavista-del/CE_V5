"""Reparto de streams entre conexiones WebSocket. PURO, SIN IO.

Los limites NO son nuestros: los publica Binance (1024 streams por conexion, 300
conexiones por IP cada 5 minutos). Pasarse no da un error bonito: da un BANEO DE IP,
y un baneo deja sin datos a TODOS los usuarios a la vez.

Por eso el tope de conexiones va deliberadamente por debajo del publicado: preferimos
fallar nosotros, en claro y a tiempo, antes que descubrir el limite por la via de que
el exchange nos eche.
"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass


class ExchangeLimitExceeded(RuntimeError):
    """La demanda no cabe en los limites del exchange. NO se abre nada."""


@dataclass(frozen=True, slots=True)
class BinanceLimits:
    """Limites publicados por Binance (spot), con margen propio."""

    max_streams_per_connection: int = 1024
    # 300 conexiones por IP cada 5 min es el limite REAL. 200 es margen a proposito:
    # si alguna vez rozamos el techo, queremos enterarnos por una excepcion nuestra,
    # no por un baneo suyo.
    max_connections: int = 200

    def capacity(self) -> int:
        return self.max_streams_per_connection * self.max_connections


# Singleton de modulo: es inmutable (frozen), asi que compartirlo es seguro.
DEFAULT_LIMITS = BinanceLimits()


class ConnectionPlanner:
    """Decide en QUE conexion vive cada stream. Pura, sin IO.

    ESTABILIDAD ANTE CAMBIOS: un stream que ya vive en una conexion SE QUEDA DONDE
    ESTA. Solo se reubica si su conexion desaparece. Sin esto, dar de alta un solo
    stream nuevo podria recolocar los otros mil, lo que significa cerrar y reabrir mil
    suscripciones: una tormenta de reconexiones contra el exchange (rate limits,
    riesgo de baneo) y un hueco de datos en cada una. El coste de un alta debe ser
    proporcional al alta, no al total.
    """

    def __init__(self, limits: BinanceLimits = DEFAULT_LIMITS) -> None:
        self._limits = limits
        # Donde vive cada stream ahora mismo. Es el estado que da la ESTABILIDAD.
        self._asignacion: dict[str, int] = {}

    def assign(self, stream_names: AbstractSet[str]) -> Mapping[int, list[str]]:
        """Reparte los streams deseados entre conexiones. Determinista y estable."""
        deseados = set(stream_names)
        if len(deseados) > self._limits.capacity():
            msg = (
                f"exchange_limit_exceeded: {len(deseados)} streams no caben en "
                f"{self._limits.max_connections} conexiones de "
                f"{self._limits.max_streams_per_connection}. NO se abre ninguno: "
                "pasarse de los limites de Binance es arriesgar un baneo de IP, que "
                "dejaria sin datos a todos los usuarios."
            )
            raise ExchangeLimitExceeded(msg)

        # 1) Los que YA estaban y siguen queriendose: se quedan en su conexion.
        conexiones: dict[int, list[str]] = {}
        for nombre in sorted(deseados):
            indice = self._asignacion.get(nombre)
            if indice is not None:
                conexiones.setdefault(indice, []).append(nombre)

        # 2) Los NUEVOS: al primer hueco libre, en orden determinista.
        nuevos = sorted(deseados - set(self._asignacion))
        for nombre in nuevos:
            indice = self._primer_hueco(conexiones)
            conexiones.setdefault(indice, []).append(nombre)

        self._asignacion = {
            nombre: indice
            for indice, nombres in conexiones.items()
            for nombre in nombres
        }
        return {indice: sorted(nombres) for indice, nombres in conexiones.items()}

    def _primer_hueco(self, conexiones: Mapping[int, list[str]]) -> int:
        """La conexion de indice mas bajo que aun tenga sitio."""
        for indice in range(self._limits.max_connections):
            if (
                len(conexiones.get(indice, []))
                < self._limits.max_streams_per_connection
            ):
                return indice
        # No deberia llegarse: assign() ya comprobo la capacidad total.
        msg = "exchange_limit_exceeded: no queda ninguna conexion con hueco."
        raise ExchangeLimitExceeded(msg)

    def current(self) -> Mapping[str, int]:
        """Donde vive cada stream (observable)."""
        return dict(self._asignacion)
