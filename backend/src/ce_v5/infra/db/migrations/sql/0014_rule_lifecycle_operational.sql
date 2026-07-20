-- Migracion 0014: estado OPERACIONAL del ciclo de evaluacion (P08, CA-P08-05).
-- Sucesora de 0013. Append-only: ninguna migracion aplicada se edita.
--
-- La 6.2 fijo la maquina de transiciones pura (RuntimeState): ademas del estado de la
-- FSM, la regla arrastra entre velas CONTADORES y BANDERAS operacionales -- STALE (dato
-- ausente persistente, TRANSITORIO, se auto-limpia) y QUARANTINE (fallo, PEGAJOSO,
-- rearme del usuario; D3) -- cada uno con su MOTIVO. Aqui se anaden como columnas de
-- rule_lifecycle_state para que record_transition (6.3) persista el RuntimeState
-- COMPLETO. Todas con DEFAULT SEGURO: una fila queda sana (cero/false, sin motivo).
--
-- NO se toca RLS, isolation_scope ni grants: rule_lifecycle_state ya es tenant-scoped
-- con RLS ENABLE + FORCE (0013) y ce_v5_rules ya tiene SELECT/INSERT/UPDATE; las
-- columnas nuevas heredan la MISMA proteccion fila-a-fila y los MISMOS privilegios.

ALTER TABLE rule_lifecycle_state
    ADD COLUMN not_evaluable_count    integer NOT NULL DEFAULT 0,
    ADD COLUMN consecutive_exceptions integer NOT NULL DEFAULT 0,
    ADD COLUMN is_stale               boolean NOT NULL DEFAULT false,
    ADD COLUMN stale_reason           text,
    ADD COLUMN is_quarantined         boolean NOT NULL DEFAULT false,
    ADD COLUMN quarantine_reason      text,
    ADD COLUMN last_technical_error   text,
    -- Los motivos usan EXACTAMENTE los valores de los StrEnum de runtime.py (misma
    -- disciplina que el CHECK de state en 0013): un motivo desconocido es un bug.
    ADD CONSTRAINT rule_lifecycle_state_stale_reason_valido
        CHECK (stale_reason IS NULL
               OR stale_reason IN ('rule_not_evaluable', 'veto_not_evaluable')),
    ADD CONSTRAINT rule_lifecycle_state_quarantine_reason_valido
        CHECK (quarantine_reason IS NULL
               OR quarantine_reason IN ('plan_not_recomputable', 'repeated_exceptions')),
    -- last_technical_error ACOTADO: un diagnostico CORTO del ultimo fallo tecnico, jamas
    -- un payload enorme ni un secreto. El limite lo hace cumplir la base (fail-loud).
    ADD CONSTRAINT rule_lifecycle_state_tech_error_acotado
        CHECK (last_technical_error IS NULL OR length(last_technical_error) <= 500);

COMMENT ON TABLE rule_lifecycle_state IS
    'Estado del ciclo de evaluacion por regla (P08, INFORME 6 sec 11.4; CA-P08-05). isolation_scope=tenant: tenant_id con RLS ENABLE + FORCE. La escribe SOLO ce_v5_rules (el motor); ce_v5_app no tiene privilegio ni policy sobre ella (CA-P08-02 p.3). state es el StrEnum EvaluationLifecycleState (inactive/pending/firing/resolved); ademas lleva el estado OPERACIONAL de la FSM (contadores not_evaluable_count/consecutive_exceptions, banderas is_stale/is_quarantined con sus motivos stale_reason/quarantine_reason, y last_technical_error acotado).';
