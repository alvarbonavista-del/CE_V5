-- Migracion 0002: ledger de idempotencia de consumidor (inbox) (ADR-013).
-- Dedup por consumer_group + handler + idempotency_key. P02b entrega la
-- tabla; P03 la usara al consumir con efectos. Tabla tecnica de sistema.

CREATE TABLE inbox (
    consumer_group  text NOT NULL,
    handler         text NOT NULL,
    idempotency_key text NOT NULL,
    processed_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (consumer_group, handler, idempotency_key)
);

COMMENT ON TABLE inbox IS
    'Ledger de idempotencia de consumidor (ADR-013). isolation_scope=system.';
