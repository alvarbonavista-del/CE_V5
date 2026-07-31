-- Migracion 0023: estado de replay del motor de reglas -- snapshot de VALOR de
-- ema.value por barra vigente (P08b LOTE 2, RECURSIVE). Sucesora de 0022.
-- Append-only: ninguna migracion aplicada se edita (regla 5.14).
--
-- POR QUE EXISTE. ema.value es RECURSIVE: el valor de la barra T depende de su propio
-- valor en T-1 (EMA[T] = alpha*close[T] + (1-alpha)*EMA[T-1], alpha = 2/(period+1)).
-- Reproducirlo desde el origen en cada tick seria O(historia); el materializador hace
-- REPLAY DETERMINISTA desde un SNAPSHOT de valor (materialize_recursive, T3). Esta
-- tabla ES ese snapshot: una fila por (flujo, period, barra).
--
-- POR QUE EL ROL DE REGLAS SI ESCRIBE AQUI (y en las tablas de mercado NO, regla 5.20).
-- Esto NO es dato de mercado: es el ESTADO DE TRABAJO del motor. Igual que cvd_snapshot
-- (0022), es el estado propio de replay del motor, no un hecho publico. La escritura se
-- acota a INSERT.
--
-- APPEND-ONLY REAL. reevaluar la misma barra reproduce el MISMO valor (el fold es
-- determinista sobre Decimal, ADR-007), asi que el INSERT repetido es un duplicado
-- exacto que el ON CONFLICT DO NOTHING absorbe. Una CORRECCION no muta la fila: se
-- resuelve con un snapshot NUEVO en su barra. Por eso UPDATE/DELETE/TRUNCATE quedan
-- NEGADOS de forma explicita.
--
-- period ENTRA EN LA IDENTIDAD (PK), no es un detalle: ema(9) y ema(21) son SERIES
-- DISTINTAS (viaja en la cache_key de ema.value). Dos snapshots de la misma barra con
-- distinto period no pueden colisionar.
--
-- market_type ENTRA EXPLICITO, fijado a spot en v5.0 (como cvd_snapshot y los lectores
-- de mercado): un mismo exchange y symbol pueden existir a la vez en spot y derivados.

CREATE TABLE ema_snapshot (
    exchange    text NOT NULL,
    market_type text NOT NULL,
    symbol      text NOT NULL,
    timeframe   text NOT NULL,
    period      integer NOT NULL,
    open_time   bigint NOT NULL,
    value       numeric NOT NULL,
    PRIMARY KEY (exchange, market_type, symbol, timeframe, period, open_time),
    -- period define la SERIE EMA; un period < 1 seria una identidad fantasma que nadie
    -- volveria a encontrar. El motor lo rechaza, no un if de la capa de arriba.
    CONSTRAINT ema_snapshot_period_positivo CHECK (period >= 1)
);
COMMENT ON TABLE ema_snapshot IS
    'isolation_scope=system. Snapshot de VALOR de ema.value por barra vigente (P08b LOTE 2, RECURSIVE). ESTADO DE TRABAJO del motor de reglas, NO dato de mercado: por eso ce_v5_rules SI escribe aqui (SELECT+INSERT), a diferencia de market_candle que solo lee (regla 5.20). SIN tenant_id A PROPOSITO: ema.value se declara shared_evaluation con sharing_scope=public_cross_tenant, asi que el snapshot es un artefacto de evaluacion COMPARTIDO; darle tenant_id duplicaria la MISMA serie del MISMO flujo por cada tenant, la explosion N x M que ADR-014 evita. Append-only real: UPDATE/DELETE/TRUNCATE revocados: una correccion es un snapshot NUEVO, no una mutacion. period entra en la PK porque ema(9) y ema(21) son series distintas.';

-- a) LA RENDIJA: el motor LEE su snapshot para anclar el replay y lo ESCRIBE al avanzar.
GRANT SELECT, INSERT ON ema_snapshot TO ce_v5_rules;

-- b) APPEND-ONLY, NEGADO DE FORMA EXPLICITA para que el check 5.20
--    (tools/check_rules_access.py) muerda si alguien lo reintroduce por descuido.
REVOKE UPDATE, DELETE, TRUNCATE ON ema_snapshot FROM ce_v5_rules;
