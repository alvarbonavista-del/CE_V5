-- Migracion 0016: el motor de reglas LEE el mercado publico (P08 D1, CA-P08-04).
-- Sucesora de 0015. Append-only: ninguna migracion aplicada se edita.
--
-- POR QUE. El motor evalua sobre la vela CERRADA (invariante CA-P07-A: jamas sobre
-- candle_updated provisional), y para aplicar las funciones canonicas continuas
-- (average/change/previous_value/value_at) necesita la VENTANA de cierres previos del
-- mismo flujo. Esa ventana vive en market_candle. La 0013 hizo REVOKE ALL sobre las
-- tres tablas de mercado a ce_v5_rules -- correcto entonces (el motor aun no evaluaba);
-- aqui se abre la MINIMA rendija que la evaluacion exige: SELECT sobre market_candle.
--
-- POR QUE ES SEGURO ABRIRLA. market_candle es isolation_scope=public_market (0012):
-- dato PUBLICO compartido cross-tenant, SIN tenant_id y SIN RLS. Leerlo no cruza
-- ninguna frontera de tenant porque no hay frontera que cruzar: no es dato de sujeto.
-- Es la misma lectura que ya tiene ce_v5_app.
--
-- LO QUE NO SE CONCEDE, Y POR QUE (regla 5.20: solo lo que la funcion exige):
--   - market_instrument: NO IMPRESCINDIBLE en v5.0. El motor recibe exchange/symbol ya
--     CANONICOS por el evento market.candle_closed y por market_scope de la regla; la
--     traduccion a la forma nativa del exchange (BTCUSDT <-> BTC-USDT) es del adaptador
--     de ingesta, no del evaluador. El motor nunca resuelve un simbolo nativo, asi que
--     no necesita el catalogo. Si un dia lo necesitara, sera su propia migracion con su
--     justificacion, no un grant preventivo "por si acaso".
--   - market_public_demand(): es la ventanilla de la DEMANDA de suscripcion, para el
--     INGESTOR (0012). El motor no decide que flujos se suscriben.
--   - market_subscription_intent: la ESCRITURA del intent de una regla (D2) es de la
--     AUTORIA (ce_v5_app, camino user-driven), no del motor. El motor no declara
--     demanda: la consume ya materializada en velas.

-- a) LA RENDIJA MINIMA: solo lectura del historico canonico de velas.
GRANT SELECT ON market_candle TO ce_v5_rules;

-- b) APPEND-ONLY REAL, tambien para el motor (mismo estilo explicito y auditable que
--    0012 apartado g y 0013 apartado f). Un rol nuevo no tiene estos privilegios por
--    defecto y el GRANT de arriba no los concede; el REVOKE los deja NEGADOS DE FORMA
--    EXPLICITA para que nadie los reintroduzca por descuido y para que el check 7.x
--    tenga que morder si alguien lo hace. Nadie reescribe la historia del mercado.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON market_candle FROM ce_v5_rules;

-- c) Y el resto de la superficie de mercado sigue CERRADA al motor (reafirma 0013 tras
--    haber abierto la rendija de arriba: que quede junto a ella, no a tres migraciones
--    de distancia).
REVOKE ALL ON market_instrument, market_subscription_intent FROM ce_v5_rules;
