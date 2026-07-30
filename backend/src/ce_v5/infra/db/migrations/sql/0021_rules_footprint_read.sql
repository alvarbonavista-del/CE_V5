-- Migracion 0021: el motor de reglas LEE el footprint publico (P08c CE-14, MAT-04).
-- Sucesora de 0020. Append-only: ninguna migracion aplicada se edita.
--
-- POR QUE. La sub-pieza de materializacion (CE-14) materializa vp.*/orderflow/cvd sobre
-- la VENTANA de footprints del mismo flujo. Esa ventana vive en market_footprint. La
-- 0013 hizo REVOKE ALL sobre las tablas de mercado a ce_v5_rules; aqui se abre la MINIMA
-- rendija que la materializacion exige: SELECT sobre market_footprint, espejo exacto de
-- 0016 para market_candle.
--
-- POR QUE ES SEGURO. market_footprint es dato PUBLICO de mercado (como market_candle):
-- sin tenant_id, sin RLS; leerlo no cruza ninguna frontera de tenant. La ESCRITURA sigue
-- NEGADA de forma explicita (append-only tambien para el motor): el motor materializa
-- LEYENDO el footprint, no lo ingiere.

-- a) LA RENDIJA MINIMA: solo lectura del historico de footprint.
GRANT SELECT ON market_footprint TO ce_v5_rules;

-- b) APPEND-ONLY REAL, tambien para el motor: escritura NEGADA de forma explicita para
--    que el check 5.20 muerda si alguien la reintroduce.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON market_footprint FROM ce_v5_rules;
