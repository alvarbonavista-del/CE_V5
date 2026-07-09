"""Persistencia base sobre PostgreSQL: conexion, transacciones y adapters.

Puertos (Protocol) y adapter psycopg de la pieza P02b (ADR-013). Sin RLS
ni tenancy (eso es P05).
"""

from ce_v5.infra.db.config import DbConfig, DbConfigError
from ce_v5.infra.db.outbox import OutboxEvent, enqueue_event, write_atomically
from ce_v5.infra.db.ports import Database, Session, SqlParams
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase

__all__ = [
    "Database",
    "DbConfig",
    "DbConfigError",
    "OutboxEvent",
    "PsycopgDatabase",
    "Session",
    "SqlParams",
    "enqueue_event",
    "write_atomically",
]
