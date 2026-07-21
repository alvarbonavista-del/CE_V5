-- Migracion 0013: motor de reglas (P08, ADR-015/016/017) + rol de reglas
-- (regla 5.20, CA-P08-02/03). Sucesora de 0012. Append-only: ninguna migracion
-- aplicada se edita.
--
-- REGLA 5.20 - NADIE FABRICA HECHOS AJENOS, aplicada a P08. Se separa el poder en
-- dos: la AUTORIA de una regla (rule_definition) la escribe ce_v5_app, la superficie
-- de usuario; el ESTADO del ciclo de evaluacion (rule_lifecycle_state) lo escribe SOLO
-- ce_v5_rules, el motor. ce_v5_app NO toca el estado (CA-P08-02 p.3) y ce_v5_rules NO
-- toca la autoria fila a fila: la lee cross-tenant por la VENTANILLA rules_for_market,
-- exactamente como el ingestor lee la demanda por market_public_demand (0012). Lo hace
-- cumplir el MOTOR (grants + RLS + policies de outbox), no la buena conducta del codigo.

-- a) Rol de reglas sin LOGIN; la credencial la provisiona el entorno (CE-13). Mismo
--    patron y mismos flags que 0004 (ce_v5_app), 0008 (ce_v5_operator) y 0012
--    (ce_v5_ingestion).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ce_v5_rules') THEN
        CREATE ROLE ce_v5_rules NOLOGIN NOSUPERUSER NOBYPASSRLS
            NOCREATEDB NOCREATEROLE NOREPLICATION INHERIT;
    END IF;
END
$$;

-- b) AUTORIA DE REGLAS (rule_definition). isolation_scope=tenant: la regla es dato DEL
--    SUJETO (tenant), con RLS ENABLE + FORCE atada al contexto transaccional de P05. La
--    ESCRIBE ce_v5_app (superficie de usuario); ce_v5_rules NO la toca fila a fila.
--    canonical_rule_hash es el hash de evaluacion (ADR-017, Bloque 1); definition es la
--    forma canonica persistida (jsonb). schema_version y canonical_rule_hash viajan
--    juntos para que el motor sepe con que version se calculo.
CREATE TABLE rule_definition (
    rule_id             uuid PRIMARY KEY,
    tenant_id           uuid NOT NULL REFERENCES tenant (tenant_id) ON DELETE CASCADE,
    exchange            text NOT NULL,
    symbol              text NOT NULL,
    evaluation_contexts text[] NOT NULL,
    product             text NOT NULL,
    name                text NOT NULL,
    canonical_rule_hash text NOT NULL,
    schema_version      integer NOT NULL,
    enabled             boolean NOT NULL,
    definition          jsonb NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    -- El producto solo puede ser uno de los dos canonicos (ADR-016).
    CONSTRAINT rule_definition_product_valido
        CHECK (product IN ('alert', 'trading_signal'))
);
CREATE INDEX rule_definition_tenant_idx
    ON rule_definition (tenant_id);
CREATE INDEX rule_definition_hash_idx
    ON rule_definition (canonical_rule_hash);
-- Soporte de la ventanilla: filtrar por exchange+symbol (btree) y por pertenencia del
-- timeframe al array de contextos (GIN sobre text[]).
CREATE INDEX rule_definition_market_idx
    ON rule_definition (exchange, symbol);
CREATE INDEX rule_definition_contexts_idx
    ON rule_definition USING gin (evaluation_contexts);
COMMENT ON TABLE rule_definition IS
    'Autoria de reglas (P08, ADR-015/016/017). isolation_scope=tenant: la regla es dato del sujeto, tenant_id con RLS ENABLE + FORCE. La escribe ce_v5_app; ce_v5_rules NO la toca fila a fila (la lee cross-tenant por la ventanilla rules_for_market). definition guarda la forma canonica; canonical_rule_hash es el hash de evaluacion (ADR-017).';

ALTER TABLE rule_definition ENABLE ROW LEVEL SECURITY;
ALTER TABLE rule_definition FORCE ROW LEVEL SECURITY;
CREATE POLICY rule_definition_isolation ON rule_definition
    FOR ALL TO ce_v5_app
    USING (tenant_id = app_current_tenant_id())
    WITH CHECK (tenant_id = app_current_tenant_id());

-- Policy del DUENO de la ventanilla (imita market_intent_owner_read de 0012).
-- FORCE RLS somete tambien al dueno; sin esta policy la ventanilla (SECURITY DEFINER,
-- corre como el dueno) veria CERO FILAS. Va ESTRECHADA a proposito:
--   FOR SELECT      -> solo lectura: el dueno no escribe reglas por esta via.
--   enabled = true  -> el dueno solo ve por aqui lo que la ventanilla expone.
-- NO se aplica a NINGUN rol de runtime; esta allowlistada en tools/check_tenancy.py.
DO $$
BEGIN
    EXECUTE format(
        'CREATE POLICY rule_definition_owner_read ON rule_definition '
        'FOR SELECT TO %I USING (enabled = true)',
        current_user);
END
$$;

-- c) ESTADO DEL CICLO DE EVALUACION (rule_lifecycle_state). isolation_scope=tenant. La
--    escribe SOLO ce_v5_rules (el motor); ce_v5_app NO tiene ningun privilegio ni policy
--    sobre ella (CA-P08-02 p.3: la superficie de usuario no fabrica estado de motor). El
--    CHECK usa EXACTAMENTE los valores del StrEnum EvaluationLifecycleState del contrato.
CREATE TABLE rule_lifecycle_state (
    rule_id                  uuid PRIMARY KEY
        REFERENCES rule_definition (rule_id) ON DELETE CASCADE,
    tenant_id                uuid NOT NULL,
    state                    text NOT NULL,
    last_evaluated_open_time bigint,
    updated_at               timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT rule_lifecycle_state_valido
        CHECK (state IN ('inactive', 'pending', 'firing', 'resolved'))
);
CREATE INDEX rule_lifecycle_state_tenant_idx
    ON rule_lifecycle_state (tenant_id);
COMMENT ON TABLE rule_lifecycle_state IS
    'Estado del ciclo de evaluacion por regla (P08, INFORME 6 sec 11.4). isolation_scope=tenant: tenant_id con RLS ENABLE + FORCE. La escribe SOLO ce_v5_rules (el motor); ce_v5_app no tiene privilegio ni policy sobre ella (CA-P08-02 p.3). state es el StrEnum EvaluationLifecycleState (inactive/pending/firing/resolved).';

ALTER TABLE rule_lifecycle_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE rule_lifecycle_state FORCE ROW LEVEL SECURITY;
CREATE POLICY rule_lifecycle_isolation ON rule_lifecycle_state
    FOR ALL TO ce_v5_rules
    USING (tenant_id = app_current_tenant_id())
    WITH CHECK (tenant_id = app_current_tenant_id());

-- d) LA VENTANILLA CROSS-TENANT (imita market_public_demand de 0012).
--    PROBLEMA: el motor evalua CADA tick de mercado contra las reglas de TODOS los
--    tenants interesados en ese par+timeframe. Esa lectura es CROSS-TENANT por
--    naturaleza, pero la RLS de P05 impide a ce_v5_rules leer reglas de otros tenants (y
--    hace bien).
--    SOLUCION (mismo patron que 0012): una SECURITY DEFINER que corre como su dueno (el
--    rol de migraciones), con search_path FIJO y sin SQL dinamico, que devuelve SOLO lo
--    que el motor necesita para evaluar y proyectar. NUNCA dato de sujeto: nada de
--    user_id, owner, email, plan ni name. El tenant_id SI sale porque el motor debe
--    proyectar la senal/alerta acotada a su tenant, pero ningun identificador de persona.
CREATE FUNCTION rules_for_market(p_exchange text, p_symbol text, p_timeframe text)
RETURNS TABLE (
    rule_id             uuid,
    tenant_id           uuid,
    product             text,
    canonical_rule_hash text,
    schema_version      integer,
    definition          jsonb
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT r.rule_id, r.tenant_id, r.product, r.canonical_rule_hash,
           r.schema_version, r.definition
    FROM rule_definition r
    WHERE r.exchange = p_exchange
      AND r.symbol = p_symbol
      AND p_timeframe = ANY(r.evaluation_contexts)
      AND r.enabled = true
$$;
COMMENT ON FUNCTION rules_for_market(text, text, text) IS
    'Ventanilla cross-tenant (P08, patron CA-P07-D de 0012): las reglas HABILITADAS de TODOS los tenants para un par+timeframe. Devuelve solo lo que el motor necesita para evaluar y proyectar (rule_id, tenant_id, product, canonical_rule_hash, schema_version, definition); NUNCA dato de sujeto (user_id, owner, email, plan, name). Solo la ejecuta ce_v5_rules.';

REVOKE EXECUTE ON FUNCTION rules_for_market(text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rules_for_market(text, text, text) TO ce_v5_rules;

-- e) PRIVILEGIOS ESTRECHOS (regla 5.20). Nada de GRANT ALL.
-- La superficie de usuario escribe la AUTORIA; no toca el estado del motor.
GRANT SELECT, INSERT, UPDATE, DELETE ON rule_definition TO ce_v5_app;
-- El motor escribe el ESTADO (sin DELETE: el estado se reinicia, no se borra); la
-- autoria la ve solo por la ventanilla.
GRANT SELECT, INSERT, UPDATE ON rule_lifecycle_state TO ce_v5_rules;

-- f) NEGATIVOS / DEFENSA EN PROFUNDIDAD (check 1 de CA-P08-03). Rol nuevo = sin
--    privilegios por defecto; estos REVOKE lo hacen explicito y auditable, como 0012.
-- El motor lee la autoria SOLO por la ventanilla: nada directo sobre rule_definition.
REVOKE ALL ON rule_definition FROM ce_v5_rules;
-- La superficie de usuario NO fabrica estado de motor (CA-P08-02 p.3).
REVOKE ALL ON rule_lifecycle_state FROM ce_v5_app;
-- El motor no toca market data, ni identidad, ni politica/auditoria. No hay tablas de
-- billing ni de execution todavia (M5+): el rol nace sin privilegio sobre ellas.
REVOKE ALL ON market_candle, market_instrument, market_subscription_intent
    FROM ce_v5_rules;
REVOKE ALL ON app_user, user_credential, user_session FROM ce_v5_rules;
REVOKE ALL ON policy_version, policy_rule, kill_switch, operator_audit,
    policy_entitlement, policy_override, sensitive_action_audit FROM ce_v5_rules;

-- g) OUTBOX ACOTADA para ce_v5_rules (imita outbox_ingestion_* de 0012, CA-04). El motor
--    encola SOLO rule.*, signal.* y alert.*; un motor comprometido NO puede fabricar un
--    execution.* ni un policy.* falso: se lo impide el MOTOR. Necesita SELECT y UPDATE
--    ademas de INSERT porque drena su propia outbox y marca published_at; sus policies de
--    SELECT/UPDATE lo limitan a SUS PROPIAS familias.
GRANT SELECT, INSERT, UPDATE ON outbox TO ce_v5_rules;
CREATE POLICY outbox_rules_insert ON outbox
    FOR INSERT TO ce_v5_rules
    WITH CHECK (
        event_type LIKE 'rule.%'
        OR event_type LIKE 'signal.%'
        OR event_type LIKE 'alert.%'
    );
CREATE POLICY outbox_rules_read ON outbox
    FOR SELECT TO ce_v5_rules
    USING (
        event_type LIKE 'rule.%'
        OR event_type LIKE 'signal.%'
        OR event_type LIKE 'alert.%'
    );
CREATE POLICY outbox_rules_update ON outbox
    FOR UPDATE TO ce_v5_rules
    USING (
        event_type LIKE 'rule.%'
        OR event_type LIKE 'signal.%'
        OR event_type LIKE 'alert.%'
    )
    WITH CHECK (
        event_type LIKE 'rule.%'
        OR event_type LIKE 'signal.%'
        OR event_type LIKE 'alert.%'
    );
REVOKE DELETE, TRUNCATE ON outbox FROM ce_v5_rules;
