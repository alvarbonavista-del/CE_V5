# MAPEO TEST-POR-REQUISITO -- P07c / SECCION 12 DEL CSA

Deliverable de la remediacion G2 (dictamen de Central + seccion 12 del CSA).
Rama wip/p07c. ASCII-safe.

PARA QUE SIRVE. Demostrar la cobertura, no afirmarla: cada fila lleva el nombre REAL
del test que muerde, su fichero y su estado. Un requisito sin nombre de test es un
requisito NO cubierto, y aqui se ve.

COMO LEER EL ESTADO:
- CUBIERTO: el test ya existia antes de esta tanda.
- ANADIDO: el test nace en la tanda G2 (remediacion).
- PARTIDO: el requisito estaba dentro de un test que cubria dos cosas; se separo en
  dos tests con su propia asercion, uno por requisito.

ADVERTENCIA HONESTA SOBRE EL ALCANCE DE ESTE DOCUMENTO. El texto integro de la
seccion 12 del CSA (los 27 requisitos) NO consta en el repositorio: solo llegaron
citados los que la tanda G2 nombra (7, 12, 13, 14, 15, 16, 20, 21, 22, 23, 24). Los
16 restantes NO se inventan aqui -- un requisito redactado a ojo y dado por cubierto
seria exactamente el verde ilusorio que la regla 5.31 existe para impedir --. Van en
la TABLA B con su estado real (PENDIENTE DE TEXTO) y la TABLA C da el inventario
COMPLETO de la suite de P07c para que completarlos, cuando el texto se pegue, sea
mecanico y no una nueva investigacion.

---

## TABLA A -- REQUISITOS CON TEXTO CONOCIDO

| # | Requisito (seccion 12 CSA) | Test(s) real(es) | Fichero | Estado |
|---|---|---|---|---|
| 7 | La huella (idempotency/cache_key) cambia con el TIMEFRAME | `TestIdentidadDelFlujoEnLaClave::test_distinto_timeframe_distinta_clave` | tests/unit/platform/market/test_orderbook_snapshot.py | ANADIDO |
| 12 | El keepalive de OKX (seqId == prevSeqId) NO es hueco | `TestFueraDeOrden::test_okx_keepalive_no_es_hueco` | tests/unit/platform/market/test_orderbook_book.py | PARTIDO |
| 12 | ... y tampoco se observa desde fuera (ni resync ni discontinuidad) | `TestIntegridadPorExchange::test_okx_keepalive_no_publica_nada` | tests/unit/platform/market/test_orderbook_ingestor.py | ANADIDO |
| 13 | El mantenimiento de OKX (seqId < prevSeqId) NO es hueco NI corrupcion | `TestFueraDeOrden::test_okx_mantenimiento_no_es_hueco_ni_corrupcion` | tests/unit/platform/market/test_orderbook_book.py | PARTIDO |
| 14 | Hueco REAL de OKX (prevSeqId inesperado) -> discontinuidad, a nivel INGESTOR | `TestIntegridadPorExchange::test_okx_hueco_real_publica_resync_y_discontinuidad` | tests/unit/platform/market/test_orderbook_ingestor.py | ANADIDO |
| 14 | ... la regla de continuidad en frio (nivel libro) | `TestPuenteBinance::test_okx_no_usa_abarque_el_primer_delta_encadena_exacto`, `TestFueraDeOrden::test_un_delta_que_no_encadena_es_un_hueco` | tests/unit/platform/market/test_orderbook_book.py | CUBIERTO |
| 15 | Bybit u==1 (reset) re-siembra, y es OBSERVABLE | `TestIntegridadPorExchange::test_bybit_u_igual_1_resetea_observable` | tests/unit/platform/market/test_orderbook_ingestor.py | ANADIDO |
| 15 | ... el reset en frio (nivel libro) | `TestReset::test_una_foto_reenviada_reconstruye_el_libro`, `TestReset::test_un_reset_recupera_de_un_hueco` | tests/unit/platform/market/test_orderbook_book.py | CUBIERTO |
| 16 | Un hueco REAL de Bybit NO se acepta como sano | `TestHueco::test_bybit_hueco_real_no_se_acepta_como_sano` | tests/unit/platform/market/test_orderbook_book.py | ANADIDO |
| 20 | ce_v5_ingestion ESCRIBE el libro (positivo) | `TestAtomicidadFrontier::test_snapshot_y_outbox_o_los_dos_o_ninguno`, `TestSampleSinOutbox::test_la_muestra_se_persiste_y_no_se_encola`, `TestFrontera520::test_el_ingestor_puede_encolar_los_dos_orderbook` | tests/integration/test_market_orderbook.py | CUBIERTO |
| 21 | ce_v5_app NO escribe el libro | `TestFrontera520::test_la_api_no_puede_escribir_el_libro` | tests/integration/test_market_orderbook.py | CUBIERTO |
| 21 | ... y el check lo verifica sin base | `TestPrivilegios520::test_la_api_no_puede_escribir_velas` (incluye las dos tablas de orderbook) | tests/unit/test_check_market_access.py | CUBIERTO |
| 22 | ce_v5_rules NO escribe market_orderbook_* | `test_el_motor_no_escribe_el_libro_l2` | tests/unit/test_check_rules_access.py | ANADIDO |
| 23 | ce_v5_ingestion NO toca identidad / reglas / execution / policy | `test_el_ingestor_con_acceso_a_la_autoria_de_reglas_falla` (autoria de reglas) | tests/unit/test_check_identity_access.py | ANADIDO |
| 23 | ... identidad | `test_privilegio_directo_del_rol_de_aplicacion_falla` y el bucle `_privilege_violations` sobre los tres roles de runtime | tests/unit/test_check_identity_access.py | CUBIERTO |
| 23 | ... politica y auditoria | `TestPrivilegios520::test_el_ingestor_no_toca_politica_ni_auditoria` | tests/unit/test_check_market_access.py | CUBIERTO |
| 23 | ... execution.* | NO HAY TEST: no existen tablas de execution todavia (M5+). El rol nace sin privilegio sobre ellas; se anaden al check cuando existan, no se inventan nombres. | tools/check_identity_access.py (comentario en `RULES_AUTHORING_TABLES`) | NO APLICA EN v5.0 |
| 24 | ci.yml ejecuta check_market_access | Step `Market - rol de ingesta y ventanilla agregada (5.20, CA-P07-D/G)` -> `uv run python tools/check_market_access.py`, en el job de integracion; espejado por `tools/ci_local.py` y vigilado por su guardia anti-deriva (5.30) | .github/workflows/ci.yml | CUBIERTO |

NOTA sobre el item 22. El requisito exigido es la NO ESCRITURA. El check anadido
prohibe INSERT/UPDATE/DELETE/TRUNCATE del rol de reglas sobre las dos tablas del
libro y NO prohibe la lectura: P08c consumira el frontier y decidir hoy que el motor
no puede leerlo seria adelantarse a una decision que no es de esta tanda.

---

## TABLA B -- REQUISITOS CUYO TEXTO NO CONSTA EN EL REPOSITORIO

Estado real: PENDIENTE DE TEXTO. No estan declarados cubiertos ni descubiertos; falta
el enunciado para poder emparejarlos 1:1. La suite que los cubriria (si los cubre) es
la de la TABLA C.

| # | Requisito | Estado |
|---|---|---|
| 1 | (texto no disponible en el repo) | PENDIENTE DE TEXTO |
| 2 | (texto no disponible en el repo) | PENDIENTE DE TEXTO |
| 3 | (texto no disponible en el repo) | PENDIENTE DE TEXTO |
| 4 | (texto no disponible en el repo) | PENDIENTE DE TEXTO |
| 5 | (texto no disponible en el repo) | PENDIENTE DE TEXTO |
| 6 | (texto no disponible en el repo) | PENDIENTE DE TEXTO |
| 8 | (texto no disponible en el repo) | PENDIENTE DE TEXTO |
| 9 | (texto no disponible en el repo) | PENDIENTE DE TEXTO |
| 10 | (texto no disponible en el repo) | PENDIENTE DE TEXTO |
| 11 | (texto no disponible en el repo) | PENDIENTE DE TEXTO |
| 17 | (texto no disponible en el repo) | PENDIENTE DE TEXTO |
| 18 | (texto no disponible en el repo) | PENDIENTE DE TEXTO |
| 19 | (texto no disponible en el repo) | PENDIENTE DE TEXTO |
| 25 | (texto no disponible en el repo) | PENDIENTE DE TEXTO |
| 26 | (texto no disponible en el repo) | PENDIENTE DE TEXTO |
| 27 | (texto no disponible en el repo) | PENDIENTE DE TEXTO |

COMO CERRAR ESTA TABLA: pegar la seccion 12 del CSA en una tanda [CLAUDE CODE]; cada
requisito se empareja contra la TABLA C, que ya lista la suite entera por area, y las
filas pasan a la TABLA A con su estado.

---

## TABLA C -- INVENTARIO COMPLETO DE LA SUITE P07c (POR AREA)

134 tests en 7 ficheros. Es la superficie contra la que se emparejan los requisitos
de la TABLA B.

| Area | Fichero | Tests | Que demuestra |
|---|---|---|---|
| Contrato de la familia orderbook | tests/unit/test_orderbook_family.py | 34 | Registro CA-06 e ida y vuelta por el sobre; is_complete fail-safe por defecto; orden y profundidad del top-K (bids desc, asks asc, sin repetir, dentro de K); vacio admitido SOLO si incompleto; ventana alineada; coherencia kind/sample_time; la idempotency_key lleva la config (K, cadencia, formula_version, ventana) y frontier/sample no colisionan; clave del resync |
| Declaracion ADR-008 | tests/unit/test_orderbook_datasource.py | 12 | Una dimension de cache_key por test; coherencia de la declaracion (observable, non_servible, recursive, decimal, contextos, unidades, publica cross-tenant, base); cada dimension declarada mueve la clave REAL |
| Motor del libro (frio) | tests/unit/platform/market/test_orderbook_book.py | 29 | Semilla; hueco y no-recomposicion a ciegas; reset de Bybit; duplicado; puente de Binance (U<=base+1<=u) y su ausencia en OKX/Bybit; keepalive y mantenimiento de OKX; fuera de orden; foto/delta corruptos con atomicidad; pertenencia (anti-suplantacion); exchange sin regla = fallo de cableado |
| Motor de ingesta (orquestacion) | tests/unit/platform/market/test_orderbook_ingestor.py | 12 | Siembra y aplicacion; resync publicado una vez por episodio; integridad por exchange observable en el canon (OKX hueco/keepalive, Bybit reset); reconexion que re-siembra y apunta discontinuidad; backpressure sin perdida; aislamiento por stream |
| Motor de snapshot | tests/unit/platform/market/test_orderbook_snapshot.py | 19 | Muestra con el is_complete del libro; frontier incompleto si una discontinuidad solapa la barra; event_time = open_time; frontera fire-anyway sin semilla; top-K recorta y ordena; la clave separa por config y por identidad de flujo |
| Cableado del worker | tests/unit/entrypoints/worker_ingestion/test_orderbook_wiring.py | 14 | Cadencia del sampler; disparo de la frontera keyed a open_time, tambien en barra plana; olvido de claves inactivas; la frontera no toca candle_corrected; drenaje/muestreo/fronterizado y aislamiento del fallo de una barra |
| Persistencia y frontera 5.20 | tests/integration/test_market_orderbook.py | 14 | Atomicidad snapshot+outbox y discontinuidad+outbox; rollback; muestra sin encolar; dedup de muestra y de frontier; is_complete viaja y vuelve; el jsonb conserva el decimal; la API no escribe el libro; el ingestor no lo reescribe; encola los dos orderbook y no una familia ajena; el frontier encolado se publica al bus |

Traductores por exchange (no orderbook-especificos de la seccion 12, pero parte de la
superficie de P07c): tests/unit/infra/connectors/{binance,bybit,okx}/ -- traduccion de
foto y delta sin validar dominio, texto decimal intacto, tamano cero conservado,
rechazo de nivel malformado, u==1 marca is_snapshot (Bybit), semilla por REST con
buffer previo del WS (Binance).

---

## TABLA D -- EXTRA DE CENTRAL (fuera de los 27) + LA DECLARACION

| Requisito (Central, G2) | Test real | Fichero | Estado |
|---|---|---|---|
| Distinto TIMEFRAME -> distinta clave | `TestIdentidadDelFlujoEnLaClave::test_distinto_timeframe_distinta_clave` | tests/unit/platform/market/test_orderbook_snapshot.py | ANADIDO |
| Distinto EXCHANGE -> distinta clave | `TestIdentidadDelFlujoEnLaClave::test_distinto_exchange_distinta_clave` | tests/unit/platform/market/test_orderbook_snapshot.py | ANADIDO |
| Distinto SYMBOL -> distinta clave | `TestIdentidadDelFlujoEnLaClave::test_distinto_symbol_distinta_clave` | tests/unit/platform/market/test_orderbook_snapshot.py | ANADIDO |
| Distinto MARKET_TYPE -> distinta clave | `TestIdentidadDelFlujoEnLaClave::test_distinto_market_type_distinta_clave` | tests/unit/platform/market/test_orderbook_snapshot.py | ANADIDO |
| DataSourceDeclaration del snapshot con cache_key_schema explicito (ADR-008) | `TestCacheKeySchema::test_la_cache_key_declara_la_dimension` (una dimension por test, 9), `test_el_schema_no_repite_dimensiones`, `test_la_constante_y_la_declaracion_no_se_separan`, `TestDeclaracionCoherenteConADR008` (8 tests), `TestLasDimensionesDiscriminanDeVerdad::test_cambiar_la_dimension_cambia_la_clave_persistida` (9 casos) | tests/unit/test_orderbook_datasource.py | ANADIDO |

Codigo de la declaracion: backend/src/ce_v5/platform/rules/rawbook.py
(`orderbook_snapshot_declaration`, espejo de `market_close_declaration` en
rawclose.py).

NOTA SOBRE MARKET_TYPE. v5.0 solo tiene `MarketType.SPOT` (los derivados quedan fuera
de alcance), asi que el segundo tipo de mercado no se puede construir por el motor. La
discriminacion se prueba donde vive -- la funcion de clave -- con el stream_key de cada
tipo; el dia que entre otro tipo, el test ya muerde sin tocarlo.

---

## LAS DIMENSIONES DE LA cache_key, Y POR QUE NO LLEVA schema_version

`ORDERBOOK_SNAPSHOT_CACHE_KEY_SCHEMA` declara NUEVE dimensiones. Cada una corresponde
a una parte real de la clave que construye `orderbook_snapshot_idempotency_key`
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

OMISION JUSTIFICADA DE `schema_version` (Paso 1c de la tanda). El payload
`OrderbookSnapshotPayload` NO tiene un campo `schema_version` propio que pueda variar
sin subir `formula_version`. La version del esquema del evento vive en el SOBRE
(`event_schema_version`, fijada por el registro de familias y gobernada por ADR-005),
no en el payload; y cualquier cambio semantico del recorte top-K sube
`formula_version`, que SI esta en la clave. Anadir una dimension que no puede variar
por si sola no anadiria garantia: seria ruido en la clave.

OBSERVACION PARA CENTRAL (no es un cambio, es un dato). La clave PERSISTIDA lleva una
decima parte que el `cache_key_schema` dictado no enumera: `clock_source` (segmento
`cs<clock_source>`, refino de procedencia de Central ya en el contrato, con su test
`test_distinto_clock_source_distinta_clave`). Se ha respetado el enunciado a la letra y
NO se ha anadido por cuenta propia, porque las dos lecturas son defendibles:
`clock_source` es procedencia de la CAPTURA (en produccion es siempre 'system', asi que
no discrimina evaluaciones compartidas), no una dimension de la EVALUACION. Queda
anotado para que Central decida si debe entrar tambien en el schema declarado.

---

## QUE NO SE TOCO

Regla de oro de la tanda: solo test/consistencia. El motor de ingesta (orderbook_book,
orderbook_ingestor, orderbook_snapshot, conectores) NO se toco. La declaracion nueva es
ADITIVA (CE-14) y NO se registra en el catalogo vivo del worker de reglas
(entrypoints/worker_rules/composition.py): registrarla exigiria un evaluador para una
fuente que en v5.0 no se sirve como termino escalar, y eso seria tocar el nucleo. La
declaracion existe, es verificable por test, y queda lista para el catalogo de I-02.
