-- Migracion 0019: is_complete en el footprint (P07b 3b-1; ADR-014, ADR-006, regla 5.20).
-- Sucesora de 0018. Append-only: ninguna migracion aplicada se edita (regla 5.14).
-- CE-14: NO toca el nucleo de ingesta; solo anade UNA columna a market_footprint.
--
-- POR QUE AQUI Y NO EN 0017. El contrato del footprint gano is_complete en la fase 3a (el
-- modelo honesto de backfill): una barra a la que le faltan trades por un hueco NO cubierto
-- se persiste como INCOMPLETA y JAMAS se emite como completa. La 0017 creo la tabla ANTES
-- de ese campo; 3b-1, que agrega el footprint, lo anade aqui para PERSISTIRLO, tal como la
-- 0018 lo anticipo ("una barra incompleta se persiste y se ve").
--
-- DEFAULT false = FAIL-SAFE, calcado del default del contrato (FootprintPayload): lo que no
-- declara su completitud cuenta como INCOMPLETO. El agregador lo fija SIEMPRE de forma
-- explicita; el default solo cubriria filas preexistentes (en 3b-1 no las hay) hacia el
-- lado seguro. NOT NULL: una barra sin veredicto de completitud no es un footprint valido.
--
-- SIN GRANTS NUEVOS: ce_v5_ingestion ya tiene INSERT sobre market_footprint (0017), que
-- cubre la columna nueva; ce_v5_app ya tiene SELECT. El append-only (UPDATE/DELETE/TRUNCATE
-- revocados) de la tabla sigue vigente: una columna no lo altera.

ALTER TABLE market_footprint
    ADD COLUMN is_complete boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN market_footprint.is_complete IS
    'True si la barra vio TODOS sus trades; False (fail-safe) si un hueco de market_trade_gap se solapa con [open_time, open_time+tf_ms). Lo fija 3b-1 al agregar. Ortogonal a maturity_state: una barra puede estar cerrada y a la vez incompleta.';
