-- Migracion 0017: trades individuales + footprint (P07b; ADR-014, ADR-007, ADR-011).
-- Sucesora de 0016. Append-only: ninguna migracion aplicada se edita (regla 5.14).
-- Regla 5.20 calcada de 0012: ce_v5_ingestion es el UNICO que escribe estas tablas;
-- ce_v5_app SOLO LEE; el operador no las toca. Lo verifica tools/check_market_access.py.
-- CE-14: NO toca el nucleo de ingesta; solo anade tablas, grants y amplia la outbox.

-- a) TRADES INDIVIDUALES (data_family=trades, ADR-014). public_market, sin tenant_id.
--    Identidad natural (exchange, market_type, symbol, trade_id): una reconexion que
--    reenvie trades ya vistos no los duplica. El motor defiende el dato (ADR-006).
CREATE TABLE market_trade (
    exchange         text NOT NULL,
    market_type      text NOT NULL,
    symbol           text NOT NULL,
    trade_id         text NOT NULL,
    price            numeric NOT NULL,
    qty              numeric NOT NULL,
    aggressor_side   text NOT NULL,
    event_time       bigint NOT NULL,
    source_sequence  bigint,
    ingested_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (exchange, market_type, symbol, trade_id),
    CONSTRAINT market_trade_precio_positivo CHECK (price > 0),
    CONSTRAINT market_trade_tamano_positivo CHECK (qty > 0),
    CONSTRAINT market_trade_lado_valido CHECK (aggressor_side IN ('buy', 'sell'))
);
CREATE INDEX market_trade_stream_tiempo_idx
    ON market_trade (exchange, market_type, symbol, event_time);
COMMENT ON TABLE market_trade IS
    'Trades individuales normalizados (P07b, data_family=trades, ADR-014). isolation_scope=public_market, sin tenant_id: dato publico. La API NO puede escribirlo (regla 5.20). Base del footprint y de la reproducibilidad bit a bit. Retencion/trimming se dimensiona tras medicion empirica en caliente (P07b); hasta entonces no hay borrado.';

-- b) FOOTPRINT POR BARRA (P07b): historia canonica derivada, como la vela. Una fila por
--    barra; las celdas (precio x volumen comprador/vendedor/delta) en JSONB, ya validadas
--    por el contrato en el borde (ADR-006). PK = idempotency_key (identidad logica del
--    hecho, ADR-003); una correccion NO muta el original (append-only): fila nueva. Solo
--    closed y correction: no hay footprint provisional.
CREATE TABLE market_footprint (
    idempotency_key          text PRIMARY KEY,
    stream_key               text NOT NULL,
    exchange                 text NOT NULL,
    market_type              text NOT NULL,
    symbol                   text NOT NULL,
    timeframe                text NOT NULL,
    open_time                bigint NOT NULL,
    close_time               bigint NOT NULL,
    cells                    jsonb NOT NULL,
    bar_buy_volume           numeric NOT NULL,
    bar_sell_volume          numeric NOT NULL,
    bar_delta                numeric NOT NULL,
    trade_count              integer NOT NULL,
    maturity_state           text NOT NULL,
    correction_revision      integer,
    corrects_idempotency_key text,
    ingested_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT market_footprint_madurez_persistible
        CHECK (maturity_state IN ('closed', 'correction')),
    CONSTRAINT market_footprint_correccion_coherente CHECK (
        (maturity_state = 'closed'
            AND correction_revision IS NULL
            AND corrects_idempotency_key IS NULL)
        OR
        (maturity_state = 'correction'
            AND correction_revision >= 1
            AND corrects_idempotency_key IS NOT NULL)
    ),
    CONSTRAINT market_footprint_totales_coherentes
        CHECK (bar_buy_volume >= 0
               AND bar_sell_volume >= 0
               AND bar_delta = bar_buy_volume - bar_sell_volume
               AND trade_count >= 0),
    CONSTRAINT market_footprint_ventana_coherente
        CHECK (close_time > open_time)
);
CREATE INDEX market_footprint_stream_ventana_idx
    ON market_footprint (stream_key, open_time DESC);
COMMENT ON TABLE market_footprint IS
    'Footprint por barra (P07b, ADR-014). isolation_scope=public_market, sin tenant_id: dato publico. Una fila por barra; celdas en JSONB validadas por el contrato. Append-only real: UPDATE/DELETE/TRUNCATE revocados a TODOS los roles de runtime. Solo admite closed y correction.';

-- c) PRIVILEGIOS ESTRECHOS (regla 5.20). La API LEE; el ingestor escribe.
GRANT SELECT ON market_trade TO ce_v5_app;
GRANT SELECT ON market_footprint TO ce_v5_app;
GRANT SELECT, INSERT ON market_trade TO ce_v5_ingestion;
GRANT SELECT, INSERT ON market_footprint TO ce_v5_ingestion;

-- d) APPEND-ONLY REAL: nadie reescribe la historia, ni siquiera quien la escribe.
REVOKE UPDATE, DELETE, TRUNCATE ON market_trade
    FROM ce_v5_app, ce_v5_ingestion, ce_v5_operator;
REVOKE UPDATE, DELETE, TRUNCATE ON market_footprint
    FROM ce_v5_app, ce_v5_ingestion, ce_v5_operator;
REVOKE ALL ON market_trade FROM ce_v5_operator;
REVOKE ALL ON market_footprint FROM ce_v5_operator;

-- e) OUTBOX: el ingestor ahora puede encolar tambien los dos market.footprint_*. Las
--    policies de 0012 se RECREAN con los cinco market.* (cambio forward sobre el esquema
--    vivo; 0012 no se edita). Siguen sin permitir familias ajenas.
DROP POLICY outbox_ingestion_insert ON outbox;
DROP POLICY outbox_ingestion_read ON outbox;
DROP POLICY outbox_ingestion_update ON outbox;
CREATE POLICY outbox_ingestion_insert ON outbox
    FOR INSERT TO ce_v5_ingestion
    WITH CHECK (event_type IN (
        'market.candle_updated', 'market.candle_closed', 'market.candle_corrected',
        'market.footprint_closed', 'market.footprint_corrected'));
CREATE POLICY outbox_ingestion_read ON outbox
    FOR SELECT TO ce_v5_ingestion
    USING (event_type IN (
        'market.candle_updated', 'market.candle_closed', 'market.candle_corrected',
        'market.footprint_closed', 'market.footprint_corrected'));
CREATE POLICY outbox_ingestion_update ON outbox
    FOR UPDATE TO ce_v5_ingestion
    USING (event_type IN (
        'market.candle_updated', 'market.candle_closed', 'market.candle_corrected',
        'market.footprint_closed', 'market.footprint_corrected'))
    WITH CHECK (event_type IN (
        'market.candle_updated', 'market.candle_closed', 'market.candle_corrected',
        'market.footprint_closed', 'market.footprint_corrected'));
