-- Migracion 0011: discriminador de auditoria (P06b, CA-11 firmada). Sucesora de 0010.
--
-- POR QUE EXISTE: Central ordeno registrar los hechos de AUTENTICACION (login correcto,
-- refresh rotado, logout) en sensitive_action_audit, cuyo vocabulario es el de las
-- DECISIONES DE POLITICA. Al construirlo se vio que no encaja: un login correcto no tiene
-- reason_code de politica ni policy_version que lo fundamente. Se estaban FORZANDO motivos
-- de politica para hechos de auth, es decir, se estaba escribiendo una traza que MIENTE en
-- su columna de motivo. Una auditoria que miente es peor que no tenerla, porque se consulta
-- creyendola.
--
-- LA CORRECCION: un DISCRIMINADOR explicito. Cada fila declara de que tipo es, y cada tipo
-- usa su propio vocabulario de reason_code. Sin el, filtrar por policy_version devolveria
-- logins que esa version nunca goberno.

ALTER TABLE sensitive_action_audit
    ADD COLUMN audit_kind text NOT NULL DEFAULT 'policy'
    CHECK (audit_kind IN ('policy', 'auth'));

CREATE INDEX sensitive_action_audit_kind_idx
    ON sensitive_action_audit (tenant_id, audit_kind, evaluated_at);

COMMENT ON COLUMN sensitive_action_audit.audit_kind IS
    'Que clase de hecho registra la fila: policy (una decision del PolicyEvaluator) o auth (un hecho de autenticacion: login, refresh, logout). Extensible. Las filas anteriores a esta migracion son todas policy (default). Sin este discriminador, los dos vocabularios de reason_code se confundirian en la misma columna.';

COMMENT ON COLUMN sensitive_action_audit.policy_version IS
    'Para audit_kind=policy: la version que FUNDAMENTA la decision. Para audit_kind=auth: la version que estaba VIGENTE en ese instante, como CONTEXTO, no como fundamento (un login no lo decide la politica). Si no hay ninguna vigente se usa el centinela explicito "none".';

COMMENT ON TABLE sensitive_action_audit IS
    'AUDITORIA DE SEGURIDAD POR SUJETO (ADR-012, CA-11): decisiones de politica Y hechos de autenticacion del sujeto, discriminados por audit_kind. Distinta del audit_log tecnico de P02b y de operator_audit (CA-05). Append-only real: UPDATE/DELETE/TRUNCATE revocados en 0008. isolation_scope=tenant/user.';
