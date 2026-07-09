"""Validacion en caliente de P02b: escritura transaccional con outbox.

Demuestra contra la DB local (docker) que:
  1) una escritura atomica deja fila de negocio + fila de outbox, y
  2) un rollback (fallo en la outbox por idempotency_key duplicado) no deja
     ni la fila de negocio ni una segunda fila de outbox.
Requiere CE_V5_DATABASE_URL. NUNCA datos reales: base de datos de juguete.
Uso: uv run python tools/validate_p02b_outbox.py
"""

from __future__ import annotations

import uuid

import psycopg

from ce_v5.infra.db.config import DbConfig
from ce_v5.infra.db.migrations.runner import apply_migrations
from ce_v5.infra.db.outbox import OutboxEvent, write_atomically
from ce_v5.infra.db.ports import Database
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase


def _count(db: Database, query: str) -> int:
    with db.transaction() as session:
        row = session.fetchone(query)
    assert row is not None
    value = row[0]
    assert isinstance(value, int)
    return value


def main() -> None:
    db = PsycopgDatabase(DbConfig.from_env())
    try:
        apply_migrations(db)
        with db.transaction() as session:
            session.execute("CREATE TEMP TABLE demo_negocio (nota text NOT NULL)")
            session.execute("TRUNCATE outbox")

        event = OutboxEvent(
            event_id=uuid.uuid4(),
            idempotency_key="idem-" + uuid.uuid4().hex,
            stream_key="stream-demo",
            event_type="component.demo",
            envelope={"hello": "world"},
        )
        write_atomically(
            db,
            business=[("INSERT INTO demo_negocio (nota) VALUES (%s)", ["ok"])],
            event=event,
        )
        negocio = _count(db, "SELECT count(*) FROM demo_negocio")
        outbox = _count(db, "SELECT count(*) FROM outbox")
        print(
            f"[1] COMMIT   -> demo_negocio={negocio}  outbox={outbox}  (esperado 1 y 1)"
        )

        duplicate = OutboxEvent(
            event_id=uuid.uuid4(),
            idempotency_key=event.idempotency_key,
            stream_key=event.stream_key,
            event_type=event.event_type,
            envelope={"hello": "again"},
        )
        rolled_back = False
        try:
            write_atomically(
                db,
                business=[("INSERT INTO demo_negocio (nota) VALUES (%s)", ["segunda"])],
                event=duplicate,
            )
        except psycopg.Error as exc:
            rolled_back = True
            print(f"[2] FALLO OUTBOX esperado -> {type(exc).__name__}")

        negocio = _count(db, "SELECT count(*) FROM demo_negocio")
        outbox = _count(db, "SELECT count(*) FROM outbox")
        print(
            f"[3] ROLLBACK -> demo_negocio={negocio}  outbox={outbox}  "
            "(esperado 1 y 1: la segunda no entro)"
        )

        if rolled_back and negocio == 1 and outbox == 1:
            print("RESULTADO: OK - atomicidad DB-outbox demostrada.")
        else:
            raise SystemExit("RESULTADO: FALLO - la atomicidad no se cumplio.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
