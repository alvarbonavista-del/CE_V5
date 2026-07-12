-- Migracion 0009: acotar por el MOTOR la outbox del operador (CA-04 p.3).
-- Sucesora de 0008. El operador puede DENEGAR DE MAS (activar/desactivar kill
-- switches, invalidar sujetos, publicar versiones) pero NUNCA FABRICAR HECHOS:
-- solo puede encolar los cuatro event_type de policy.*, jamas un execution.* o
-- signal.* falso.
--
-- Por que RLS y no un CHECK de tabla: un CHECK de tabla no distingue rol y
-- prohibiria execution.* a TODOS, incluidos los productores legitimos de M5. La
-- RLS SI distingue rol: el rol de aplicacion sigue encolando cualquier familia
-- que produzca (P02b/P03 no cambian de comportamiento); el operador queda
-- acotado a policy.*.

-- El operador solo INSERTA en la outbox; publicar/drenar es del runtime (nada
-- de SELECT, UPDATE ni DELETE para el).
GRANT INSERT ON outbox TO ce_v5_operator;

-- RLS sobre la outbox de P02b. FORCE somete tambien al dueno (las migraciones
-- corren como superusuario y la esquivan, por eso siembran/limpian sin problema).
ALTER TABLE outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbox FORCE ROW LEVEL SECURITY;

-- Rol de aplicacion: TODO sin cambios. El OutboxPublisher de P03 necesita SELECT
-- (drenar) y UPDATE (marcar published_at); write_atomically necesita INSERT.
CREATE POLICY outbox_app_all ON outbox
    FOR ALL TO ce_v5_app
    USING (true)
    WITH CHECK (true);

-- Rol de operador: solo puede encolar los cuatro eventos de policy.*.
CREATE POLICY outbox_operator_insert ON outbox
    FOR INSERT TO ce_v5_operator
    WITH CHECK (
        event_type IN (
            'policy.kill_switch_activated',
            'policy.kill_switch_deactivated',
            'policy.version_published',
            'policy.subject_invalidated'
        )
    );

-- outbox sigue siendo isolation_scope=system y allowlistada (check 7.8); el rol
-- de aplicacion conserva SELECT/INSERT/UPDATE/DELETE (0004) mas su policy ALL,
-- asi que P02b/P03 no pierden ninguna capacidad.
