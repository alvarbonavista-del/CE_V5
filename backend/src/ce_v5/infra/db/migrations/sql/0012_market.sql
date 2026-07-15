-- Migracion 0012: market data (ADR-014, ADR-007, ADR-011) + rol de ingesta
-- (regla 5.20, CA-P07-B). Sucesora de 0011. Append-only: ninguna migracion
-- aplicada se edita.
--
-- REGLA 5.20 - NADIE FABRICA HECHOS AJENOS. Hasta hoy, la API (expuesta a
-- internet) compartia el rol con todo lo demas y podria INSERTAR velas. Una vela
-- falsa es un HECHO FABRICADO que alimenta reglas -> senales -> en M5, ORDENES
-- REALES. Se separa el poder: ce_v5_ingestion es el UNICO que escribe market
-- data; ce_v5_app pasa a SOLO LECTURA sobre ella. Lo hace cumplir el MOTOR
-- (grants + RLS), no la buena conducta del codigo. Generaliza CA-03/CA-04/CA-07.
--
-- LO PROVISIONAL NO ES HISTORIA (dictamen P07-A): market_candle solo admite
-- velas 'closed' y 'correction'. Una vela provisional es una vista viva que se
-- publica directa al bus y NO se persiste; el CHECK de la tabla lo hace
-- imposible, no un if en Python.

-- a) Rol de ingesta sin LOGIN; la credencial la provisiona el entorno (CE-13).
--    Mismo patron que 0004 (ce_v5_app) y 0008 (ce_v5_operator).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ce_v5_ingestion') THEN
        CREATE ROLE ce_v5_ingestion NOLOGIN NOSUPERUSER NOBYPASSRLS
            NOCREATEDB NOCREATEROLE NOREPLICATION INHERIT;
    END IF;
END
$$;

-- b) HISTORICO CANONICO DE VELAS (ADR-014: "historico canonico en DB").
--    isolation_scope=public_market: dato PUBLICO compartido cross-tenant, SIN
--    tenant_id (ADR-011: los publicos no se duplican por tenant). Allowlistado
--    con justificacion escrita en tools/check_tenancy.py.
--    La PK es la idempotency_key: es la identidad LOGICA del hecho (ADR-003), y
--    una correccion NO muta el original (append-only, ADR-007): es una fila
--    NUEVA con su propia clave.
CREATE TABLE market_candle (
    idempotency_key         text PRIMARY KEY,
    stream_key              text NOT NULL,
    exchange                text NOT NULL,
    market_type             text NOT NULL,
    symbol                  text NOT NULL,
    timeframe               text NOT NULL,
    open_time               bigint NOT NULL,
    close_time              bigint NOT NULL,
    open                    numeric NOT NULL,
    high                    numeric NOT NULL,
    low                     numeric NOT NULL,
    close                   numeric NOT NULL,
    volume                  numeric NOT NULL,
    maturity_state          text NOT NULL,
    correction_revision     integer,
    corrects_idempotency_key text,
    ingested_at             timestamptz NOT NULL DEFAULT now(),
    -- Lo provisional NO se persiste: no es historia, es una vista viva.
    CONSTRAINT market_candle_madurez_persistible
        CHECK (maturity_state IN ('closed', 'correction')),
    -- Una correccion referencia SIEMPRE el hecho que corrige y numera su
    -- revision (dos correcciones de la misma vela son dos hechos distintos).
    CONSTRAINT market_candle_correccion_coherente CHECK (
        (maturity_state = 'closed'
            AND correction_revision IS NULL
            AND corrects_idempotency_key IS NULL)
        OR
        (maturity_state = 'correction'
            AND correction_revision >= 1
            AND corrects_idempotency_key IS NOT NULL)
    ),
    -- El dato viene de un tercero: el motor tambien lo defiende (ADR-006).
    CONSTRAINT market_candle_precios_positivos
        CHECK (open > 0 AND high > 0 AND low > 0 AND close > 0 AND volume >= 0),
    CONSTRAINT market_candle_rango_coherente
        CHECK (high >= low
               AND high >= greatest(open, close)
               AND low <= least(open, close)),
    CONSTRAINT market_candle_ventana_coherente
        CHECK (close_time > open_time)
);
CREATE INDEX market_candle_stream_ventana_idx
    ON market_candle (stream_key, open_time DESC);
COMMENT ON TABLE market_candle IS
    'Historico canonico de velas (ADR-014). isolation_scope=public_market: dato publico compartido cross-tenant, SIN tenant_id (ADR-011). Append-only real: UPDATE/DELETE/TRUNCATE revocados a TODOS los roles de runtime. Solo admite closed y correction: lo provisional no es historia.';

-- c) CATALOGO DE INSTRUMENTOS (alcance anadido a P07 por Central).
--    Es un CONTROL DE SEGURIDAD, no una comodidad: sin el, validar que un
--    interes apunta a un par REAL seria humo y se podrian fabricar
--    MarketStreamKeys arbitrarios (DoS por cardinalidad).
--    symbol es CANONICO (BTC-USDT); native_symbol es como lo llama el exchange
--    (BTCUSDT en Binance): sin esa traduccion, el mismo mercado tendria dos
--    identidades.
CREATE TABLE market_instrument (
    exchange      text NOT NULL,
    market_type   text NOT NULL,
    symbol        text NOT NULL,
    native_symbol text NOT NULL,
    status        text NOT NULL DEFAULT 'active',
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (exchange, market_type, symbol),
    CONSTRAINT market_instrument_status_valido
        CHECK (status IN ('active', 'inactive')),
    -- min 1: Binance tiene el ticker 'T' (Threshold), par TUSDT -> T-USDT. El {2,15}
    -- original era una suposicion sin verificar que la validacion en caliente sobre
    -- datos reales de Binance desmintio. max 20 por meme-tokens largos.
    CONSTRAINT market_instrument_symbol_canonico
        CHECK (symbol ~ '^[A-Z0-9]{1,20}-[A-Z0-9]{1,20}$')
);
CREATE UNIQUE INDEX market_instrument_nativo_idx
    ON market_instrument (exchange, market_type, native_symbol);
COMMENT ON TABLE market_instrument IS
    'Catalogo de instrumentos por exchange (P07). isolation_scope=public_market, sin tenant_id: es dato publico. La API NO puede escribirlo (regla 5.20): un instrumento inventado tambien es un hecho fabricado.';

-- d) DEMANDA DE SUSCRIPCION (ADR-014: MarketInterestRegistry).
--    isolation_scope=user: quien quiere que se traiga que es dato DEL SUJETO.
--    RLS ENABLE + FORCE atada al contexto transaccional de P05.
--    El ref-count NO se persiste: es estado operativo RECONSTRUIBLE desde esta
--    tabla (ADR-014). Esta tabla es la fuente de verdad; el ref-count, no.
CREATE TABLE market_subscription_intent (
    intent_id         uuid PRIMARY KEY,
    tenant_id         uuid NOT NULL REFERENCES tenant (tenant_id) ON DELETE CASCADE,
    user_id           uuid NOT NULL REFERENCES app_user (user_id) ON DELETE CASCADE,
    stream_scope      text NOT NULL,
    market_stream_key text NOT NULL,
    exchange          text NOT NULL,
    market_type       text NOT NULL,
    symbol            text NOT NULL,
    data_kind         text NOT NULL,
    timeframe         text,
    source_type       text NOT NULL,
    source_ref        text NOT NULL,
    priority          integer NOT NULL DEFAULT 100,
    expires_at        timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT market_intent_scope_valido
        CHECK (stream_scope IN ('public_market', 'user')),
    CONSTRAINT market_intent_prioridad_valida
        CHECK (priority BETWEEN 1 AND 1000),
    -- Un mismo origen no duplica su propio interes por el mismo flujo.
    CONSTRAINT market_intent_origen_unico
        UNIQUE (tenant_id, user_id, source_type, source_ref, market_stream_key)
);
CREATE INDEX market_intent_stream_idx
    ON market_subscription_intent (market_stream_key);
CREATE INDEX market_intent_sujeto_idx
    ON market_subscription_intent (tenant_id, user_id);
COMMENT ON TABLE market_subscription_intent IS
    'Demanda de suscripcion agregada (ADR-014: SubscriptionIntent). isolation_scope=user: tenant_id + user_id con RLS. Es la FUENTE DE VERDAD desde la que se RECONSTRUYE el ref-count tras un reinicio; el ref-count es estado operativo, no fuente de verdad. expires_at NULL = interes PERSISTENTE (reglas/alertas); con valor = interes EFIMERO (widgets), para que no queden suscripciones zombis.';

ALTER TABLE market_subscription_intent ENABLE ROW LEVEL SECURITY;
ALTER TABLE market_subscription_intent FORCE ROW LEVEL SECURITY;
CREATE POLICY market_intent_isolation ON market_subscription_intent
    FOR ALL TO ce_v5_app
    USING (
        tenant_id = app_current_tenant_id()
        AND user_id = app_current_user_id()
    )
    WITH CHECK (
        tenant_id = app_current_tenant_id()
        AND user_id = app_current_user_id()
    );

-- Policy del DUENO de la ventanilla (CA-P07-G, opcion 1, firmada).
-- FORCE RLS somete tambien al dueno; sin esta policy la ventanilla veria CERO
-- FILAS. Va ESTRECHADA a proposito:
--   FOR SELECT      -> solo lectura: el dueno no escribe intents por esta via.
--   stream_scope='public_market' -> ni el dueno puede leer por aqui los
--                      intereses PRIVADOS/BYOC. La condicion (1) de CA-P07-D
--                      deja de ser una promesa del cuerpo de la funcion y pasa a
--                      ser un HECHO IMPUESTO POR EL MOTOR.
-- Esta policy NO se aplica a NINGUN rol de runtime, y el check 7.8 endurecido
-- ROMPE EL BUILD si alguien la ensancha a uno.
DO $$
BEGIN
    EXECUTE format(
        'CREATE POLICY market_intent_owner_read ON market_subscription_intent '
        'FOR SELECT TO %I USING (stream_scope = ''public_market'')',
        current_user);
END
$$;

-- e) LA VENTANILLA AGREGADA (CA-P07-D, firmada).
--    PROBLEMA: el proposito entero de ADR-014 es que dos tenants interesados en
--    el MISMO flujo compartan UN SOLO stream. Esa union es CROSS-TENANT por
--    naturaleza, pero la RLS de P05 impide al worker leer los intereses de todos
--    los tenants (y hace bien).
--    SOLUCION (patron CA-07): una ventanilla que devuelve SOLO la demanda
--    AGREGADA -- la clave del stream y CUANTOS lo piden -- y NADA sobre QUIEN lo
--    pide. El worker obtiene la union que necesita y no aprende nada de nadie.
--    La tabla base conserva su RLS INTACTA.
--    Solo flujos PUBLICOS: los intereses privados/BYOC JAMAS pasan por aqui.
--    La allowlist ESTRICTA de columnas de retorno la verifica
--    tools/check_market_access.py: si alguien intenta devolver un tenant_id, el
--    build se rompe.
CREATE FUNCTION market_public_demand()
RETURNS TABLE (out_market_stream_key text, out_intent_count bigint)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT i.market_stream_key, count(*)
    FROM market_subscription_intent i
    WHERE i.stream_scope = 'public_market'
      AND (i.expires_at IS NULL OR i.expires_at > now())
    GROUP BY i.market_stream_key
$$;
COMMENT ON FUNCTION market_public_demand() IS
    'Ventanilla (CA-P07-D): demanda AGREGADA de flujos PUBLICOS. Devuelve la clave del stream y cuantos intereses vivos lo piden; NUNCA tenant_id, user_id, source_ref ni ningun identificador de sujeto. Agregacion sin fuga de identidad: el worker sabe que dos lo piden, no quienes son.';

REVOKE EXECUTE ON FUNCTION market_public_demand() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION market_public_demand() TO ce_v5_ingestion;

-- f) PRIVILEGIOS ESTRECHOS (regla 5.20). Nada de GRANT ALL.
-- La API (expuesta a internet): LEE market data, NO la escribe.
GRANT SELECT ON market_candle TO ce_v5_app;
GRANT SELECT ON market_instrument TO ce_v5_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON market_subscription_intent TO ce_v5_app;

-- El ingestor: escribe velas y catalogo; NO ve los intereses (solo la
-- ventanilla); NO toca identidad, politica, auditoria de operador ni ordenes.
GRANT SELECT, INSERT ON market_candle TO ce_v5_ingestion;
GRANT SELECT, INSERT, UPDATE ON market_instrument TO ce_v5_ingestion;

-- g) APPEND-ONLY REAL sobre el historico: REVOKE explicito y auditable, tambien
--    para el propio ingestor. Nadie reescribe la historia del mercado.
REVOKE UPDATE, DELETE, TRUNCATE ON market_candle
    FROM ce_v5_app, ce_v5_ingestion, ce_v5_operator;
REVOKE ALL ON market_candle FROM ce_v5_operator;
REVOKE ALL ON market_instrument FROM ce_v5_operator;
REVOKE ALL ON market_subscription_intent FROM ce_v5_ingestion, ce_v5_operator;
REVOKE DELETE, TRUNCATE ON market_instrument FROM ce_v5_app, ce_v5_ingestion;

-- h) OUTBOX: el ingestor encola SOLO market.* (mismo patron que CA-04 con el
--    operador). Un ingestor comprometido NO puede fabricar un execution.* ni un
--    policy.* falso: se lo impide el MOTOR.
--    Necesita SELECT y UPDATE ademas de INSERT porque drena su propia outbox y
--    marca published_at; su policy de SELECT lo limita a SUS PROPIAS filas, asi
--    que no ve ni drena eventos de otras familias.
GRANT SELECT, INSERT, UPDATE ON outbox TO ce_v5_ingestion;
CREATE POLICY outbox_ingestion_insert ON outbox
    FOR INSERT TO ce_v5_ingestion
    WITH CHECK (
        event_type IN (
            'market.candle_updated',
            'market.candle_closed',
            'market.candle_corrected'
        )
    );
CREATE POLICY outbox_ingestion_read ON outbox
    FOR SELECT TO ce_v5_ingestion
    USING (
        event_type IN (
            'market.candle_updated',
            'market.candle_closed',
            'market.candle_corrected'
        )
    );
CREATE POLICY outbox_ingestion_update ON outbox
    FOR UPDATE TO ce_v5_ingestion
    USING (
        event_type IN (
            'market.candle_updated',
            'market.candle_closed',
            'market.candle_corrected'
        )
    )
    WITH CHECK (
        event_type IN (
            'market.candle_updated',
            'market.candle_closed',
            'market.candle_corrected'
        )
    );
REVOKE DELETE, TRUNCATE ON outbox FROM ce_v5_ingestion;
