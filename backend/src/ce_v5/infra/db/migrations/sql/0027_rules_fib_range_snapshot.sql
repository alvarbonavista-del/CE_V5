-- Migracion 0027: estado de replay del motor de reglas -- snapshot del RANGO VIGENTE
-- del grid Fibonacci con histeresis (P08b LOTE 4, RECURSIVE). Sucesora de 0026.
-- Append-only: ninguna migracion aplicada se edita (regla 5.14).
--
-- POR QUE EXISTE. fib.nearest_level y fib.level_pct se calculan sobre un RANGO
-- [range_low, range_high] que NO es el swing de la barra: es un rango PEGAJOSO que solo
-- se mueve cuando el swing lo supera por al menos 0.414 veces su tamano (histeresis L2,
-- paridad v4 _maybe_retrace). Esa pegajosidad es MEMORIA: el rango de la barra T depende
-- del de T-1, y por tanto de toda la historia anterior. Reproducirlo desde el origen en
-- cada tick seria O(historia); el materializador hace REPLAY DETERMINISTA desde un
-- SNAPSHOT del rango. Esta tabla ES ese snapshot: una fila por (flujo, strength, barra).
--
-- QUE SE GUARDA: EL RANGO, NO LOS NIVELES. Los 17 niveles del grid, el nivel mas cercano
-- y el porcentaje son DERIVADOS de (range_high, range_low) mas el cierre de la barra:
-- persistirlos dejaria que la fila se contradijera a si misma y no anadiria nada que el
-- replay necesite. Dos columnas bastan y son exactamente el estado.
--
-- strength EN LA IDENTIDAD (PK). El rango se alimenta de swing.high/swing.low, que son
-- parametricas en strength (la fuerza simetrica del pivote): con strength distinto los
-- pivotes son otros y el rango que decantan es OTRO. rango(7) != rango(2), asi que sus
-- snapshots no pueden colisionar. strength viaja en la cache_key de fib.*.
--
-- LOS RATIOS FIBONACCI NO SON PARAMETROS. El 0.414 de la histeresis y los 17 ratios del
-- grid son constantes DEFINITORIAS del indicador (dictamen P08b-13 D6): viven en la
-- funcion pura, no en esta tabla ni en la cache_key. Si alguna vez dejaran de serlo,
-- seria un indicador distinto, no una parametrizacion del mismo.
--
-- POR QUE EL ROL DE REGLAS SI ESCRIBE AQUI (y en las tablas de mercado NO, regla 5.20).
-- Esto NO es dato de mercado: es el ESTADO DE TRABAJO del motor, como cvd_snapshot
-- (0022), ema_snapshot (0023), pivotphase_snapshot (0024), rsi_snapshot (0025) y
-- macd_snapshot (0026). La escritura se acota a INSERT.
--
-- APPEND-ONLY REAL. Reevaluar la misma barra reproduce el MISMO rango (el fold es
-- determinista sobre Decimal con el contexto pinneado de indicators/fib.py, ADR-007),
-- asi que el INSERT repetido es un duplicado exacto que el ON CONFLICT DO NOTHING
-- absorbe -- y eso es lo que hace inofensivo que las DOS fuentes (nearest_level y
-- level_pct) materialicen la misma barra por separado. Una CORRECCION no muta la fila:
-- es un snapshot NUEVO en su barra. Por eso UPDATE/DELETE/TRUNCATE quedan NEGADOS de
-- forma explicita.
--
-- market_type ENTRA EXPLICITO, fijado a spot en v5.0 (como las cinco tablas de snapshot
-- anteriores y los lectores de mercado).

CREATE TABLE fib_range_snapshot (
    exchange    text NOT NULL,
    market_type text NOT NULL,
    symbol      text NOT NULL,
    timeframe   text NOT NULL,
    strength    integer NOT NULL,
    open_time   bigint NOT NULL,
    range_high  numeric NOT NULL,
    range_low   numeric NOT NULL,
    PRIMARY KEY (exchange, market_type, symbol, timeframe, strength, open_time),
    -- Mismo dominio que swing (fuerza simetrica >= 1), de quien fib hereda el param. Un
    -- strength < 1 seria una identidad fantasma que nadie volveria a encontrar.
    CONSTRAINT fib_range_snapshot_strength_positivo CHECK (strength >= 1)
);
COMMENT ON TABLE fib_range_snapshot IS
    'isolation_scope=system. Snapshot del RANGO VIGENTE del grid Fibonacci con histeresis por barra (P08b LOTE 4, RECURSIVE). ESTADO DE TRABAJO del motor de reglas, NO dato de mercado: por eso ce_v5_rules SI escribe aqui (SELECT+INSERT), a diferencia de market_candle que solo lee (regla 5.20). Guarda el RANGO (range_high, range_low), no los niveles: los 17 niveles, el mas cercano y el porcentaje son DERIVADOS del rango mas el cierre. El rango es PEGAJOSO (solo se mueve si el swing lo supera por >= 0.414 del tamano del rango, paridad v4 _maybe_retrace), y esa pegajosidad es memoria ilimitada: de ahi que sea RECURSIVE y necesite snapshot. SIN tenant_id A PROPOSITO: fib.* se declara shared_evaluation con sharing_scope=public_cross_tenant, asi que el snapshot es un artefacto de evaluacion COMPARTIDO; darle tenant_id duplicaria la MISMA serie del MISMO flujo por cada tenant, la explosion N x M que ADR-014 evita. Append-only real: UPDATE/DELETE/TRUNCATE revocados; una correccion es un snapshot NUEVO. strength entra en la PK porque el rango se decanta de swing.high/swing.low y rango(7) != rango(2); los ratios Fibonacci (0.414 incluido) son DEFINITORIOS, no parametros.';

-- a) LA RENDIJA: el motor LEE su snapshot para anclar el replay y lo ESCRIBE al avanzar.
GRANT SELECT, INSERT ON fib_range_snapshot TO ce_v5_rules;

-- b) APPEND-ONLY, NEGADO DE FORMA EXPLICITA para que el check 5.20
--    (tools/check_rules_access.py) muerda si alguien lo reintroduce por descuido.
REVOKE UPDATE, DELETE, TRUNCATE ON fib_range_snapshot FROM ce_v5_rules;
