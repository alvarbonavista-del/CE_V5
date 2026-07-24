-- Migracion 0020: snapshot top-K del libro L2 + discontinuidades (P07c; ADR-014, ADR-013).
-- Sucesora de 0019. Append-only: ninguna migracion aplicada se edita (regla 5.14).
-- Regla 5.20 calcada de 0017/0018: ce_v5_ingestion es el UNICO que escribe estas tablas;
-- ce_v5_app SOLO LEE; el operador no las toca. Lo verifica tools/check_market_access.py.
-- CE-14: NO toca el nucleo de ingesta; solo anade tablas, grants y amplia la outbox.

-- a) SNAPSHOT TOP-K DEL LIBRO (data_family=orderbook, ADR-014). public_market, sin
--    tenant_id. UNA tabla, dos variantes: kind='frontier' (as-of close_time, uno por
--    barra, se PUBLICA por outbox) y kind='sample' (muestra intra-ventana a cadencia, se
--    PERSISTE sin publicar, como los trades). El libro COMPLETO vive en memoria; aqui solo
--    el top-K por lado. PK = idempotency_key (identidad logica del hecho, ADR-003) que ya
--    incluye K, cadencia, ventana y formula_version (reproducibilidad, cond.1). Los niveles
--    (precio x tamano) en JSONB con Decimal EN TEXTO, ya validados por el contrato (ADR-006).
CREATE TABLE market_orderbook_snapshot (
    idempotency_key  text PRIMARY KEY,
    stream_key       text NOT NULL,
    exchange         text NOT NULL,
    market_type      text NOT NULL,
    symbol           text NOT NULL,
    depth_k          integer NOT NULL,
    sequence         bigint NOT NULL,
    kind             text NOT NULL,
    timeframe        text NOT NULL,
    open_time        bigint NOT NULL,
    close_time       bigint NOT NULL,
    -- NULL en frontier (la foto de la barra no tiene instante); NOT NULL en sample (su
    -- instante dentro de la ventana). El CHECK de abajo lo ata al kind.
    sample_time      bigint,
    bids             jsonb NOT NULL,
    asks             jsonb NOT NULL,
    -- FAIL-SAFE UNIFORME (cond.3): un hueco/resync en la ventana marca is_complete=False
    -- en las muestras afectadas Y en el frontier. Una foto incompleta se persiste y SE VE,
    -- pero nunca se toma por completa.
    is_complete      boolean NOT NULL,
    cadence_ms       integer NOT NULL,
    formula_version  integer NOT NULL,
    -- event_time del ORIGEN (ADR-007): el instante del exchange/barra, no nuestro reloj.
    event_time       bigint NOT NULL,
    ingested_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT market_orderbook_snapshot_kind_valido
        CHECK (kind IN ('frontier', 'sample')),
    CONSTRAINT market_orderbook_snapshot_variante_coherente CHECK (
        (kind = 'frontier' AND sample_time IS NULL)
        OR
        (kind = 'sample' AND sample_time IS NOT NULL)
    ),
    CONSTRAINT market_orderbook_snapshot_ventana_coherente
        CHECK (close_time > open_time),
    CONSTRAINT market_orderbook_snapshot_config_positiva
        CHECK (depth_k >= 1 AND cadence_ms >= 1 AND formula_version >= 1)
);
CREATE INDEX market_orderbook_snapshot_stream_tiempo_idx
    ON market_orderbook_snapshot (stream_key, event_time);
COMMENT ON TABLE market_orderbook_snapshot IS
    'Snapshot top-K del libro L2 (P07c, data_family=orderbook, ADR-014). isolation_scope=public_market, sin tenant_id: dato publico. Dos variantes en una tabla: frontier (as-of close_time, uno por barra, publicado por outbox) y sample (muestra intra-ventana a cadencia, persistida sin publicar). Solo el top-K por lado; el libro completo vive en memoria (OrderbookBook). Niveles en JSONB con Decimal en texto. Append-only: UPDATE/DELETE/TRUNCATE revocados a TODOS los roles de runtime. La API NO puede escribirlo (regla 5.20). DIFERIDO A v5.1 (cond.4, no deuda silenciosa): el LIBRO PROFUNDO (mas alla del top-K) y el DELTA-LOG crudo NO se persisten hoy porque el market data aun no fluye a produccion; DISPARADOR DE REVISION: si el market data empezara a servirse antes de v5.1, se reabre esta decision y se evalua persistir profundidad y delta-log.';

-- b) DISCONTINUIDADES DEL LIBRO (P07c): el resync como HECHO. Registra que entre
--    from_sequence (lo ultimo bueno) y to_sequence (donde reanudo) hubo un hueco y el
--    libro se reinicio desde una foto nueva. to_sequence NULL = extremo DESCONOCIDO
--    (fail-safe, como gap_to en 0018). Es su propio hecho, no una correccion.
CREATE TABLE market_orderbook_discontinuity (
    exchange       text NOT NULL,
    market_type    text NOT NULL,
    symbol         text NOT NULL,
    from_sequence  bigint NOT NULL,
    to_sequence    bigint,
    event_time     bigint NOT NULL,
    reason         text NOT NULL,
    recorded_at    timestamptz NOT NULL DEFAULT now(),
    -- IDEMPOTENCIA: el mismo hueco detectado dos veces (dos reconexiones antes de
    -- consumirlo) es UN hueco. NULLS NOT DISTINCT (PG15+) para que dos huecos identicos
    -- con to_sequence NULL no cuenten como distintos, igual que market_trade_gap (0018).
    CONSTRAINT market_orderbook_discontinuity_identidad
        UNIQUE NULLS NOT DISTINCT (exchange, market_type, symbol,
                                   from_sequence, to_sequence),
    CONSTRAINT market_orderbook_discontinuity_rango_coherente CHECK (
        to_sequence IS NULL OR to_sequence >= from_sequence
    )
);
CREATE INDEX market_orderbook_discontinuity_stream_tiempo_idx
    ON market_orderbook_discontinuity (exchange, market_type, symbol, event_time);
COMMENT ON TABLE market_orderbook_discontinuity IS
    'Discontinuidades del libro L2: el resync como hecho (P07c, ADR-014). isolation_scope=public_market, sin tenant_id y sin RLS: dato publico, como market_trade_gap. Registra que el libro perdio continuidad entre from_sequence y to_sequence y se reinicio desde una foto nueva; se PUBLICA (market.orderbook_resynced). Append-only: UPDATE/DELETE/TRUNCATE revocados a todos los roles de runtime. La API NO puede escribirla (regla 5.20). DIFERIDO A v5.1 (cond.4): el delta-log crudo del libro no se persiste hoy (mismo disparador de revision que market_orderbook_snapshot).';

-- c) PRIVILEGIOS ESTRECHOS (regla 5.20). La API LEE; el ingestor escribe.
GRANT SELECT ON market_orderbook_snapshot TO ce_v5_app;
GRANT SELECT ON market_orderbook_discontinuity TO ce_v5_app;
GRANT SELECT, INSERT ON market_orderbook_snapshot TO ce_v5_ingestion;
GRANT SELECT, INSERT ON market_orderbook_discontinuity TO ce_v5_ingestion;

-- d) APPEND-ONLY REAL: nadie reescribe el libro, ni siquiera quien lo escribe. Un resync
--    no se "arregla" borrandolo: el hueco ocurrio y borrar la fila solo borraria la prueba.
REVOKE UPDATE, DELETE, TRUNCATE ON market_orderbook_snapshot
    FROM ce_v5_app, ce_v5_ingestion, ce_v5_operator;
REVOKE UPDATE, DELETE, TRUNCATE ON market_orderbook_discontinuity
    FROM ce_v5_app, ce_v5_ingestion, ce_v5_operator;
REVOKE ALL ON market_orderbook_snapshot FROM ce_v5_operator;
REVOKE ALL ON market_orderbook_discontinuity FROM ce_v5_operator;

-- e) OUTBOX: el ingestor ahora puede encolar tambien los dos market.orderbook_*. Las
--    policies de 0017 se RECREAN con el conjunto ampliado (candle_* + footprint_* +
--    orderbook_frontier + orderbook_resynced; cambio forward sobre el esquema vivo, 0017
--    no se edita). Siguen sin permitir familias ajenas (calca 0017 seccion e). La variante
--    'sample' NO aparece: no se publica, solo se persiste.
DROP POLICY outbox_ingestion_insert ON outbox;
DROP POLICY outbox_ingestion_read ON outbox;
DROP POLICY outbox_ingestion_update ON outbox;
CREATE POLICY outbox_ingestion_insert ON outbox
    FOR INSERT TO ce_v5_ingestion
    WITH CHECK (event_type IN (
        'market.candle_updated', 'market.candle_closed', 'market.candle_corrected',
        'market.footprint_closed', 'market.footprint_corrected',
        'market.orderbook_frontier', 'market.orderbook_resynced'));
CREATE POLICY outbox_ingestion_read ON outbox
    FOR SELECT TO ce_v5_ingestion
    USING (event_type IN (
        'market.candle_updated', 'market.candle_closed', 'market.candle_corrected',
        'market.footprint_closed', 'market.footprint_corrected',
        'market.orderbook_frontier', 'market.orderbook_resynced'));
CREATE POLICY outbox_ingestion_update ON outbox
    FOR UPDATE TO ce_v5_ingestion
    USING (event_type IN (
        'market.candle_updated', 'market.candle_closed', 'market.candle_corrected',
        'market.footprint_closed', 'market.footprint_corrected',
        'market.orderbook_frontier', 'market.orderbook_resynced'))
    WITH CHECK (event_type IN (
        'market.candle_updated', 'market.candle_closed', 'market.candle_corrected',
        'market.footprint_closed', 'market.footprint_corrected',
        'market.orderbook_frontier', 'market.orderbook_resynced'));
