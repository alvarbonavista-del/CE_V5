-- Migracion 0022: estado de replay del motor de reglas -- snapshot de VALOR de
-- cvd.value por barra vigente (P08c CE-14; MAT-04 opcion A, MAT-07 decisiones 2/3).
-- Sucesora de 0021. Append-only: ninguna migracion aplicada se edita (regla 5.14).
--
-- POR QUE EXISTE. cvd.value es INTEGRATOR: el valor de la barra T es el de T-1 mas el
-- delta de T. Reproducirlo desde el origen en cada tick seria O(historia); el materia-
-- lizador hace REPLAY DETERMINISTA desde un SNAPSHOT de valor (materialize_recursive,
-- T3). Esta tabla ES ese snapshot: una fila por (flujo, reset_policy, barra).
--
-- POR QUE EL ROL DE REGLAS SI ESCRIBE AQUI (y en las tablas de mercado NO, regla 5.20).
-- Esto NO es dato de mercado: es el ESTADO DE TRABAJO del motor. El motor no fabrica
-- hechos publicos -- un footprint o una vela escritos por el motor alimentarian sus
-- propias reglas, y eso es lo que 0016/0021 prohiben --, pero su propio estado de replay
-- si es suyo. El precedente en la misma direccion ya existe: el motor INSERTA en la
-- outbox (0013) lo que el mismo produce. La escritura se acota a INSERT.
--
-- APPEND-ONLY REAL. reevaluar la misma barra reproduce el MISMO valor (el fold es
-- determinista sobre Decimal, ADR-007), asi que el INSERT repetido es un duplicado
-- exacto que el ON CONFLICT DO NOTHING absorbe (T5b-2b). Una CORRECCION no muta la fila:
-- se resuelve con un snapshot NUEVO en su barra. Por eso UPDATE/DELETE/TRUNCATE quedan
-- NEGADOS de forma explicita: nadie reescribe un snapshot ya tomado, tampoco el motor.
--
-- reset_policy ENTRA EN LA IDENTIDAD (PK), no es un detalle: rolling-CVD y session-CVD
-- son HECHOS DISTINTOS (OA-1, y por eso viaja en la cache_key de cvd.value). Dos
-- snapshots de la misma barra con distinta politica no pueden colisionar.
--
-- market_type ENTRA EXPLICITO, como en market_footprint (0017): un mismo exchange y
-- symbol existen a la vez en spot y en derivados, y su CVD no es el mismo.

CREATE TABLE cvd_snapshot (
    exchange     text NOT NULL,
    market_type  text NOT NULL,
    symbol       text NOT NULL,
    timeframe    text NOT NULL,
    reset_policy text NOT NULL,
    open_time    bigint NOT NULL,
    value        numeric NOT NULL,
    PRIMARY KEY (exchange, market_type, symbol, timeframe, reset_policy, open_time),
    -- Las DOS politicas del enum ResetPolicy (OA-1). Una politica mal escrita crearia
    -- una identidad fantasma de snapshot que nadie volveria a encontrar; el motor la
    -- rechaza, no un if de la capa de arriba. Ampliarla seria una migracion sucesora.
    CONSTRAINT cvd_snapshot_politica_valida
        CHECK (reset_policy IN ('rolling', 'session_utc'))
);
COMMENT ON TABLE cvd_snapshot IS
    'isolation_scope=system. Snapshot de VALOR de cvd.value por barra vigente (P08c CE-14, MAT-07). ESTADO DE TRABAJO del motor de reglas, NO dato de mercado: por eso ce_v5_rules SI escribe aqui (SELECT+INSERT), a diferencia de market_candle/market_footprint que solo lee (regla 5.20). SIN tenant_id A PROPOSITO: cvd.value se declara shared_evaluation con sharing_scope=public_cross_tenant, asi que el snapshot es un artefacto de evaluacion COMPARTIDO; darle tenant_id duplicaria el MISMO acumulado del MISMO flujo por cada tenant, la explosion N x M que ADR-014 evita. Append-only real: UPDATE/DELETE/TRUNCATE revocados: una correccion es un snapshot NUEVO, no una mutacion. reset_policy entra en la PK porque rolling-CVD y session-CVD son hechos distintos (OA-1).';

-- a) LA RENDIJA: el motor LEE su snapshot para anclar el replay y lo ESCRIBE al avanzar.
GRANT SELECT, INSERT ON cvd_snapshot TO ce_v5_rules;

-- b) APPEND-ONLY, NEGADO DE FORMA EXPLICITA para que el check 5.20
--    (tools/check_rules_access.py) muerda si alguien lo reintroduce por descuido.
REVOKE UPDATE, DELETE, TRUNCATE ON cvd_snapshot FROM ce_v5_rules;
