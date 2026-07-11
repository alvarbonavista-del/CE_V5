-- Migracion 0005: modelo de tenancy (ADR-011).
-- tenant es la ABSTRACCION de aislamiento; user_tenant_membership es la
-- pertenencia user -> tenant, en capa aparte. En v5.0 el tenant coincide 1:1
-- con el usuario (B2C), pero el modelo NO lo asume como eterno: la costura
-- para organizaciones futuras queda abierta y no se soporta en producto.
-- user_id NO lleva clave foranea: las cuentas de usuario reales son P06b.
-- El RLS de estas tablas se activa en la migracion 0006.
CREATE TABLE tenant (
    tenant_id   uuid PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE tenant IS
    'Tenant como abstraccion de aislamiento (ADR-011). isolation_scope=tenant.';

CREATE TABLE user_tenant_membership (
    user_id     uuid NOT NULL,
    tenant_id   uuid NOT NULL REFERENCES tenant (tenant_id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, tenant_id)
);
CREATE INDEX user_tenant_membership_tenant_idx
    ON user_tenant_membership (tenant_id);
COMMENT ON TABLE user_tenant_membership IS
    'Pertenencia user -> tenant (ADR-011). isolation_scope=user.';

GRANT SELECT, INSERT, UPDATE, DELETE ON tenant TO ce_v5_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON user_tenant_membership TO ce_v5_app;
