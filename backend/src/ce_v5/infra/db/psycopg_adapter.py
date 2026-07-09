"""Adapter de PostgreSQL basado en psycopg 3.

Implementa los puertos Database/Session de ports.py. Es el UNICO fichero
de infra/db que conoce psycopg; el resto del sistema depende de los
Protocol, no del driver (REST-15, ADR-013).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg import Connection, Cursor
from psycopg.rows import TupleRow

from ce_v5.infra.db.config import DbConfig
from ce_v5.infra.db.ports import Session, SqlParams


class _PsycopgSession:
    """Sesion que delega en un cursor de psycopg (cumple el Protocol Session)."""

    def __init__(self, cursor: Cursor[TupleRow]) -> None:
        self._cursor = cursor

    def execute(self, query: str, params: SqlParams = None) -> None:
        self._cursor.execute(query, params)

    def fetchone(
        self, query: str, params: SqlParams = None
    ) -> tuple[object, ...] | None:
        self._cursor.execute(query, params)
        return self._cursor.fetchone()

    def fetchall(
        self, query: str, params: SqlParams = None
    ) -> list[tuple[object, ...]]:
        self._cursor.execute(query, params)
        return self._cursor.fetchall()


class PsycopgDatabase:
    """Adapter concreto de PostgreSQL (cumple el Protocol Database).

    Mantiene una conexion perezosa (se abre al primer uso) con autocommit
    desactivado, de modo que cada bloque transaction() sea una transaccion
    real y atomica.
    """

    def __init__(self, config: DbConfig) -> None:
        self._config = config
        self._conn: Connection[TupleRow] | None = None

    def _connection(self) -> Connection[TupleRow]:
        conn = self._conn
        if conn is None or conn.closed:
            conn = psycopg.connect(self._config.dsn, autocommit=False)
            self._conn = conn
        return conn

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        conn = self._connection()
        with conn.transaction(), conn.cursor() as cursor:
            yield _PsycopgSession(cursor)

    def close(self) -> None:
        conn = self._conn
        if conn is not None and not conn.closed:
            conn.close()
        self._conn = None
