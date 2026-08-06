-- Migracion 0028: estado de replay del motor de reglas -- snapshot del ULTIMO PIVOTE
-- CONFIRMADO DE CADA LADO para divergence.* (P08b LOTE 5, RECURSIVE). Sucesora de 0027.
-- Append-only: ninguna migracion aplicada se edita (regla 5.14).
--
-- POR QUE EXISTE. divergence.* empareja cada pivote de precio con el ANTERIOR DEL MISMO
-- LADO (maximos con maximos, minimos con minimos) y compara precio contra RSI. Ese
-- "anterior" no tiene cota: entre dos maximos confirmados pueden pasar cinco barras o
-- quinientas, segun lo que haga el mercado. Materializar por VENTANA ACOTADA perderia
-- -- o peor, emparejaria mal -- todo evento cuyo pivote previo cayera antes del inicio
-- de la ventana, y el fallo seria MUDO: una divergencia que no salta, o una que salta
-- contra el pivote equivocado. De ahi RECURSIVE y de ahi esta tabla (dictamen
-- P08b-D1-05, OPCION A).
--
-- QUE SE GUARDA: EL ULTIMO PIVOTE DE CADA LADO, NO LOS EVENTOS. Un evento es la
-- COMPARACION de dos pivotes consecutivos; con el ultimo de cada lado en la mano, el
-- replay solo necesita los pivotes NUEVOS que aparezcan despues para reanudar la cadena
-- exactamente donde la dejo. Persistir los eventos seria persistir un derivado que la
-- fila podria acabar contradiciendo. Tres columnas por lado -- open_time, precio y RSI
-- del pivote -- porque el emparejamiento compara PRECIO Y RSI, y el open_time es lo que
-- identifica la barra ancla frente a los pivotes que vengan luego.
--
-- POR QUE UN SOLO PIVOTE POR LADO BASTA. Los pivotes de un lado no se solapan y su orden
-- de confirmacion sigue al de su barra ancla, asi que "pivote con ancla posterior a la
-- guardada" es EXACTAMENTE el conjunto de los que aun no se han contabilizado. Guardar
-- dos, o los N ultimos, no anadiria ni un evento: la formula (detect_divergences) solo
-- mira el par consecutivo.
--
-- NULLABLE HASTA EL PRIMER PIVOTE DE CADA LADO. Al principio de un historico todavia no
-- hay maximo (o minimo) confirmado, y esa ausencia es un HECHO del estado, no un hueco
-- que rellenar: se guarda NULL. El RSI va aparte de su pivote porque puede faltar SIN
-- que falte el pivote -- durante el warm-up de Wilder el pivote existe y su RSI no --, y
-- la formula ya sabe que hacer con eso (ese par no produce evento; el siguiente si). Los
-- CHECK atan open_time y precio: media identidad de pivote no es un pivote.
--
-- strength Y rsi_period EN LA IDENTIDAD (PK). strength es la fuerza simetrica del
-- pivote: con otra fuerza los pivotes son OTROS y la cadena que decantan es otra.
-- rsi_period cambia el RSI que se lee EN esos pivotes, y por tanto que pares cruzan el
-- umbral de divergencia. Los dos viajan en la cache_key de divergence.*, asi que sus
-- snapshots no pueden colisionar: cadena(2,14) != cadena(7,21).
--
-- POR QUE EL ROL DE REGLAS SI ESCRIBE AQUI (y en las tablas de mercado NO, regla 5.20).
-- Esto NO es dato de mercado: es el ESTADO DE TRABAJO del motor, como cvd_snapshot
-- (0022), ema_snapshot (0023), pivotphase_snapshot (0024), rsi_snapshot (0025),
-- macd_snapshot (0026) y fib_range_snapshot (0027). La escritura se acota a INSERT.
--
-- APPEND-ONLY REAL. Reevaluar la misma barra reproduce el MISMO estado (la cadena de
-- pivotes es determinista: symmetric_pivots es geometrico y el RSI corre bajo el
-- contexto Decimal pinneado de indicators/rsi.py, ADR-007), asi que el INSERT repetido
-- es un duplicado exacto que el ON CONFLICT DO NOTHING absorbe -- y eso es lo que hace
-- inofensivo que las CINCO fuentes (kind y los cuatro flags) materialicen la misma barra
-- por separado. Una CORRECCION no muta la fila: es un snapshot NUEVO en su barra. Por
-- eso UPDATE/DELETE/TRUNCATE quedan NEGADOS de forma explicita.
--
-- market_type ENTRA EXPLICITO, fijado a spot en v5.0 (como las seis tablas de snapshot
-- anteriores y los lectores de mercado).

CREATE TABLE divergence_snapshot (
    exchange            text NOT NULL,
    market_type         text NOT NULL,
    symbol              text NOT NULL,
    timeframe           text NOT NULL,
    strength            integer NOT NULL,
    rsi_period          integer NOT NULL,
    open_time           bigint NOT NULL,
    last_high_open_time bigint,
    last_high_price     numeric,
    last_high_rsi       numeric,
    last_low_open_time  bigint,
    last_low_price      numeric,
    last_low_rsi        numeric,
    PRIMARY KEY (
        exchange, market_type, symbol, timeframe, strength, rsi_period, open_time
    ),
    -- Mismo dominio que swing (fuerza simetrica >= 1), de quien divergence hereda el
    -- param. Un strength < 1 seria una identidad fantasma que nadie volveria a
    -- encontrar.
    CONSTRAINT divergence_snapshot_strength_positivo CHECK (strength >= 1),
    -- Mismo dominio que el CHECK de rsi_snapshot (0025): el RSI de Wilder exige
    -- period >= 1.
    CONSTRAINT divergence_snapshot_rsi_period_positivo CHECK (rsi_period >= 1),
    -- Un pivote es su barra Y su precio: con uno solo de los dos el replay no podria ni
    -- reanudar la cadena ni saber desde donde. El RSI queda FUERA del par a proposito:
    -- un pivote sin RSI (warm-up de Wilder) es un estado legitimo.
    CONSTRAINT divergence_snapshot_high_completo CHECK (
        (last_high_open_time IS NULL) = (last_high_price IS NULL)
    ),
    CONSTRAINT divergence_snapshot_low_completo CHECK (
        (last_low_open_time IS NULL) = (last_low_price IS NULL)
    ),
    -- Un RSI sin su pivote no es nada: no hay barra a la que pertenezca.
    CONSTRAINT divergence_snapshot_high_rsi_con_pivote CHECK (
        last_high_rsi IS NULL OR last_high_open_time IS NOT NULL
    ),
    CONSTRAINT divergence_snapshot_low_rsi_con_pivote CHECK (
        last_low_rsi IS NULL OR last_low_open_time IS NOT NULL
    )
);
COMMENT ON TABLE divergence_snapshot IS
    'isolation_scope=system. Snapshot del ULTIMO PIVOTE CONFIRMADO DE CADA LADO (maximo y minimo) por barra, estado de replay del RECURSIVE divergence.kind/regular_bull/regular_bear/hidden_bull/hidden_bear (P08b LOTE 5, dictamen P08b-D1-05 OPCION A). ESTADO DE TRABAJO del motor de reglas, NO dato de mercado: por eso ce_v5_rules SI escribe aqui (SELECT+INSERT), a diferencia de market_candle que solo lee (regla 5.20). Guarda el PIVOTE (open_time, precio, RSI) de cada lado, no los eventos: un evento es la comparacion de dos pivotes consecutivos y se deriva de la cadena. Un solo pivote por lado BASTA porque la formula solo empareja consecutivos y los pivotes de un lado no se solapan. La distancia entre dos pivotes consecutivos no tiene cota -- pueden mediar cinco barras o quinientas --, y esa memoria sin cota es lo que hace RECURSIVE a la fuente y necesario a este snapshot: una ventana acotada perderia o emparejaria mal todo evento cuyo pivote previo cayera antes de su inicio. Columnas NULLABLE hasta el primer pivote de cada lado; el RSI va aparte del pivote porque durante el warm-up de Wilder el pivote existe y su RSI no. SIN tenant_id A PROPOSITO: divergence.* se declara shared_evaluation con sharing_scope=public_cross_tenant, asi que el snapshot es un artefacto de evaluacion COMPARTIDO; darle tenant_id duplicaria la MISMA serie del MISMO flujo por cada tenant, la explosion N x M que ADR-014 evita. Append-only real: UPDATE/DELETE/TRUNCATE revocados; una correccion es un snapshot NUEVO. strength y rsi_period entran en la PK porque cadena(2,14) != cadena(7,21).';

-- a) LA RENDIJA: el motor LEE su snapshot para anclar el replay y lo ESCRIBE al avanzar.
GRANT SELECT, INSERT ON divergence_snapshot TO ce_v5_rules;

-- b) APPEND-ONLY, NEGADO DE FORMA EXPLICITA para que el check 5.20
--    (tools/check_rules_access.py) muerda si alguien lo reintroduce por descuido.
REVOKE UPDATE, DELETE, TRUNCATE ON divergence_snapshot FROM ce_v5_rules;
