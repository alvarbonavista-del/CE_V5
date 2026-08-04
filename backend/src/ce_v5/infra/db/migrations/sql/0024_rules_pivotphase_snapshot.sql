-- Migracion 0024: estado de replay del motor de reglas -- snapshot de ESTADO de
-- pivotphase por barra vigente (P08c CE-14 P5; ELEVACION P08c-PIVOT-03, D-E3).
-- Sucesora de 0022 (el numero 0023 lo ocupa en paralelo P08b, ema_snapshot;
-- coordinacion 5.34). Append-only: ninguna migracion aplicada se edita (regla 5.14).
--
-- POR QUE EXISTE. pivotphase es RECURSIVE: el estado de la barra T (fase de la FSM +
-- referencias) depende del de T-1. Reproducirlo desde el origen en cada tick seria
-- O(historia); el materializador hace REPLAY DETERMINISTA desde un SNAPSHOT de estado
-- (evaluate_bar + compute_confidence, T4). Esta tabla ES ese snapshot: una fila por
-- (identidad de flujo + params_version + barra).
--
-- QUE SE GUARDA. state: serializacion canonica determinista de PivotState (Decimals
-- como string, campos ordenados; ADR-007) -- ancla el replay de la FSM. confidence: la
-- confianza 0-100 de la barra ancla (numeric, NULL cuando el modelo la marca
-- NOT_EVALUABLE); el materializador mapea NULL/None -> 0 al proyectar la serie (Q2
-- opcion d, dictamen P08c-PIVOT-03). Columnas explicitas por campo de PivotState se
-- rechazaron (Q1): acoplarian el esquema a la estructura interna del estado.
--
-- PARAMS_VERSION EN LA IDENTIDAD (PK). Un cambio de los parametros de la FSM o de la
-- formula de confianza produce un replay DISTINTO; su snapshot no puede colisionar con
-- el de otra version (mismo concepto que reset_policy en cvd_snapshot, OA-1).
-- params_version es la huella de (PivotParams + formula_version) y viaja en la
-- cache_key de pivotphase.phase/confidence.
--
-- POR QUE EL ROL DE REGLAS ESCRIBE AQUI (5.20). No es dato de mercado: es el ESTADO DE
-- TRABAJO del motor (como cvd_snapshot, 0022). Su escritura se acota a INSERT.
--
-- APPEND-ONLY REAL. Reevaluar la misma barra reproduce el MISMO estado (fold
-- determinista sobre Decimal, ADR-007): el INSERT repetido es un duplicado exacto que
-- ON CONFLICT DO NOTHING absorbe. Una CORRECCION no muta la fila: es un snapshot NUEVO
-- en su barra. Por eso UPDATE/DELETE/TRUNCATE quedan NEGADOS de forma explicita.

CREATE TABLE pivotphase_snapshot (
    exchange       text NOT NULL,
    market_type    text NOT NULL,
    symbol         text NOT NULL,
    timeframe      text NOT NULL,
    params_version text NOT NULL,
    open_time      bigint NOT NULL,
    state          text NOT NULL,
    confidence     numeric,
    PRIMARY KEY (exchange, market_type, symbol, timeframe, params_version, open_time),
    CONSTRAINT pivotphase_snapshot_confidence_rango
        CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 100))
);

COMMENT ON TABLE pivotphase_snapshot IS
    'isolation_scope=system. Estado de replay de pivotphase (RECURSIVE) del motor de '
    'reglas. SIN tenant_id A PROPOSITO: pivotphase.phase/confidence son '
    'shared_evaluation/public_cross_tenant, su snapshot es artefacto de evaluacion '
    'compartido (ADR-014). Escritura del motor acotada a INSERT (append-only).';

-- LA RENDIJA: el motor LEE su snapshot para anclar el replay y lo ESCRIBE al avanzar.
GRANT SELECT, INSERT ON pivotphase_snapshot TO ce_v5_rules;
-- APPEND-ONLY, NEGADO EXPLICITO para que check_rules_access.py (5.20) muerda si se
-- reintroduce por descuido.
REVOKE UPDATE, DELETE, TRUNCATE ON pivotphase_snapshot FROM ce_v5_rules;
