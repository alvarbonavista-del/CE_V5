-- Migracion 0025: estado de replay del motor de reglas -- snapshot de ESTADO de
-- rsi.value por barra vigente (P08b LOTE 3, RECURSIVE). Sucesora de 0024.
-- Append-only: ninguna migracion aplicada se edita (regla 5.14).
--
-- POR QUE EXISTE. rsi.value es RECURSIVE (Wilder/RMA): el estado de la barra T depende
-- del de T-1 (avg = (avg_prev*(period-1) + nuevo)/period). Reproducirlo desde el origen
-- en cada tick seria O(historia); el materializador hace REPLAY DETERMINISTA desde un
-- SNAPSHOT de estado (rsi_seed + rsi_step, indicators/rsi.py). Esta tabla ES ese
-- snapshot: una fila por (flujo, period, barra).
--
-- QUE SE GUARDA, Y POR QUE TRES COLUMNAS. El estado de Wilder NO es un solo numero como
-- el de ema (0023): son las dos medias suavizadas (avg_gain, avg_loss) MAS el cierre de
-- la barra ancla (last_close). La tercera no es comodidad: gain[T] = close[T] -
-- close[T-1] es un DIFERENCIAL, asi que sin el cierre previo el primer paso del replay
-- no se puede calcular y el ancla no anclaria nada. Se guardan POR COLUMNAS y no
-- serializadas (a diferencia de pivotphase_snapshot.state, 0024): el estado del RSI son
-- tres numeros de tipo fijo, autocontenidos y legibles, no una estructura interna cuya
-- forma pudiera cambiar. El RSI de la barra NO se persiste porque es DERIVADO del par
-- (avg_gain, avg_loss): guardarlo permitiria que la fila se contradijera a si misma.
--
-- POR QUE EL ROL DE REGLAS SI ESCRIBE AQUI (y en las tablas de mercado NO, regla 5.20).
-- Esto NO es dato de mercado: es el ESTADO DE TRABAJO del motor. Igual que cvd_snapshot
-- (0022), ema_snapshot (0023) y pivotphase_snapshot (0024), es estado propio de replay
-- del motor, no un hecho publico. La escritura se acota a INSERT.
--
-- APPEND-ONLY REAL. Reevaluar la misma barra reproduce el MISMO estado (el fold es
-- determinista sobre Decimal con el contexto pinneado de indicators/rsi.py, ADR-007),
-- asi que el INSERT repetido es un duplicado exacto que el ON CONFLICT DO NOTHING
-- absorbe. Una CORRECCION no muta la fila: se resuelve con un snapshot NUEVO en su
-- barra. Por eso UPDATE/DELETE/TRUNCATE quedan NEGADOS de forma explicita.
--
-- period ENTRA EN LA IDENTIDAD (PK), no es un detalle: rsi(7) y rsi(14) son SERIES
-- DISTINTAS (viaja en la cache_key de rsi.value). Dos snapshots de la misma barra con
-- distinto period no pueden colisionar.
--
-- market_type ENTRA EXPLICITO, fijado a spot en v5.0 (como las tres tablas de snapshot
-- anteriores y los lectores de mercado): un mismo exchange y symbol pueden existir a la
-- vez en spot y derivados.

CREATE TABLE rsi_snapshot (
    exchange    text NOT NULL,
    market_type text NOT NULL,
    symbol      text NOT NULL,
    timeframe   text NOT NULL,
    period      integer NOT NULL,
    open_time   bigint NOT NULL,
    avg_gain    numeric NOT NULL,
    avg_loss    numeric NOT NULL,
    last_close  numeric NOT NULL,
    PRIMARY KEY (exchange, market_type, symbol, timeframe, period, open_time),
    -- period define la SERIE RSI; un period < 1 seria una identidad fantasma que nadie
    -- volveria a encontrar. El motor lo rechaza, no un if de la capa de arriba.
    CONSTRAINT rsi_snapshot_period_positivo CHECK (period >= 1)
);
COMMENT ON TABLE rsi_snapshot IS
    'isolation_scope=system. Snapshot de ESTADO de rsi.value por barra vigente (P08b LOTE 3, RECURSIVE Wilder). ESTADO DE TRABAJO del motor de reglas, NO dato de mercado: por eso ce_v5_rules SI escribe aqui (SELECT+INSERT), a diferencia de market_candle que solo lee (regla 5.20). El estado son TRES columnas -- avg_gain, avg_loss y last_close --: la tercera es obligatoria porque gain[T]=close[T]-close[T-1] es un diferencial y sin el cierre previo el replay no puede dar su primer paso. SIN tenant_id A PROPOSITO: rsi.value se declara shared_evaluation con sharing_scope=public_cross_tenant, asi que el snapshot es un artefacto de evaluacion COMPARTIDO; darle tenant_id duplicaria la MISMA serie del MISMO flujo por cada tenant, la explosion N x M que ADR-014 evita. Append-only real: UPDATE/DELETE/TRUNCATE revocados; una correccion es un snapshot NUEVO, no una mutacion. period entra en la PK porque rsi(7) y rsi(14) son series distintas.';

-- a) LA RENDIJA: el motor LEE su snapshot para anclar el replay y lo ESCRIBE al avanzar.
GRANT SELECT, INSERT ON rsi_snapshot TO ce_v5_rules;

-- b) APPEND-ONLY, NEGADO DE FORMA EXPLICITA para que el check 5.20
--    (tools/check_rules_access.py) muerda si alguien lo reintroduce por descuido.
REVOKE UPDATE, DELETE, TRUNCATE ON rsi_snapshot FROM ce_v5_rules;
