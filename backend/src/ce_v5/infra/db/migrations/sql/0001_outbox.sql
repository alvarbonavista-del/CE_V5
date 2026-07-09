-- Migracion 0001: tabla outbox transaccional (ADR-013).
-- Los eventos que nacen de una transaccion de negocio se escriben en la
-- misma transaccion en esta tabla. Un publisher (P03) los publicara y
-- marcara published_at de forma idempotente. Tabla tecnica de sistema,
-- sin tenant (la tenancy es P05). Identidad de evento segun ADR-003.

CREATE TABLE outbox (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id        uuid NOT NULL UNIQUE,
    idempotency_key text NOT NULL UNIQUE,
    stream_key      text NOT NULL,
    event_type      text NOT NULL,
    envelope        jsonb NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    published_at    timestamptz
);

CREATE INDEX outbox_unpublished_idx
    ON outbox (id)
    WHERE published_at IS NULL;

COMMENT ON TABLE outbox IS
    'Outbox transaccional (ADR-013). isolation_scope=system; sin tenant.';
