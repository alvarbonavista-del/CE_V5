-- Migracion 0007: modelo de datos de politica y auditoria (ADR-012, ADR-021).
-- Sucesora de 0006; no toca ninguna migracion anterior. Siete tablas: catalogo
-- de plataforma (system), concesiones/overrides por sujeto (tenant/user) y dos
-- auditorias separadas por alcance. tenant_id/user_id son uuid, como en P05;
-- user_id NUNCA lleva FK (las cuentas reales son P06b, P05 D3). Los roles, los
-- privilegios y el RLS se activan en 0008; aqui solo estructura, CHECK e indices.

-- 1) policy_version: la edicion vigente del reglamento (catalogo de plataforma).
CREATE TABLE policy_version (
    policy_version          text PRIMARY KEY,
    status                  text NOT NULL
        CHECK (status IN ('draft', 'current', 'superseded')),
    previous_policy_version text NULL REFERENCES policy_version (policy_version),
    actor                   text NOT NULL,
    reason                  text NULL,
    created_at              timestamptz NOT NULL DEFAULT now(),
    published_at            timestamptz NULL
);
-- Una sola fila puede estar en vigor a la vez.
CREATE UNIQUE INDEX policy_version_una_current
    ON policy_version (status)
    WHERE status = 'current';
COMMENT ON TABLE policy_version IS
    'Catalogo de plataforma: la edicion vigente del reglamento (ADR-005/012). published_at es COMODIDAD DE CONSULTA, no auditoria canonica: el operador tiene UPDATE sobre esta fila y podria reescribir su propia traza; la traza canonica de la publicacion vive en operator_audit (CA-05). isolation_scope=system.';

-- 2) policy_rule: reglamento por jurisdiccion/plan/rol. DATO de negocio.
CREATE TABLE policy_rule (
    rule_id            uuid PRIMARY KEY,
    policy_version     text NOT NULL REFERENCES policy_version (policy_version),
    capability_id      text NOT NULL,
    effect             text NOT NULL CHECK (effect IN ('allow', 'deny')),
    reason_code        text NOT NULL,
    match_jurisdiction text NULL,
    match_plan         text NULL,
    match_role         text NULL,
    match_kyc_status   text NULL
        CHECK (match_kyc_status IN ('verified', 'unverified', 'unknown')),
    match_vpn          boolean NULL,
    created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX policy_rule_version_capability_idx
    ON policy_rule (policy_version, capability_id);
COMMENT ON TABLE policy_rule IS
    'Reglamento por jurisdiccion/plan/rol; DATO de negocio de Alvaro, no codigo (ADR-012). El repositorio no siembra datos comerciales. isolation_scope=system.';

-- 3) policy_entitlement: lo que un sujeto tiene concedido.
CREATE TABLE policy_entitlement (
    entitlement_id  uuid PRIMARY KEY,
    tenant_id       uuid NOT NULL REFERENCES tenant (tenant_id),
    user_id         uuid NULL,
    capability_id   text NOT NULL,
    source          text NOT NULL CHECK (source IN ('plan', 'purchase', 'admin')),
    granted_at      timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz NULL
);
CREATE INDEX policy_entitlement_tenant_capability_idx
    ON policy_entitlement (tenant_id, capability_id);
COMMENT ON TABLE policy_entitlement IS
    'Lo que un sujeto tiene concedido; user_id NULL => alcance de tenant. isolation_scope=tenant/user.';

-- 4) policy_override: excepcion por sujeto. DENY vence; ALLOW no amplia.
CREATE TABLE policy_override (
    override_id     uuid PRIMARY KEY,
    tenant_id       uuid NOT NULL REFERENCES tenant (tenant_id),
    user_id         uuid NULL,
    capability_id   text NOT NULL,
    effect          text NOT NULL CHECK (effect IN ('allow', 'deny')),
    reason_code     text NOT NULL,
    actor           text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz NULL
);
CREATE INDEX policy_override_tenant_capability_idx
    ON policy_override (tenant_id, capability_id);
COMMENT ON TABLE policy_override IS
    'Excepcion por sujeto: un ALLOW solo concede dentro del perimetro de las politicas superiores, nunca amplia; un DENY siempre vence (se hace cumplir en el motor, B4). isolation_scope=tenant/user.';

-- 5) kill_switch: artefacto de OPERADOR, no de tenant. Solo se desactiva.
CREATE TABLE kill_switch (
    kill_switch_id  uuid PRIMARY KEY,
    scope           text NOT NULL
        CHECK (scope IN ('global', 'exchange', 'connector', 'market_scope',
                         'capability', 'tenant', 'user')),
    target_ref      text NULL,
    tenant_id       uuid NULL,
    user_id         uuid NULL,
    active          boolean NOT NULL DEFAULT true,
    reason_code     text NOT NULL,
    actor           text NOT NULL,
    activated_at    timestamptz NOT NULL DEFAULT now(),
    deactivated_at  timestamptz NULL,
    -- Espejo exacto del validador de KillSwitchPayload (contracts/source).
    CONSTRAINT kill_switch_scope_coherente CHECK (
        (scope = 'global'
            AND target_ref IS NULL AND tenant_id IS NULL AND user_id IS NULL)
        OR (scope IN ('exchange', 'connector', 'market_scope', 'capability')
            AND target_ref IS NOT NULL AND tenant_id IS NULL AND user_id IS NULL)
        OR (scope = 'tenant'
            AND tenant_id IS NOT NULL AND user_id IS NULL AND target_ref IS NULL)
        OR (scope = 'user'
            AND tenant_id IS NOT NULL AND user_id IS NOT NULL
            AND target_ref IS NULL)
    ),
    CONSTRAINT kill_switch_desactivacion CHECK (
        active OR deactivated_at IS NOT NULL
    )
);
-- No puede haber dos switches activos identicos para el mismo objetivo.
-- NULLS NOT DISTINCT: dos NULL cuentan como iguales (PostgreSQL 15+).
CREATE UNIQUE INDEX kill_switch_activo_unico
    ON kill_switch (scope, target_ref, tenant_id, user_id)
    NULLS NOT DISTINCT
    WHERE active;
COMMENT ON TABLE kill_switch IS
    'Artefacto de operador, no de tenant (CA-03). El rol de aplicacion solo LEE; escribe solo ce_v5_operator. No se borra: se desactiva. isolation_scope=system.';

-- 6) operator_audit: auditoria canonica de TODA accion de operador (CA-05).
CREATE TABLE operator_audit (
    audit_id          uuid PRIMARY KEY,
    action            text NOT NULL CHECK (action IN (
        'kill_switch_activated', 'kill_switch_deactivated',
        'policy_version_published')),
    actor             text NOT NULL,
    reason_code       text NOT NULL,
    kill_switch_id    uuid NULL REFERENCES kill_switch (kill_switch_id),
    policy_version    text NULL,
    previous_current  text NULL,
    new_current       text NULL,
    correlation_id    text NOT NULL,
    event_id          text NOT NULL,
    recorded_at       timestamptz NOT NULL DEFAULT now(),
    -- Coherencia por accion: los kill switch exigen kill_switch_id y NO llevan
    -- previous_current/new_current; la publicacion exige policy_version y
    -- new_current, y NO lleva kill_switch_id.
    CONSTRAINT operator_audit_coherente CHECK (
        (action IN ('kill_switch_activated', 'kill_switch_deactivated')
            AND kill_switch_id IS NOT NULL
            AND previous_current IS NULL AND new_current IS NULL)
        OR (action = 'policy_version_published'
            AND policy_version IS NOT NULL
            AND new_current IS NOT NULL
            AND kill_switch_id IS NULL)
    )
);
COMMENT ON TABLE operator_audit IS
    'Auditoria CANONICA de la accion de OPERADOR (CA-05): el operador INSERTA pero NO puede editar ni borrar su propia traza (append-only real). Distinta de sensitive_action_audit (seguridad por sujeto, tenant-scoped) y del audit_log de P02b (traza tecnica de infraestructura). isolation_scope=system.';

-- 7) sensitive_action_audit: auditoria de seguridad por sujeto (ADR-012).
CREATE TABLE sensitive_action_audit (
    audit_id        uuid PRIMARY KEY,
    tenant_id       uuid NOT NULL REFERENCES tenant (tenant_id),
    user_id         uuid NULL,
    capability_id   text NOT NULL,
    decision        text NOT NULL
        CHECK (decision IN ('allow', 'deny', 'not_applicable')),
    reason_code     text NOT NULL,
    policy_version  text NOT NULL,
    sensitive       boolean NOT NULL,
    -- Entradas resumidas (jurisdiccion, plan, kyc, vpn, kill_switch_id que
    -- gano). NUNCA datos personales crudos ni credenciales.
    context         jsonb NULL,
    evaluated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sensitive_action_audit_lookup_idx
    ON sensitive_action_audit (tenant_id, capability_id, evaluated_at);
COMMENT ON TABLE sensitive_action_audit IS
    'Auditoria de SEGURIDAD por sujeto (ADR-012), distinta del audit_log tecnico de P02b. Append-only real: UPDATE/DELETE/TRUNCATE revocados en 0008. isolation_scope=tenant/user.';
