"""Tests de integracion de la persistencia (requieren PostgreSQL local).

Se saltan si no esta definido CE_V5_DATABASE_URL. NUNCA datos reales:
base de datos de juguete (DOC_ENTREGABLES sec.5).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from ce_v5.infra.db.config import DbConfig
from ce_v5.infra.db.migrations.runner import MigrationsError, apply_migrations
from ce_v5.infra.db.outbox import OutboxEvent, write_atomically
from ce_v5.infra.db.ports import Database
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase

_DSN = os.environ.get("CE_V5_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    _DSN is None, reason="requiere CE_V5_DATABASE_URL (PostgreSQL local)"
)


@pytest.fixture
def db() -> Iterator[Database]:
    assert _DSN is not None
    database = PsycopgDatabase(DbConfig(dsn=_DSN))
    apply_migrations(database)
    with database.transaction() as session:
        session.execute("CREATE TEMP TABLE demo_negocio (nota text NOT NULL)")
        session.execute("TRUNCATE outbox")
    try:
        yield database
    finally:
        database.close()


def _make_event() -> OutboxEvent:
    return OutboxEvent(
        event_id=uuid.uuid4(),
        idempotency_key="idem-" + uuid.uuid4().hex,
        stream_key="stream-demo",
        event_type="component.demo",
        envelope={"hello": "world"},
    )


def _count(db: Database, query: str) -> int:
    with db.transaction() as session:
        row = session.fetchone(query)
    assert row is not None
    value = row[0]
    assert isinstance(value, int)
    return value


def test_write_atomically_persiste_negocio_y_outbox(db: Database) -> None:
    write_atomically(
        db,
        business=[("INSERT INTO demo_negocio (nota) VALUES (%s)", ["ok"])],
        event=_make_event(),
    )
    assert _count(db, "SELECT count(*) FROM demo_negocio") == 1
    assert _count(db, "SELECT count(*) FROM outbox") == 1


def test_rollback_por_fallo_de_negocio_no_deja_nada(db: Database) -> None:
    with pytest.raises(psycopg.Error):
        write_atomically(
            db,
            business=[
                ("INSERT INTO demo_negocio (nota) VALUES (%s)", ["fila"]),
                ("INSERT INTO demo_negocio (nota) VALUES (NULL)", None),
            ],
            event=_make_event(),
        )
    assert _count(db, "SELECT count(*) FROM demo_negocio") == 0
    assert _count(db, "SELECT count(*) FROM outbox") == 0


def test_rollback_por_fallo_de_outbox_no_deja_negocio(db: Database) -> None:
    event = _make_event()
    write_atomically(
        db,
        business=[("INSERT INTO demo_negocio (nota) VALUES (%s)", ["primera"])],
        event=event,
    )
    duplicate = OutboxEvent(
        event_id=uuid.uuid4(),
        idempotency_key=event.idempotency_key,
        stream_key=event.stream_key,
        event_type=event.event_type,
        envelope={"hello": "again"},
    )
    with pytest.raises(psycopg.Error):
        write_atomically(
            db,
            business=[("INSERT INTO demo_negocio (nota) VALUES (%s)", ["segunda"])],
            event=duplicate,
        )
    assert _count(db, "SELECT count(*) FROM demo_negocio") == 1
    assert _count(db, "SELECT count(*) FROM outbox") == 1


def test_apply_migrations_es_idempotente(db: Database) -> None:
    assert apply_migrations(db) == []


def test_tamper_detecta_checksum_alterado(db: Database, tmp_path: Path) -> None:
    sql_file = tmp_path / "9001_demo_tamper.sql"
    sql_file.write_text("CREATE TABLE demo_tamper (x int);\n", encoding="utf-8")
    try:
        assert apply_migrations(db, tmp_path) == ["9001"]
        sql_file.write_text("CREATE TABLE demo_tamper (y int);\n", encoding="utf-8")
        with pytest.raises(MigrationsError):
            apply_migrations(db, tmp_path)
    finally:
        with db.transaction() as session:
            session.execute("DROP TABLE IF EXISTS demo_tamper")
            session.execute(
                "DELETE FROM schema_migrations WHERE version = %s", ["9001"]
            )
