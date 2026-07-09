-- Migracion 0003: audit tecnico minimo.
-- Registro tecnico append-only de acciones de infraestructura. No es el
-- historico canonico de eventos (ese vive en la outbox / DB de dominio).
-- Tabla tecnica de sistema, sin tenant.

CREATE TABLE audit_log (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    action      text NOT NULL,
    detail      jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE audit_log IS
    'Audit tecnico minimo (P02b). isolation_scope=system; sin tenant.';
