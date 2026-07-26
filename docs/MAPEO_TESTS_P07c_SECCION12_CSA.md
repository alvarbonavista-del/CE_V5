# MAPEO TEST-POR-REQUISITO -- P07c / SECCION 12 DEL CSA

Deliverable de la remediacion G2 (dictamen de Central + seccion 12 del CSA).
Rama wip/p07c. ASCII-safe.

PARA QUE SIRVE. Demostrar la cobertura, no afirmarla: cada fila lleva el nombre REAL
del test que muerde, su fichero y su estado. Un requisito sin nombre de test es un
requisito NO cubierto, y aqui se ve.

COMO LEER EL ESTADO:
- CUBIERTO: el test ya existia antes de la remediacion G2.
- ANADIDO: el test nace en la remediacion G2.
- PARTIDO: el requisito estaba dentro de un test que cubria dos cosas; se separo en dos
  tests con su propia asercion, uno por requisito.
- PROCESO: el requisito NO se satisface con un test sino con un artefacto de proceso
  (registro, evidencia, run de Actions). Se referencia el artefacto exacto.

HISTORIA DE ESTE DOCUMENTO. En la primera version, el texto de la seccion 12 no constaba
en el repositorio: 11 requisitos venian citados en la tanda y 16 quedaron marcados
PENDIENTE DE TEXTO, sin inventarlos. Con el texto ya entregado (tanda G2-FINAL), la
antigua TABLA A (los 11 conocidos) y la TABLA B (los 16 pendientes) se FUNDEN en la
TABLA B unica de abajo -- los 27 en una sola tabla 1:1 --, para que no haya dos sitios
donde mantener lo mismo. NO QUEDA NINGUN PENDIENTE.

---

## TABLA B -- LOS 27 REQUISITOS DE LA SECCION 12 (texto verbatim del CSA)

| # | Requisito (verbatim) | Test(s) real(es) | Fichero | Estado |
|---|---|---|---|---|
| 1 | Snapshot completo con niveles -> pasa. | `TestIsCompleteFailSafe::test_is_complete_declarado_se_respeta`; `TestOrdenYProfundidad::test_bids_no_descendentes_rechazados` / `test_asks_no_ascendentes_rechazados` / `test_nivel_repetido_rechazado` / `test_un_lado_excede_depth_k_rechazado` (el camino completo, con sus guardias de orden y profundidad) | tests/unit/test_orderbook_family.py | CUBIERTO |
| 1 | ... y el motor lo produce asi de punta a punta | `TestFrontier::test_frontier_completo_si_no_hay_discontinuidad`, `TestTopK::test_el_top_k_recorta_y_ordena` | tests/unit/platform/market/test_orderbook_snapshot.py | CUBIERTO |
| 2 | Snapshot incompleto con niveles vacios -> pasa. | `TestOrdenYProfundidad::test_snapshot_incompleto_vacio_aceptado_y_round_trip` | tests/unit/test_orderbook_family.py | CUBIERTO |
| 3 | Snapshot completo con niveles vacios -> falla. | `TestOrdenYProfundidad::test_snapshot_completo_vacio_rechazado` | tests/unit/test_orderbook_family.py | CUBIERTO |
| 4 | Round-trip de todos los event_types orderbook con payload no vacio. | `TestRegistroCA06::test_los_dos_publicados_resuelven_a_su_payload`, `test_event_schema_version_de_los_dos`, `test_el_frontier_hace_ida_y_vuelta_por_el_registro`, `test_el_resync_hace_ida_y_vuelta_por_el_registro` | tests/unit/test_orderbook_family.py | CUBIERTO |
| 4 | ... barrido de TODO el registro, orderbook incluido (5.21) | `test_payload_serializa_no_vacio_y_revalida[market.orderbook_frontier]` y `[market.orderbook_resynced]`, con `test_completitud_sample_cubre_todo_el_registro` (nadie puede quedarse fuera del barrido) y `test_control_negativo_base_serializa_payload_vacio` | tests/unit/test_envelope_payload_roundtrip.py | CUBIERTO |
| 5 | cache_key cambia si cambia K. | `TestIdempotencyKeyLlevaLaConfig::test_distinta_K_distinta_clave` (contrato) y `TestIdempotencyReproducible::test_distinta_K_distinta_clave` (motor) | tests/unit/test_orderbook_family.py, tests/unit/platform/market/test_orderbook_snapshot.py | CUBIERTO |
| 6 | cache_key cambia si cambia cadencia. | `TestIdempotencyKeyLlevaLaConfig::test_distinta_cadencia_distinta_clave` (contrato) y `TestIdempotencyReproducible::test_distinta_cadencia_distinta_clave` (motor) | tests/unit/test_orderbook_family.py, tests/unit/platform/market/test_orderbook_snapshot.py | CUBIERTO |
| 7 | cache_key cambia si cambia timeframe. | `TestIdentidadDelFlujoEnLaClave::test_distinto_timeframe_distinta_clave` | tests/unit/platform/market/test_orderbook_snapshot.py | ANADIDO |
| 8 | cache_key cambia si cambia formula_version. | `TestIdempotencyKeyLlevaLaConfig::test_distinta_formula_version_distinta_clave` (contrato) y `TestIdempotencyReproducible::test_distinta_formula_version_distinta_clave` (motor) | tests/unit/test_orderbook_family.py, tests/unit/platform/market/test_orderbook_snapshot.py | CUBIERTO |
| 9 | Binance primer delta post-semilla permite puente. | `TestPuenteBinance::test_el_primer_delta_que_abarca_la_foto_se_aplica`, `test_un_delta_con_u_menor_o_igual_a_base_se_descarta` (el reenvio no gasta el puente) | tests/unit/platform/market/test_orderbook_book.py | CUBIERTO |
| 10 | Binance segundo delta y siguientes exigen continuidad estricta. | `TestPuenteBinance::test_tras_el_puente_la_continuidad_es_estricta`; contraprueba de que el puente es SOLO de Binance: `test_okx_no_usa_abarque_el_primer_delta_encadena_exacto`, `test_bybit_no_usa_abarque_el_primer_delta_encadena_exacto` | tests/unit/platform/market/test_orderbook_book.py | CUBIERTO |
| 11 | Hueco Binance real se marca discontinuity + is_complete=False. | is_complete=False: `TestHueco::test_un_salto_de_secuencia_pide_resync_y_marca_incompleto`, `TestPuenteBinance::test_si_ningun_delta_abarca_es_hueco_real` | tests/unit/platform/market/test_orderbook_book.py | CUBIERTO |
| 11 | ... discontinuity: el hueco publica su resync y queda apuntado | `TestResyncPublicado::test_un_hueco_detectado_publica_orderbook_resynced` (asercion `reason == "gap"`), `test_el_resync_se_publica_una_sola_vez_por_episodio` | tests/unit/platform/market/test_orderbook_ingestor.py | CUBIERTO |
| 11 | ... y la discontinuidad llega a la base junto a su outbox | `TestAtomicidadResync::test_discontinuidad_y_outbox_atomicos`, `test_el_mismo_hueco_no_duplica_ni_reencola` | tests/integration/test_market_orderbook.py | CUBIERTO |
| 12 | OKX keepalive seqId==prevSeqId no marca hueco. | `TestFueraDeOrden::test_okx_keepalive_no_es_hueco` | tests/unit/platform/market/test_orderbook_book.py | PARTIDO |
| 12 | ... y no se observa desde fuera (ni resync ni discontinuidad) | `TestIntegridadPorExchange::test_okx_keepalive_no_publica_nada` | tests/unit/platform/market/test_orderbook_ingestor.py | ANADIDO |
| 13 | OKX mantenimiento seqId<prevSeqId sigue ruta especial, no corrupcion silenciosa. | `TestFueraDeOrden::test_okx_mantenimiento_no_es_hueco_ni_corrupcion` (ruta NOOP: ni incompleto, ni resync, ni el libro tocado, ni la secuencia movida) | tests/unit/platform/market/test_orderbook_book.py | PARTIDO |
| 14 | OKX hueco real prevSeqId inesperado marca discontinuity. | `TestIntegridadPorExchange::test_okx_hueco_real_publica_resync_y_discontinuidad` (nivel INGESTOR: publica market.orderbook_resynced Y apunta la discontinuidad con reason 'gap') | tests/unit/platform/market/test_orderbook_ingestor.py | ANADIDO |
| 14 | ... la regla de continuidad en frio | `TestPuenteBinance::test_okx_no_usa_abarque_el_primer_delta_encadena_exacto`, `TestFueraDeOrden::test_un_delta_que_no_encadena_es_un_hueco` | tests/unit/platform/market/test_orderbook_book.py | CUBIERTO |
| 15 | Bybit u==1 resetea/reseed de forma observable. | `TestIntegridadPorExchange::test_bybit_u_igual_1_resetea_observable` (el libro se recupera Y la huella del hueco anterior NO se borra: la barra solapada sigue saliendo incompleta) | tests/unit/platform/market/test_orderbook_ingestor.py | ANADIDO |
| 15 | ... el reset en frio | `TestReset::test_una_foto_reenviada_reconstruye_el_libro`, `TestReset::test_un_reset_recupera_de_un_hueco` | tests/unit/platform/market/test_orderbook_book.py | CUBIERTO |
| 15 | ... y el traductor marca is_snapshot cuando u==1 | `TestDelta::test_u_igual_a_1_marca_is_snapshot` | tests/unit/infra/connectors/bybit/test_bybit_orderbook_translate.py | CUBIERTO |
| 16 | Bybit hueco real no se acepta como sano. | `TestHueco::test_bybit_hueco_real_no_se_acepta_como_sano` | tests/unit/platform/market/test_orderbook_book.py | ANADIDO |
| 17 | Arranque sin semilla emite frontera vacia solo con is_complete=False. | `TestFrontier::test_frontier_de_libro_sin_semilla_emite_incompleto_y_vacio` (fire-anyway honesto: emite, vacio, is_complete=False) | tests/unit/platform/market/test_orderbook_snapshot.py | CUBIERTO |
| 17 | ... el "SOLO con is_complete=False" lo muerde el contrato | `TestOrdenYProfundidad::test_snapshot_completo_vacio_rechazado` (vacio + completo = rechazado) frente a `test_snapshot_incompleto_vacio_aceptado_y_round_trip` | tests/unit/test_orderbook_family.py | CUBIERTO |
| 17 | ... y el cableado pasa el libro identificado aunque no haya semilla | `test_fronteriza_un_simbolo_sin_libro_pasa_libro_identificado` | tests/unit/entrypoints/worker_ingestion/test_orderbook_wiring.py | CUBIERTO |
| 18 | Resync a medias nunca emite is_complete=True. | `TestFrontier::test_frontier_incompleto_si_una_discontinuidad_solapa_la_barra` (aunque el libro ya se hubiera recuperado), `TestSample::test_una_muestra_de_un_libro_incompleto_sale_incompleta`; sin falso rojo: `test_una_discontinuidad_fuera_de_la_barra_no_la_marca` | tests/unit/platform/market/test_orderbook_snapshot.py | CUBIERTO |
| 18 | ... el libro en resync no se recompone a ciegas | `TestHueco::test_en_hueco_los_deltas_siguientes_no_recomponen_a_ciegas`, `TestHueco::test_una_foto_nueva_resuelve_el_hueco` (la unica salida es una foto) | tests/unit/platform/market/test_orderbook_book.py | CUBIERTO |
| 18 | ... y la incompletitud viaja a la base y vuelve | `TestIsCompleteViajaYVuelve::test_una_foto_incompleta_se_persiste_como_incompleta` | tests/integration/test_market_orderbook.py | CUBIERTO |
| 19 | Outbox falla -> snapshot/discontinuity rollback. | `TestRollbackOutbox::test_si_el_outbox_falla_el_snapshot_hace_rollback` (0 filas en snapshot Y 0 en outbox); atomicidad en el camino bueno: `TestAtomicidadFrontier::test_snapshot_y_outbox_o_los_dos_o_ninguno`, `TestAtomicidadResync::test_discontinuidad_y_outbox_atomicos` | tests/integration/test_market_orderbook.py | CUBIERTO |
| 20 | ce_v5_ingestion puede escribir orderbook. | `TestAtomicidadFrontier::test_snapshot_y_outbox_o_los_dos_o_ninguno`, `TestSampleSinOutbox::test_la_muestra_se_persiste_y_no_se_encola`, `TestFrontera520::test_el_ingestor_puede_encolar_los_dos_orderbook` (todos escriben con el DSN del rol de ingesta, fixture `ingestion_db`) | tests/integration/test_market_orderbook.py | CUBIERTO |
| 21 | ce_v5_app no puede escribir orderbook. | `TestFrontera520::test_la_api_no_puede_escribir_el_libro` (permission denied real de PostgreSQL, las dos tablas) | tests/integration/test_market_orderbook.py | CUBIERTO |
| 21 | ... y el check lo verifica sin base | `TestPrivilegios520::test_la_api_no_puede_escribir_velas` (incluye market_orderbook_snapshot y market_orderbook_discontinuity), `test_historico_append_only_para_todos_incluido_el_ingestor` | tests/unit/test_check_market_access.py | CUBIERTO |
| 22 | ce_v5_rules no puede escribir orderbook. | `test_el_motor_no_escribe_el_libro_l2` (INSERT/UPDATE/DELETE/TRUNCATE sobre las dos tablas del libro), con `test_el_caso_conforme_no_produce_violaciones` como control sin falso rojo | tests/unit/test_check_rules_access.py | ANADIDO |
| 23 | ce_v5_ingestion no puede tocar identity/rules/execution/policy. | rules (autoria): `test_el_ingestor_con_acceso_a_la_autoria_de_reglas_falla`, con `test_el_rol_de_aplicacion_si_puede_escribir_la_autoria` como control sin falso rojo | tests/unit/test_check_identity_access.py | ANADIDO |
| 23 | ... identity | `test_privilegio_directo_del_rol_de_aplicacion_falla` y el bucle de `_privilege_violations` sobre los TRES roles de runtime (el de ingesta incluido) | tests/unit/test_check_identity_access.py | CUBIERTO |
| 23 | ... policy (y auditoria) | `TestPrivilegios520::test_el_ingestor_no_toca_politica_ni_auditoria`; en caliente contra el motor: `test_5_20_b_el_ingestor_no_toca_identidad_politica_ni_auditoria` | tests/unit/test_check_market_access.py, tests/integration/test_market_access.py | CUBIERTO |
| 23 | ... execution | NO HAY TABLAS DE EXECUTION todavia (M5+): el rol nace sin privilegio sobre ellas y no se inventan nombres. Lo que SI se verifica hoy es que el ingestor no puede FABRICAR un execution.* por la outbox: `TestOutboxAcotadaPorElMotor::test_policy_que_menciona_otra_familia_es_violacion` y, contra el motor, `test_el_ingestor_no_puede_fabricar_un_execution_falso` | tests/unit/test_check_market_access.py, tests/integration/test_market_access.py | CUBIERTO (la parte verificable hoy) |
| 24 | check_market_access corre en Actions. | Step `Market - rol de ingesta y ventanilla agregada (5.20, CA-P07-D/G)` -> `uv run python tools/check_market_access.py`, job `Backend integration (DB + bus + tenancy)`. Espejado en `tools/ci_local.py` (paso 17/24) y vigilado en las DOS direcciones por su guardia anti-deriva (5.30) | .github/workflows/ci.yml, tools/ci_local.py | PROCESO |
| 25 | P07/P07b siguen verdes tras .vision. | Suites completas de velas (P07) y trades+footprint (P07b) verdes en la misma bateria: tests/unit/platform/market/{test_ingestor,test_normalize,test_trade_ingestor,test_trade_normalize,test_footprint_ingestor,test_footprint_aggregate}.py y tests/integration/{test_market_candles,test_market_store,test_market_trade_store,test_market_footprint,test_footprint_okx_gap_seam,test_worker_ingestion}.py, mas los del conector Binance (tests/unit/infra/connectors/binance/) | ci_local 24/24 verde + run de Actions del HEAD | PROCESO |
| 25 | ... y el .vision se SOSTIENE: los hosts quedan CLAVADOS por test | `test_el_ws_apunta_al_dominio_de_datos_no_geobloqueado`, `test_el_rest_apunta_al_dominio_de_datos_no_geobloqueado`, `test_ningun_host_geobloqueado_se_cuela` (pin literal de `_WS_BASE` y `_REST_BASE`: un revert accidental al host geobloqueado -- el fallo que arreglo ee21f0f -- se pone rojo en frio, sin esperar a la siguiente validacion en caliente) | tests/unit/infra/connectors/binance/test_binance_hosts.py | ANADIDO |
| 26 | Validacion caliente cruda referenciada en cierre. | docs/EVIDENCIA_CALIENTE_P07c.md, referenciado desde la seccion 27 del registro ("EVIDENCIA CRUDA EN EL RASTRO (5.32/5.18)") | docs/EVIDENCIA_CALIENTE_P07c.md, docs/contexto/REGISTRO_DECISIONES_CONSTRUCCION.md | PROCESO |
| 27 | 5.31/5.32 registradas y firmadas. | REGISTRADAS: SI, y es verificable -- seccion 5 del registro, reglas 5.31 y 5.32, commit 7569401. FIRMADAS: acto humano de Alvaro, NO verificable por el periferico; la seccion 27 lo deja explicitamente PENDIENTE ("su FIRMA por Alvaro es acto humano PENDIENTE... se deja constancia de que se USARON, no de que esten firmadas") | docs/contexto/REGISTRO_DECISIONES_CONSTRUCCION.md | PROCESO (registro SI; firma pendiente de Alvaro) |

NOTA sobre el item 22. El requisito exigido es la NO ESCRITURA. El check anadido prohibe
INSERT/UPDATE/DELETE/TRUNCATE del rol de reglas sobre las dos tablas del libro y NO
prohibe la lectura: P08c consumira el frontier y decidir hoy que el motor no puede leerlo
seria adelantarse a una decision que no es de esta tanda.

NOTA sobre el item 25 -- OBSERVACION CERRADA (tanda G2-PIN). El requisito pide que P07 y
P07b sigan VERDES tras el cambio de host, y eso se demuestra con la bateria. Se anoto
ademas que los hosts `.vision` eran dos constantes de
`infra/connectors/binance/connector.py` (`_WS_BASE`, `_REST_BASE`) que NINGUN test fijaba:
un revert accidental no habria puesto nada en rojo, y el fallo solo habria reaparecido en
la siguiente validacion en caliente. POR DECISION DE ALVARO se anade la red de seguridad:
tests/unit/infra/connectors/binance/test_binance_hosts.py clava los dos hosts en frio.
connector.py NO se toco: el test se limita a leer sus constantes.

---

## AL PIE DE LA TABLA B -- EXTRA DE CENTRAL Y LA DECLARACION

| Requisito (Central, G2) | Test real | Fichero | Estado |
|---|---|---|---|
| Distinto TIMEFRAME -> distinta clave (= item 7) | `TestIdentidadDelFlujoEnLaClave::test_distinto_timeframe_distinta_clave` | tests/unit/platform/market/test_orderbook_snapshot.py | ANADIDO |
| Distinto EXCHANGE -> distinta clave | `TestIdentidadDelFlujoEnLaClave::test_distinto_exchange_distinta_clave` | tests/unit/platform/market/test_orderbook_snapshot.py | ANADIDO |
| Distinto SYMBOL -> distinta clave | `TestIdentidadDelFlujoEnLaClave::test_distinto_symbol_distinta_clave` | tests/unit/platform/market/test_orderbook_snapshot.py | ANADIDO |
| Distinto MARKET_TYPE -> distinta clave | `TestIdentidadDelFlujoEnLaClave::test_distinto_market_type_distinta_clave` | tests/unit/platform/market/test_orderbook_snapshot.py | ANADIDO |
| DataSourceDeclaration del snapshot con cache_key_schema explicito de DIEZ dimensiones (ADR-008; clock_source incluido por dictamen G2/clock_source) | `TestCacheKeySchema::test_la_cache_key_declara_la_dimension` (10 casos: exchange, symbol, market_type, data_family, depth_k, cadence_ms, timeframe, frontier_time_anchor, formula_version, clock_source), `test_el_schema_no_repite_dimensiones`, `test_la_constante_y_la_declaracion_no_se_separan`, `TestDeclaracionCoherenteConADR008` (8 tests), `TestLasDimensionesDiscriminanDeVerdad::test_cambiar_la_dimension_cambia_la_clave_persistida` (10 casos) | tests/unit/test_orderbook_datasource.py | ANADIDO |
| clock_source mueve la clave (huella concreta, ya existente: NO se duplica) | `TestIdempotencyReproducible::test_distinto_clock_source_distinta_clave` | tests/unit/platform/market/test_orderbook_snapshot.py | CUBIERTO |

Codigo de la declaracion: backend/src/ce_v5/platform/rules/rawbook.py
(`orderbook_snapshot_declaration`, espejo de `market_close_declaration` en rawclose.py).

NOTA SOBRE MARKET_TYPE. v5.0 solo tiene `MarketType.SPOT` (los derivados quedan fuera de
alcance), asi que el segundo tipo de mercado no se puede construir por el motor. La
discriminacion se prueba donde vive -- la funcion de clave -- con el stream_key de cada
tipo; el dia que entre otro tipo, el test ya muerde sin tocarlo.

---

## TABLA C -- INVENTARIO COMPLETO DE LA SUITE P07c (POR AREA)

Es la superficie contra la que se emparejan los requisitos de la TABLA B.

| Area | Fichero | Tests | Que demuestra |
|---|---|---|---|
| Contrato de la familia orderbook | tests/unit/test_orderbook_family.py | 34 | Registro CA-06 e ida y vuelta por el sobre; is_complete fail-safe por defecto; orden y profundidad del top-K (bids desc, asks asc, sin repetir, dentro de K); vacio admitido SOLO si incompleto; ventana alineada; coherencia kind/sample_time; la idempotency_key lleva la config (K, cadencia, formula_version, ventana) y frontier/sample no colisionan; clave del resync |
| Declaracion ADR-008 | tests/unit/test_orderbook_datasource.py | 12 (30 casos) | Una dimension de cache_key por test (DIEZ); coherencia de la declaracion (observable, non_servible, recursive, decimal, contextos, unidades, publica cross-tenant, base); cada dimension declarada mueve la clave REAL |
| Motor del libro (frio) | tests/unit/platform/market/test_orderbook_book.py | 29 | Semilla; hueco y no-recomposicion a ciegas; reset de Bybit; duplicado; puente de Binance (U<=base+1<=u) y su ausencia en OKX/Bybit; keepalive y mantenimiento de OKX; fuera de orden; foto/delta corruptos con atomicidad; pertenencia (anti-suplantacion); exchange sin regla = fallo de cableado |
| Motor de ingesta (orquestacion) | tests/unit/platform/market/test_orderbook_ingestor.py | 12 | Siembra y aplicacion; resync publicado una vez por episodio; integridad por exchange observable en el canon (OKX hueco/keepalive, Bybit reset); reconexion que re-siembra y apunta discontinuidad; backpressure sin perdida; aislamiento por stream |
| Motor de snapshot | tests/unit/platform/market/test_orderbook_snapshot.py | 19 | Muestra con el is_complete del libro; frontier incompleto si una discontinuidad solapa la barra; event_time = open_time; frontera fire-anyway sin semilla; top-K recorta y ordena; la clave separa por config y por identidad de flujo |
| Cableado del worker | tests/unit/entrypoints/worker_ingestion/test_orderbook_wiring.py | 14 | Cadencia del sampler; disparo de la frontera keyed a open_time, tambien en barra plana; olvido de claves inactivas; la frontera no toca candle_corrected; drenaje/muestreo/fronterizado y aislamiento del fallo de una barra |
| Persistencia y frontera 5.20 | tests/integration/test_market_orderbook.py | 14 | Atomicidad snapshot+outbox y discontinuidad+outbox; rollback; muestra sin encolar; dedup de muestra y de frontier; is_complete viaja y vuelve; el jsonb conserva el decimal; la API no escribe el libro; el ingestor no lo reescribe; encola los dos orderbook y no una familia ajena; el frontier encolado se publica al bus |

Traductores por exchange (parte de la superficie de P07c):
tests/unit/infra/connectors/{binance,bybit,okx}/ -- traduccion de foto y delta sin validar
dominio, texto decimal intacto, tamano cero conservado, rechazo de nivel malformado, u==1
marca is_snapshot (Bybit), semilla por REST con buffer previo del WS (Binance).

Checks de rol que cubren orderbook: tests/unit/test_check_market_access.py,
tests/unit/test_check_rules_access.py, tests/unit/test_check_identity_access.py y
tests/integration/test_market_access.py.

---

## LAS DIMENSIONES DE LA cache_key, Y POR QUE NO LLEVA schema_version

`ORDERBOOK_SNAPSHOT_CACHE_KEY_SCHEMA` declara DIEZ dimensiones. Cada una corresponde a
una parte real de la clave que construye `orderbook_snapshot_idempotency_key`
(contracts/source/families/orderbook.py):

| Dimension declarada | Donde vive en la clave persistida |
|---|---|
| exchange | dentro del stream_key (`market:orderbook:<exchange>:<mkt>:<symbol>`) |
| symbol | dentro del stream_key |
| market_type | dentro del stream_key |
| data_family | dentro del stream_key (el segmento `orderbook`) |
| timeframe | segmento propio (`tf.value`) |
| frontier_time_anchor | el as_of: `open_time` de la barra (el sample anade su `sample_time`) |
| depth_k | segmento `k<depth_k>` |
| cadence_ms | segmento `c<cadence_ms>` |
| formula_version | segmento `v<formula_version>` |
| clock_source | segmento `cs<clock_source>` |

DECIMA DIMENSION, POR DICTAMEN DE CENTRAL (G2/clock_source): `clock_source` ENTRA en el
schema declarado. Estaba ya en la clave que se persiste, pero no en la declaracion; con
esto, declaracion y clave persistida coinciden dimension a dimension. La lectura que lo
resuelve: dos capturas del MISMO as_of por relojes distintos (real vs simulado) son
HECHOS DISTINTOS, y una fuente que no lo declara podria compartir evaluacion entre una
captura real y una simulada.

OMISION JUSTIFICADA DE `schema_version` (criterio mantenido). El payload
`OrderbookSnapshotPayload` NO tiene un campo `schema_version` propio que pueda variar sin
subir `formula_version`. La version del esquema del evento vive en el SOBRE
(`event_schema_version`, fijada por el registro de familias y gobernada por ADR-005), no
en el payload; y cualquier cambio semantico del recorte top-K sube `formula_version`, que
SI esta en la clave. Anadir una dimension que no puede variar por si sola no anadiria
garantia: seria ruido en la clave. Si algun dia el payload gana un `schema_version`
propio, esta omision se reabre.

---

## QUE NO SE TOCO

Regla de oro de las dos tandas de remediacion: solo test/consistencia. El motor de
ingesta (orderbook_book, orderbook_ingestor, orderbook_snapshot, conectores) NO se toco.
La declaracion es ADITIVA (CE-14) y NO se registra en el catalogo vivo del worker de
reglas (entrypoints/worker_rules/composition.py): registrarla exigiria un evaluador para
una fuente que en v5.0 no se sirve como termino escalar, y eso seria tocar el nucleo. La
declaracion existe, es verificable por test, y queda lista para el catalogo de I-02.
