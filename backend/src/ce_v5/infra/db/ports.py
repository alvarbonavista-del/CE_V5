"""Puertos de persistencia: contratos que exponen los adapters de DB.

Estos Protocol permiten que otras capas dependan de una abstraccion de
sesion y transaccion, no del driver concreto (DOC_ESTRUCTURA sec.6). En
P02b el puerto y su adapter psycopg viven juntos en infra/db; el cableado
en composition root llegara cuando existan los entrypoints (ADR-013).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Protocol

# Parametros aceptados por una sentencia SQL: posicionales o con nombre.
SqlParams = Sequence[object] | Mapping[str, object] | None


class Session(Protocol):
    """Sesion activa dentro de una transaccion abierta.

    Expone lo minimo para ejecutar SQL y leer resultados sin conocer el
    driver concreto.
    """

    def execute(self, query: str, params: SqlParams = None) -> None:
        """Ejecuta una sentencia que no devuelve filas (INSERT/UPDATE/DDL)."""
        ...

    def fetchone(
        self, query: str, params: SqlParams = None
    ) -> tuple[object, ...] | None:
        """Ejecuta una consulta y devuelve la primera fila, o None."""
        ...

    def fetchall(
        self, query: str, params: SqlParams = None
    ) -> list[tuple[object, ...]]:
        """Ejecuta una consulta y devuelve todas las filas."""
        ...


class Database(Protocol):
    """Fuente de sesiones transaccionales sobre PostgreSQL."""

    def transaction(self) -> AbstractContextManager[Session]:
        """Abre una transaccion.

        Al salir sin excepcion hace COMMIT; si sale por excepcion hace
        ROLLBACK. La atomicidad es responsabilidad del adapter.
        """
        ...

    def close(self) -> None:
        """Cierra la conexion subyacente si esta abierta."""
        ...
