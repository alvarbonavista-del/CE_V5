-- Migracion 0008: rol de operador, privilegios estrechos y RLS (CA-03, ADR-012).
-- Sucesora de 0007. Sigue el patron de 0004 (rol sin LOGIN) y 0006 (RLS).
-- Separacion de poderes: ce_v5_app (runtime, sometido a RLS) NUNCA escribe un
-- kill switch ni la auditoria de operador; ce_v5_operator (fuera de runtime)
-- opera kill switches, transiciona el status de policy_version y escribe su
-- auditoria canonica (operator_audit), y no ve datos de tenant. La escritura
-- prohibida la rechaza el MOTOR (grants + RLS), no el codigo de aplicacion.

-- a) Rol de operador sin LOGIN; la credencial la provisiona el entorno (CE-13).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ce_v5_operator') THEN
        CREATE ROLE ce_v5_operator NOLOGIN NOSUPERUSER NOBYPASSRLS
            NOCREATEDB NOCREATEROLE NOREPLICATION INHERIT;
    END IF;
END
$$;

-- b) Privilegios ESTRECHOS (nada de GRANT ALL).
-- Rol de aplicacion: lee catalogo y kill switches; escribe concesiones,
-- overrides y su propia auditoria de seguridad; NADA de la bitacora de operador.
GRANT SELECT ON policy_version TO ce_v5_app;
GRANT SELECT ON policy_rule TO ce_v5_app;
GRANT SELECT ON kill_switch TO ce_v5_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON policy_entitlement TO ce_v5_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON policy_override TO ce_v5_app;
GRANT SELECT, INSERT ON sensitive_action_audit TO ce_v5_app;

-- Rol de operador: kill switches, transiciones de status de policy_version y su
-- auditoria canonica. NO gana INSERT en policy_version (la crean las
-- migraciones), NADA sobre policy_rule, y sigue SIN acceso a ninguna tabla de
-- tenant de P05. No es un admin de plataforma.
GRANT SELECT, UPDATE ON policy_version TO ce_v5_operator;
GRANT SELECT, INSERT, UPDATE ON kill_switch TO ce_v5_operator;
GRANT SELECT, INSERT ON operator_audit TO ce_v5_operator;

-- c) REVOKE explicito y auditable (aunque no se hayan concedido: queda escrito).
REVOKE DELETE, TRUNCATE ON kill_switch FROM ce_v5_app, ce_v5_operator;
REVOKE DELETE, TRUNCATE ON operator_audit FROM ce_v5_app, ce_v5_operator;
REVOKE DELETE, TRUNCATE ON sensitive_action_audit FROM ce_v5_app, ce_v5_operator;
REVOKE UPDATE ON operator_audit FROM ce_v5_app, ce_v5_operator;
REVOKE UPDATE ON sensitive_action_audit FROM ce_v5_app, ce_v5_operator;

-- d) RLS ENABLE + FORCE en las SIETE tablas; policies por rol y por contexto.

-- Catalogo de plataforma: app y operador LEEN; el operador ademas TRANSICIONA
-- el status (UPDATE), nunca crea filas (el INSERT es de las migraciones).
ALTER TABLE policy_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE policy_version FORCE ROW LEVEL SECURITY;
CREATE POLICY policy_version_read ON policy_version
    FOR SELECT TO ce_v5_app, ce_v5_operator
    USING (true);
CREATE POLICY policy_version_operator_update ON policy_version
    FOR UPDATE TO ce_v5_operator
    USING (true)
    WITH CHECK (true);

ALTER TABLE policy_rule ENABLE ROW LEVEL SECURITY;
ALTER TABLE policy_rule FORCE ROW LEVEL SECURITY;
CREATE POLICY policy_rule_read ON policy_rule
    FOR SELECT TO ce_v5_app
    USING (true);

-- kill_switch: lo LEEN app y operador; solo el operador lo escribe. El rol de
-- aplicacion NO tiene policy de escritura: el motor rechaza su INSERT/UPDATE.
ALTER TABLE kill_switch ENABLE ROW LEVEL SECURITY;
ALTER TABLE kill_switch FORCE ROW LEVEL SECURITY;
CREATE POLICY kill_switch_read ON kill_switch
    FOR SELECT TO ce_v5_app, ce_v5_operator
    USING (true);
CREATE POLICY kill_switch_insert ON kill_switch
    FOR INSERT TO ce_v5_operator
    WITH CHECK (true);
CREATE POLICY kill_switch_update ON kill_switch
    FOR UPDATE TO ce_v5_operator
    USING (true)
    WITH CHECK (true);

-- operator_audit: auditoria canonica del operador, invisible al rol de
-- aplicacion. Solo el operador la LEE y la INSERTA; UPDATE/DELETE revocados.
ALTER TABLE operator_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE operator_audit FORCE ROW LEVEL SECURITY;
CREATE POLICY operator_audit_read ON operator_audit
    FOR SELECT TO ce_v5_operator
    USING (true);
CREATE POLICY operator_audit_insert ON operator_audit
    FOR INSERT TO ce_v5_operator
    WITH CHECK (true);

-- Concesiones y overrides: atados al contexto transaccional de P05 (0006).
-- Del propio tenant, y del propio usuario cuando la fila es de alcance usuario.
ALTER TABLE policy_entitlement ENABLE ROW LEVEL SECURITY;
ALTER TABLE policy_entitlement FORCE ROW LEVEL SECURITY;
CREATE POLICY policy_entitlement_isolation ON policy_entitlement
    USING (
        tenant_id = app_current_tenant_id()
        AND (user_id IS NULL OR user_id = app_current_user_id())
    )
    WITH CHECK (
        tenant_id = app_current_tenant_id()
        AND (user_id IS NULL OR user_id = app_current_user_id())
    );

ALTER TABLE policy_override ENABLE ROW LEVEL SECURITY;
ALTER TABLE policy_override FORCE ROW LEVEL SECURITY;
CREATE POLICY policy_override_isolation ON policy_override
    USING (
        tenant_id = app_current_tenant_id()
        AND (user_id IS NULL OR user_id = app_current_user_id())
    )
    WITH CHECK (
        tenant_id = app_current_tenant_id()
        AND (user_id IS NULL OR user_id = app_current_user_id())
    );

-- Auditoria de seguridad por sujeto: append-only. Lectura y alta del propio
-- tenant; sin UPDATE ni DELETE (revocados arriba y sin policy que los permita).
ALTER TABLE sensitive_action_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE sensitive_action_audit FORCE ROW LEVEL SECURITY;
CREATE POLICY sensitive_action_audit_read ON sensitive_action_audit
    FOR SELECT TO ce_v5_app
    USING (tenant_id = app_current_tenant_id());
CREATE POLICY sensitive_action_audit_insert ON sensitive_action_audit
    FOR INSERT TO ce_v5_app
    WITH CHECK (tenant_id = app_current_tenant_id());
