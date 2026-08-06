-- Migracion 0029: el motor de reglas LEE el frontier del libro L2 (P08c-CONF-05).
-- Sucesora de 0028. Append-only: ninguna migracion aplicada se edita (regla 5.14).
--
-- POR QUE. El bloque L2 de notrade (peso 35, RESERVADO desde P08c-DET-01) deja de estar
-- diferido: sus cuatro features (imbalance_vol, liquidity_shift, spoof_proxy, thin_book)
-- se computan sobre el SNAPSHOT FRONTIER del libro -- la foto as-of el cierre de cada
-- barra --, que vive en market_orderbook_snapshot. La 0020 dio SELECT solo a ce_v5_app y
-- ce_v5_ingestion; el materializador de notrade.* corre con ce_v5_rules, asi que sin
-- esta rendija no puede leer el libro y el bloque seguiria valiendo 0.
--
-- ESPEJO EXACTO DE LA 0021 (que abrio market_footprint al motor): misma forma, misma
-- justificacion, mismo REVOKE explicito de escritura.
--
-- POR QUE ES SEGURO. market_orderbook_snapshot es dato PUBLICO de mercado (como
-- market_candle y market_footprint): sin tenant_id, sin RLS; leerlo no cruza ninguna
-- frontera de tenant. La 0020 ya lo dejo previsto -- check_rules_access.py documenta que
-- el libro NO va en las tablas prohibidas porque "P08c consumira el frontier del libro y
-- podria necesitar LECTURA" --; esta migracion ejecuta esa decision.
--
-- QUE NO ABRE. market_orderbook_discontinuity NO se toca: el motor no necesita el
-- registro de resyncs para computar las features (el fail-safe de una barra sin frontier
-- se resuelve con la AUSENCIA de la fila, no leyendo el hueco). Un grant preventivo "por
-- si acaso" es exactamente lo que la regla 5.20 prohibe.

-- a) LA RENDIJA MINIMA: solo lectura del snapshot del libro.
GRANT SELECT ON market_orderbook_snapshot TO ce_v5_rules;

-- b) APPEND-ONLY REAL, tambien para el motor: escritura NEGADA de forma explicita para
--    que el check 5.20 muerda si alguien la reintroduce. Un snapshot del libro fabricado
--    por el motor alimentaria sus propias reglas de orderflow.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON market_orderbook_snapshot FROM ce_v5_rules;
