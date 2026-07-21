-- Migracion 0015: lectura tenant-scoped del estado de reglas por ce_v5_app (CA-P08-06 p.7).
-- Sucesora de 0014. Append-only: ninguna migracion aplicada se edita.
--
-- STALE (y el resto del estado operacional) es "observable" -- pero observable debe ser
-- REAL: la superficie de usuario (ce_v5_app) tiene que poder LEER el estado de SUS reglas
-- (is_stale/stale_reason/is_quarantined/quarantine_reason/contadores) para exponerlo. La
-- 0013 hizo REVOKE ALL sobre rule_lifecycle_state a ce_v5_app (no fabrica estado de motor,
-- CA-P08-02 p.3). Aqui se le da SOLO LECTURA, y SOLO de su tenant:
--   - GRANT SELECT (nada de INSERT/UPDATE/DELETE): ce_v5_app lee, no escribe contadores ni
--     banderas -- eso sigue siendo exclusivo de ce_v5_rules (0013).
--   - POLICY FOR SELECT atada al tenant (USING tenant_id = app_current_tenant_id()): bajo
--     RLS ENABLE+FORCE ya activo, la lectura cross-tenant devuelve CERO filas.
-- El motor (ce_v5_rules) conserva su policy FOR ALL de 0013 intacta.

GRANT SELECT ON rule_lifecycle_state TO ce_v5_app;

CREATE POLICY rule_lifecycle_app_read ON rule_lifecycle_state
    FOR SELECT TO ce_v5_app
    USING (tenant_id = app_current_tenant_id());
