-- Migracion 0006: Row Level Security sobre las tablas de tenancy (ADR-011).
-- El tenant efectivo vive en un ajuste de TRANSACCION (SET LOCAL), no en la
-- conexion: asi es correcto con pools y se descarta al terminar. Si el ajuste
-- no esta fijado, la funcion devuelve NULL, la comparacion de la policy es
-- NULL (ninguna fila visible) y una escritura con WITH CHECK NULL es
-- rechazada por PostgreSQL: fail-closed por defecto.
-- FORCE ROW LEVEL SECURITY somete tambien al dueno de la tabla. El rol de
-- aplicacion (ce_v5_app) no tiene SUPERUSER ni BYPASSRLS (migracion 0004),
-- asi que estas policies le aplican de verdad.
CREATE FUNCTION app_current_tenant_id() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$ SELECT NULLIF(current_setting('app.current_tenant_id', true), '')::uuid $$;
COMMENT ON FUNCTION app_current_tenant_id() IS
    'Tenant efectivo de la transaccion (SET LOCAL app.current_tenant_id). NULL si no esta fijado.';

CREATE FUNCTION app_current_user_id() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$ SELECT NULLIF(current_setting('app.current_user_id', true), '')::uuid $$;
COMMENT ON FUNCTION app_current_user_id() IS
    'Principal autenticado de la transaccion (SET LOCAL app.current_user_id). Lo fija el backend, nunca el cliente.';

GRANT EXECUTE ON FUNCTION app_current_tenant_id() TO ce_v5_app;
GRANT EXECUTE ON FUNCTION app_current_user_id() TO ce_v5_app;

ALTER TABLE tenant ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenant
    USING (tenant_id = app_current_tenant_id())
    WITH CHECK (tenant_id = app_current_tenant_id());

-- La pertenencia se lee ANTES de conocer el tenant (el resolver la necesita
-- para resolverlo): por eso la lectura admite tambien las filas del principal
-- autenticado. La ESCRITURA sigue exigiendo contexto de tenant.
ALTER TABLE user_tenant_membership ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_tenant_membership FORCE ROW LEVEL SECURITY;
CREATE POLICY user_tenant_membership_isolation ON user_tenant_membership
    USING (
        tenant_id = app_current_tenant_id()
        OR user_id = app_current_user_id()
    )
    WITH CHECK (tenant_id = app_current_tenant_id());
