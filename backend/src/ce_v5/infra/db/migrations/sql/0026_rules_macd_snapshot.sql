-- Migracion 0026: estado de replay del motor de reglas -- snapshot de ESTADO de
-- macd.line/macd.signal/macd.histogram por barra vigente (P08b LOTE 3, RECURSIVE).
-- Sucesora de 0025. Append-only: ninguna migracion aplicada se edita (regla 5.14).
--
-- POR QUE EXISTE. El MACD es RECURSIVE por dentro: son TRES EMAs encadenadas (EMA(fast)
-- y EMA(slow) sobre el cierre, y EMA(signal) sobre la diferencia de las dos), y cada una
-- depende de su propio valor en T-1. Reproducirlo desde el origen en cada tick seria
-- O(historia); el materializador hace REPLAY DETERMINISTA desde un SNAPSHOT de estado
-- (macd_seed + macd_step, indicators/macd.py). Esta tabla ES ese snapshot: una fila por
-- (flujo, fast, slow, signal, barra).
--
-- QUE SE GUARDA: EL ESTADO, NO LAS SALIDAS. Las tres columnas son las EMAs INTERNAS
-- (ema_fast, ema_slow, ema_signal). Las tres salidas publicas -- line = ema_fast -
-- ema_slow, signal = ema_signal, histogram = line - signal -- son DERIVADAS de ellas:
-- persistirlas dejaria que la fila se contradijera a si misma, y ademas no bastarian
-- para continuar el replay (de line y signal no se recuperan ema_fast y ema_slow por
-- separado). Se guardan POR COLUMNAS y no serializadas (a diferencia de
-- pivotphase_snapshot.state, 0024): son tres numeros de tipo fijo, autocontenidos.
--
-- NO HAY last_close, a diferencia de rsi_snapshot (0025): las EMAs del MACD se alimentan
-- del cierre DIRECTO, no de un diferencial entre cierres consecutivos, asi que el estado
-- no necesita recordar la barra anterior. El estado es exactamente lo que hace falta y
-- nada mas.
--
-- LOS TRES PARAMS ENTRAN EN LA IDENTIDAD (PK). macd(12,26,9) y macd(5,35,5) son SERIES
-- DISTINTAS (los tres viajan en la cache_key de macd.*). Dos snapshots de la misma barra
-- con distinta parametrizacion no pueden colisionar. Los CHECK exigen >= 1 en cada uno,
-- el mismo dominio que valida la funcion pura; NO se exige fast < slow porque macd() no
-- lo exige: el esquema no inventa reglas que el nucleo no tiene.
--
-- POR QUE EL ROL DE REGLAS SI ESCRIBE AQUI (y en las tablas de mercado NO, regla 5.20).
-- Esto NO es dato de mercado: es el ESTADO DE TRABAJO del motor, como cvd_snapshot
-- (0022), ema_snapshot (0023), pivotphase_snapshot (0024) y rsi_snapshot (0025). La
-- escritura se acota a INSERT.
--
-- APPEND-ONLY REAL. Reevaluar la misma barra reproduce el MISMO estado (fold
-- determinista sobre Decimal con el contexto pinneado de indicators/macd.py, ADR-007),
-- asi que el INSERT repetido es un duplicado exacto que el ON CONFLICT DO NOTHING
-- absorbe -- y eso es lo que hace inofensivo que las TRES fuentes materialicen la misma
-- barra por separado. Una CORRECCION no muta la fila: es un snapshot NUEVO en su barra.
-- Por eso UPDATE/DELETE/TRUNCATE quedan NEGADOS de forma explicita.
--
-- market_type ENTRA EXPLICITO, fijado a spot en v5.0 (como las cuatro tablas de snapshot
-- anteriores y los lectores de mercado).

CREATE TABLE macd_snapshot (
    exchange    text NOT NULL,
    market_type text NOT NULL,
    symbol      text NOT NULL,
    timeframe   text NOT NULL,
    fast        integer NOT NULL,
    slow        integer NOT NULL,
    signal      integer NOT NULL,
    open_time   bigint NOT NULL,
    ema_fast    numeric NOT NULL,
    ema_slow    numeric NOT NULL,
    ema_signal  numeric NOT NULL,
    PRIMARY KEY (exchange, market_type, symbol, timeframe, fast, slow, signal, open_time),
    -- Mismo dominio que la funcion pura (macd exige cada periodo >= 1). Un periodo < 1
    -- seria una identidad fantasma que nadie volveria a encontrar.
    CONSTRAINT macd_snapshot_fast_positivo CHECK (fast >= 1),
    CONSTRAINT macd_snapshot_slow_positivo CHECK (slow >= 1),
    CONSTRAINT macd_snapshot_signal_positivo CHECK (signal >= 1)
);
COMMENT ON TABLE macd_snapshot IS
    'isolation_scope=system. Snapshot de ESTADO de macd.line/macd.signal/macd.histogram por barra vigente (P08b LOTE 3, RECURSIVE). ESTADO DE TRABAJO del motor de reglas, NO dato de mercado: por eso ce_v5_rules SI escribe aqui (SELECT+INSERT), a diferencia de market_candle que solo lee (regla 5.20). Guarda las TRES EMAs INTERNAS (ema_fast, ema_slow, ema_signal), no las salidas: line/signal/histogram son DERIVADAS de ellas y no bastarian para continuar el replay. SIN last_close (a diferencia de rsi_snapshot): las EMAs se alimentan del cierre directo, no de un diferencial. SIN tenant_id A PROPOSITO: macd.* se declara shared_evaluation con sharing_scope=public_cross_tenant, asi que el snapshot es un artefacto de evaluacion COMPARTIDO; darle tenant_id duplicaria la MISMA serie del MISMO flujo por cada tenant, la explosion N x M que ADR-014 evita. Append-only real: UPDATE/DELETE/TRUNCATE revocados; una correccion es un snapshot NUEVO. fast, slow y signal entran en la PK porque macd(12,26,9) y macd(5,35,5) son series distintas.';

-- a) LA RENDIJA: el motor LEE su snapshot para anclar el replay y lo ESCRIBE al avanzar.
GRANT SELECT, INSERT ON macd_snapshot TO ce_v5_rules;

-- b) APPEND-ONLY, NEGADO DE FORMA EXPLICITA para que el check 5.20
--    (tools/check_rules_access.py) muerda si alguien lo reintroduce por descuido.
REVOKE UPDATE, DELETE, TRUNCATE ON macd_snapshot FROM ce_v5_rules;
