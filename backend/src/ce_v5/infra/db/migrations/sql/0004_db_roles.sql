-- Migracion 0004: separacion de roles de base de datos (ADR-011).
-- El rol de aplicacion (ce_v5_app) NO es dueno de las tablas y no tiene
-- SUPERUSER ni BYPASSRLS: por eso las policies de RLS (migracion 0006) le
-- aplican de verdad. El rol de migraciones (dueno de las tablas) NO corre
-- en runtime. Aqui se crea el rol sin LOGIN; su credencial de conexion la
-- provisiona ce_v5.infra.db.provision desde el entorno (nunca en el repo).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ce_v5_app') THEN
        CREATE ROLE ce_v5_app NOLOGIN NOSUPERUSER NOBYPASSRLS
            NOCREATEDB NOCREATEROLE NOREPLICATION INHERIT;
    END IF;
END
$$;

-- Tablas tecnicas de sistema (P02b): el rol de aplicacion opera sobre ellas
-- lo justo (encolar y drenar outbox, dedup de inbox, audit tecnico). No son
-- superficie de consulta por usuario y no llevan tenant_id (isolation_scope=
-- system, allowlistadas en el check 7.8).
GRANT SELECT, INSERT, UPDATE, DELETE ON outbox TO ce_v5_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON inbox TO ce_v5_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON audit_log TO ce_v5_app;
GRANT SELECT ON schema_migrations TO ce_v5_app;

-- schema_migrations la crea el runner, no una migracion: se declara aqui su
-- alcance para que ninguna tabla quede sin isolation_scope (ADR-011, 7.8).
COMMENT ON TABLE schema_migrations IS
    'Registro de migraciones aplicadas (P02b). isolation_scope=system; sin tenant.';
