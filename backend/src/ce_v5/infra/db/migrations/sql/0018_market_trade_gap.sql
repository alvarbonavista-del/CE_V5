-- Migracion 0018: huecos de trades NO cubiertos (P07b; ADR-014, ADR-006, regla 5.20).
-- Sucesora de 0017. Append-only: ninguna migracion aplicada se edita (regla 5.14).
-- Regla 5.20 calcada de 0017: ce_v5_ingestion es el UNICO que escribe; ce_v5_app SOLO
-- LEE; el operador no la toca. Lo verifica tools/check_market_access.py.
-- CE-14: NO toca el nucleo de ingesta; solo anade una tabla y sus grants.

-- ESTA TABLA REGISTRA LA AUSENCIA DE DATOS, y por eso existe. En una reconexion el
-- conector rellena por REST publico hasta el techo de su endpoint; si el corte duro mas
-- que eso, los trades mas antiguos del hueco NO se recuperan JAMAS. Sin esta fila, esa
-- perdida seria invisible: las barras de footprint afectadas se agregarian con los
-- trades que SI llegaron y se publicarian como completas, mintiendo sobre el mercado.
--
-- 3b LA LEE para marcar is_complete=False en toda barra cuyo [open_time, close_time) se
-- solape con algun hueco de su flujo. Una barra incompleta se persiste y se ve, pero
-- NUNCA se emite como completa.
CREATE TABLE market_trade_gap (
    exchange               text NOT NULL,
    market_type            text NOT NULL,
    symbol                 text NOT NULL,
    -- Limites del hueco en el event_time del EXCHANGE (ADR-007), no en nuestro reloj.
    -- gap_from: el ultimo trade que SI teniamos. gap_to: el mas antiguo que el relleno
    -- alcanzo. NULL en gap_to = extremo DESCONOCIDO (el REST no devolvio nada con lo que
    -- acotarlo): el hueco es abierto por ese lado y 3b debe tratarlo como tal, que es la
    -- lectura fail-safe. Un limite inventado seria peor que un limite ausente.
    gap_from_event_time_ms bigint,
    gap_to_event_time_ms   bigint,
    recorded_at            timestamptz NOT NULL DEFAULT now(),
    -- IDEMPOTENCIA DEL REGISTRO: el mismo hueco detectado dos veces (dos reconexiones
    -- antes de que nadie lo consuma) es UN hueco, no dos. Lo decide este UNIQUE con el
    -- ON CONFLICT DO NOTHING de record_gap, no un SELECT previo que otra replica podria
    -- invalidar entre la consulta y el INSERT.
    -- NULLS NOT DISTINCT (PG15+) es imprescindible aqui: con la regla por defecto, dos
    -- huecos identicos con gap_to NULL contarian como distintos y la tabla se llenaria
    -- de duplicados del MISMO hueco en cada reconexion.
    CONSTRAINT market_trade_gap_identidad
        UNIQUE NULLS NOT DISTINCT (exchange, market_type, symbol,
                                   gap_from_event_time_ms, gap_to_event_time_ms),
    CONSTRAINT market_trade_gap_ventana_coherente CHECK (
        gap_from_event_time_ms IS NULL
        OR gap_to_event_time_ms IS NULL
        OR gap_to_event_time_ms >= gap_from_event_time_ms
    )
);
-- El acceso de 3b es "que huecos hay de ESTE flujo, ordenados por tiempo": ese es el
-- indice.
CREATE INDEX market_trade_gap_stream_tiempo_idx
    ON market_trade_gap (exchange, market_type, symbol, gap_from_event_time_ms);
COMMENT ON TABLE market_trade_gap IS
    'Huecos de trades NO cubiertos por el backfill REST tras una reconexion (P07b, ADR-014). isolation_scope=public_market, sin tenant_id y sin RLS: dato publico, igual que market_trade. Registra la AUSENCIA de datos, que es lo que permite que 3b marque is_complete=False en las barras de footprint solapadas en vez de publicarlas como completas. La API NO puede escribirla (regla 5.20). Append-only: UPDATE/DELETE/TRUNCATE revocados a todos los roles de runtime.';

-- PRIVILEGIOS ESTRECHOS (regla 5.20). La API LEE (necesitara decir que una barra esta
-- incompleta); el ingestor escribe.
GRANT SELECT ON market_trade_gap TO ce_v5_app;
GRANT SELECT, INSERT ON market_trade_gap TO ce_v5_ingestion;

-- APPEND-ONLY REAL: un hueco no se "arregla" borrandolo. El dato perdido no vuelve, y
-- borrar su registro solo borraria la prueba de que falta.
REVOKE UPDATE, DELETE, TRUNCATE ON market_trade_gap
    FROM ce_v5_app, ce_v5_ingestion, ce_v5_operator;
REVOKE ALL ON market_trade_gap FROM ce_v5_operator;
