# ESTADO DE CONSTRUCCION - Crypto Engine V5

Archivo vivo de estado de proceso (sin logica). Lo mantiene Claude Code
en disco; Alvaro lo resube al knowledge cada vez que se cierra una pieza
o un hito (DOC_ENTREGABLES sec.8).

Ultima actualizacion: 2026-07-23 (P07b ENTREGADA: cierre formal con doble revision
Central+CSA; fase 3b -agregacion del footprint- cerrada sobre la 3a (conectores de trades
+ modelo honesto de backfill), tests de costura anadidos y DoD de retencion redefinido
(escenario B). M3 SIGUE ABIERTO: faltan P07c, P08b, P08c, P09a. Ver REGISTRO_DECISIONES
seccion 26. T-05 ENTREGADA y en main; T-04 (feasibility comparador TradingView) CERRADA:
verificacion del RSI por FIXTURE (no hay API en vivo)).

## Hito actual
M3 (datos, reglas y notificacion backend) ABIERTO y EXPANDIDO a paridad
funcional v4 (EXP-M3-01, firmada 2026-07-17; doble revision Central + CSA;
no reabre ADR). Piezas de M3 y su estado:
  - P07  (ingesta de market data) .................... ENTREGADA
  - T-03 (conectores OKX y Bybit; transversal) ....... ENTREGADA
  - P07b (trades + footprint) ........................ ENTREGADA
  - P07c (orderbook L2 con estado) ................... PENDIENTE
  - P08  (motor de reglas) ........................... ENTREGADA
  - P08b (DataSources candle-derived) ................ PENDIENTE
  - P08c (DataSources footprint/L2-derived) .......... PENDIENTE
  - P09a (router de notificaciones backend) .......... PENDIENTE
Orden: P07 -> T-03 -> P07b -> P07c -> P08 -> P08b -> P08c -> P09a.
Paralelismo admitido: P08 || P07b || P07c || P08b; P08c tras P07b+P07c;
P09a tras P08.
Investigaciones:
  - I-03 (pivotes/divergencias) ...... COMPLETO (5 secciones). Pendiente solo
    GAP-P08c, que se cierra EN CONSTRUCCION, antes de P08c.
  - I-04 (orderflow) ................. COMPLETO (Partes 1-5 consolidadas).
La investigacion de pivotphase/divergencias/orderflow (I-03 + I-04) queda
CERRADA; alimenta la construccion de P08b/P08c.
DOC_ROADMAP_V5 incorpora la ampliacion en su SECCION A-1 (append-only,
2026-07-18): alli esta el M3 ampliado y la ficha de P07b, P07c, P08b y P08c.
El contenido original v1.0 queda intacto como historico. Las decisiones
asociadas siguen en REGISTRO_DECISIONES sec.21.
Proximo hito (tras M3): M4.

## Pieza actual
P08 - Motor de reglas (ADR-015/016/017): ENTREGADA. NO cierra M3 (tras ella
  quedan P07b, P07c, P08b, P08c y P09a).
  Commit de pieza: 59855bf.
  Refinamiento documental de las puertas de revision: 107e94f.
  ACTIONS VERDE 3/3 (backend, backend-integration, frontend) sobre 107e94f,
  cabeza del PR wip->main (run #18). El job backend-integration corrio por
  PRIMERA VEZ la provision de ce_v5_rules y el check_rules_access sobre un
  PostgreSQL VIRGEN del runner: es lo que exige la regla 5.22 (no basta el
  barrido local). 1040 tests; CERO SKIPS en local con los CINCO DSN.
  Merge a main por git con --no-ff (143f4f0) para PRESERVAR los hashes que el
  registro cita; la caja "Merge" de GitHub los habria reescrito.
  Doble revision Central + CSA conforme; firmado por Alvaro 2026-07-21.
  Resumen: una Rule dispara sobre market data real y proyecta alert.*/signal.*
  POR TRANSICION (CA-P08-01), con FSM K3 + veto fail-safe, persistencia ATOMICA
  de estado + outbox en una sola transaccion (rollback demostrado contra
  PostgreSQL real), ventanilla cross-tenant rules_for_market SECURITY DEFINER
  donde manda el tenant de la COLUMNA (no el del JSON), rol ce_v5_rules estrecho
  (regla 5.20) y correccion point-local end-to-end por candle_corrected
  (CA-P08-08). Reglas nuevas 5.21 y 5.22; la 5.22 nace de un defecto real del
  cierre (check_rules_access construido pero NO enganchado en ci.yml, y P08 sin
  ningun test de integracion), corregido dentro de la propia pieza.
  Cierre de contexto en el commit "docs(contexto): registra hash, Actions y firma
  del cierre de P08" (regla 5.9); su hash se registra en el commit inmediato
  posterior.

## Pieza anterior
P07 - Ingesta de market data (hibrida), ADR-014: ENTREGADA. ABRE el hito M3
  (no lo cierra: M3 = P07 + P08 + P09a).
  Commit de pieza: e7c92be.
  Commit final (cierre de huecos de la doble revision): f62e4e0.
  ACTIONS VERDE 3/3 (backend, backend-integration, frontend) sobre f62e4e0
  (run #9). 870 tests; cero skips en local.
  Doble revision Central + CSA conforme; firmado por Alvaro.
  Resumen: ingesta hibrida (ADR-014). Publicos compartidos sin tenant (un stream
  por MarketStreamKey); privados por-usuario gateados (connector FAKE); ref-count
  reconstruible; auto-bootstrap tras reconexion en el motor; conector REAL de
  Binance Spot (feed publico); rol ce_v5_ingestion estrecho (regla 5.20) con check
  bloqueante MARKET; ventanilla agregada cross-tenant sin fuga (CA-P07-D/G); los
  tres market.* registrados (CA-06 pagado, DEFERRED vacio); barrido de seguridad
  5.15.
  Cierre de contexto en el commit "docs(contexto): cierre P07" (regla 5.9); su hash
  se registra en el commit inmediato posterior.
  Hash del commit de cierre de contexto: 77e8067d9a465fdab818abdf06f4beab6428e02b.
  (Un commit no puede contener su propio hash: por eso el hash del cierre 'docs(contexto):
  cierre P07' se registra en este commit inmediato posterior. Regla 5.9 cumplida: cero
  cola en el arbol.)

## Pieza en curso
Ninguna. P07b ENTREGADA (cierre formal, ver "Piezas cerradas"). Las siguientes de M3
(P07c, P08b, P08c, P09a) estan PENDIENTES; admiten paralelismo P07c y P08b.
T-05 (visor de desarrollo, transversal): CERRADA (ver "Transversales cerradas").

## Piezas cerradas
- P00 - Esqueleto de repositorio + CI base: ENTREGADA (hito M0 CERRADO).
  Commits: d3f7ad6 -> 15f936d.
- P01 - Contratos base y envelope: ENTREGADA. Commit 17bb584.
- P02 - Modelo temporal y Clock: ENTREGADA. Commit 271d677.
- P02b - Persistencia base + migraciones + outbox: ENTREGADA.
  Commit ed3e788.
- P03 - Sustrato EventBus + adapter Redis: ENTREGADA. Commit cb25b81.
  Con P03 se cierra el hito M1 (4 de 4).
- P04 - Raiz Componente, manifest, discovery, lifecycle: ENTREGADA.
  Commit 866b434. Abre el hito M2.
- P05 - Tenancy shared-schema + RLS: ENTREGADA. Commit 795deb3.
- P06 - PolicyEvaluator central + kill switch: ENTREGADA. Commit 06cb51f.
- P06b - API/Auth/Realtime Gateway: ENTREGADA. Commit de pieza 6864c2a; commit
  final 52b26db. Con P06b se cierra el hito M2 (4 de 4).
- P07 - Ingesta de market data (hibrida): ENTREGADA. Commit de pieza e7c92be;
  commit final f62e4e0. Abre el hito M3.
- P07b - Trades + footprint: ENTREGADA (cierre formal, doble revision Central+CSA).
  Fase 3a (conectores de trades Binance/OKX/Bybit + modelo honesto de backfill) y 3b
  (agregacion del footprint: celda=tick nativo lossless, reproducibilidad bit a bit,
  is_complete fail-safe, outbox atomico, worker propio bajo ce_v5_ingestion). Retencion:
  escenario B (append-only, sin trimming; tarea post-P08c). Ver REGISTRO_DECISIONES
  secciones 24 (3a) y 26 (cierre). Commits 3a en main: 78920bf, 437a1dc, 308f812,
  e53fa22, e08bf6d, 295770a, 5dba7af.
- P08 - Motor de reglas (ADR-015/016/017): ENTREGADA. Commit de pieza 59855bf;
  refinamiento documental 107e94f; merge a main 143f4f0. Actions verde 3/3 sobre
  107e94f. NO cierra M3.
Van 12 piezas cerradas de 23 (inventario ampliado de 19 a 23 por EXP-M3-01,
firmada 2026-07-17: entran P07b, P07c, P08b y P08c).

## Transversales cerradas (no cuentan en las 23 piezas)
- T-05 - Visor de desarrollo (herramienta desechable / semilla de P13): ENTREGADA/CERRADA.
  Visor minimo en frontend/src/dev-viewer (Vite 8.0.14 SOLO dev-server, KLineChart 10.0.0),
  alimentado por el endpoint publico GET /v1/public/market/candles (read-only, public_market,
  borde ADR-006: symbol canonico + timeframe enum -> 422). Hueco de DataSources preparado
  para P08b/P08c (inerte hoy). Commits: backend en abb7324 (NOTA DE TRAZABILIDAD, ver
  REGISTRO seccion T-05: aterrizo dentro de un commit de P07b "Tanda 3a-i" por un git add
  amplio del working-tree; no se reescribe main, se registra -> origen de la regla 5.29);
  remate del borde 422 en 5acc9e0; visor en f7890e1. Merge wip/t-05-visor -> main con
  --no-ff. Actions verde 3/3 run #30010566621 (PR #2).
- T-04 - Feasibility del comparador TradingView (transversal de investigacion): CERRADA.
  GO/NO-GO: comparador EN VIVO por API oficial NO FACTIBLE (RETIRADO); comparador por FIXTURE
  (CSV "Download chart data": velas + RSI de TradingView sobre las mismas velas) FACTIBLE y es
  LA via para P08b. DoD de P08b "verificacion contra TradingView" = dentro de tolerancia tras
  warm-up (puerta <= 0,1 pts de RSI), NO bit a bit (no reabre ADR-007). GOLDEN FIXTURE versionado
  por simbolo/timeframe. DESACOPLE: si la frontera legal de Alvaro no despeja TradingView, se usa
  un referente Wilder publicado (P08b no se bloquea). Sin codigo/commit de producto. Informe:
  claude/INFORME_T04_FEASIBILITY_TRADINGVIEW_v5.md. Detalle en REGISTRO_DECISIONES seccion 25.

## Regla de trabajo (REGISTRO_DECISIONES sec.1)
Construccion en micro-pasos: el periferico nunca entrega la pieza entera
de golpe. Un paso, se explica, Alvaro ejecuta y pega salida, siguiente.

## Notas
- Guardarrailes vivos desde el commit 0. Sin deuda, sin codigo muerto,
  sin placeholders.
- Windows local requiere PYTHONUTF8=1 y PYTHONIOENCODING=utf-8.
- Docker Desktop (backend WSL2) requerido para el PostgreSQL local de
  pruebas y el check de integracion DB/bus (ADR-013).
- Checks activos tras P05: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8 (activado
  en P05), 7.9, integracion DB e integracion del bus (job backend-integration
  con PostgreSQL y Redis 8.8), mas lint/format/type (backend) y biome/tsc/
  depcruise (frontend); todos verdes en local. Ya no queda ningun check
  inactivo.
- Checks activos tras P06: 7.1-7.9 + check audit (tools/check_audit.py) + check
  de registro event_type->payload (tools/check_event_payload_registry.py) +
  integracion DB/bus. Todos verdes en local.
- Checks activos tras P06b: 7.1-7.9 + audit + IDENTITY (NUEVO, CA-07:
  tools/check_identity_access.py; exige que el rol de aplicacion NO tenga
  privilegios de tabla sobre las tablas de secretos y que el acceso vaya por
  ventanillas SECURITY DEFINER) + registro event_type->payload + integracion
  DB/bus/API. TODOS EN VERDE en local Y en Actions.
- 7.5/7.6/7.9 (P04): tools/check_manifests, tools/check_orphans,
  tools/check_component_docs; enganchados al job backend del workflow.
- Componentes: viven en backend/src/ce_v5/components/<nombre>/ (manifest.json
  + __init__ entrypoint + logica + tests + README); testpaths incluye esa
  carpeta. Componente de referencia: sample.
- Contracts: la fuente Pydantic se importa como paquete 'source'
  (source.envelope / source.families / source.time); raiz de importacion
  en contracts/. El vocabulario de lifecycle vive en
  source.families.component (P04 D1).
- Persistencia (P02b): variable CE_V5_DATABASE_URL con el DSN de
  PostgreSQL; migraciones via "python -m ce_v5.infra.db.migrations";
  entorno local en infra/compose/docker-compose.yml.
- Bus (P03): variable CE_V5_REDIS_URL con la URL de Redis; entorno local en
  infra/compose/docker-compose.yml (PostgreSQL + Redis). El contrato "source"
  se instala en runtime (pyproject: wheel packages incluye contracts/source).
- Tenancy (P05, ADR-011): CE_V5_DATABASE_URL = rol de APLICACION (ce_v5_app;
  sin SUPERUSER ni BYPASSRLS; sometido a RLS). CE_V5_MIGRATIONS_DATABASE_URL =
  rol de MIGRACIONES (dueno de las tablas; NO corre en runtime).
  CE_V5_APP_DB_PASSWORD = credencial del rol de aplicacion, que provisiona
  "python -m ce_v5.infra.db.provision" (se ejecuta con el rol de migraciones).
  Migraciones: "python -m ce_v5.infra.db.migrations" corre con el rol de
  migraciones. Check 7.8: "python tools/check_tenancy.py". Validacion en
  caliente de P05: "python tools/validate_p05_tenancy.py".
- Politica (P06, ADR-012/ADR-021): existe un TERCER DSN,
  CE_V5_OPERATOR_DATABASE_URL (rol ce_v5_operator: kill switch, publicacion de
  policy_version y su auditoria). JAMAS debe estar presente en un proceso de
  runtime: DbConfig.from_env lo detecta y ABORTA el arranque
  (OperatorDsnInRuntimeError). Solo la herramienta de operador
  ("python -m ce_v5.entrypoints.operator_cli") lo porta. Tambien
  CE_V5_OPERATOR_DB_PASSWORD para provisionar el rol. Utilidades de P06:
  tools/seed_p06_fake.py (escenario FALSO de demo),
  entrypoints/hot_validation_policy.py (validacion en caliente),
  tools/show_p06_audit.py (volcado de auditorias).
- Remoto: github.com/alvarbonavista-del/CE_V5 (PRIVADO). Desde T-01 existe
  copia del repositorio fuera del disco de Alvaro. Empujar es obligatorio al
  cerrar cada pieza.
- CI real: GitHub Actions ejecuta ci.yml en cada push. Tres jobs (backend,
  backend-integration con PostgreSQL y Redis, frontend). VERDE desde 64330c7.
  ACTIONS VERDE 3/3 sobre el commit final de P06b (52b26db). La formula vieja
  ("Actions pendiente por ausencia de remoto") quedo DEROGADA en T-01 (regla
  5.13).
- API y auth (P06b, ADR-002/019): la API es un PROCESO PROPIO y se arranca con
  "python -m ce_v5.entrypoints.api". Variables de entorno nuevas:
    CE_V5_JWT_SECRET ............. FIRMA los tokens de acceso. Minimo 32 chars.
                                   Si falta o es corto, la aplicacion NO ARRANCA.
    CE_V5_RATE_LIMIT_SECRET ...... calcula las HUELLAS de email e IP usadas como
                                   claves en Redis (el almacen nunca guarda emails
                                   ni IPs en claro). Minimo 32 chars.
    CE_V5_ENV .................... entorno de ejecucion (development/production).
    CE_V5_TRUSTED_PROXY_COUNT .... cuantos proxies PROPIOS hay delante. 0 = se
                                   IGNORA X-Forwarded-For (valor SEGURO).
    CE_V5_CORS_ALLOWED_ORIGINS ... origenes admitidos, separados por coma. VACIO
                                   por defecto; un "*" esta PROHIBIDO y la
                                   aplicacion NO ARRANCA con el.
    CE_V5_COOKIE_SECURE .......... cookies solo por HTTPS. En produccion, false
                                   impide el arranque.
    CE_V5_MAX_BODY_BYTES ......... tamano maximo de cuerpo, rechazado ANTES de
                                   leerlo.
    CE_V5_API_HOST / CE_V5_API_PORT  escucha de la API.
- Validacion en caliente de P06b: UN SOLO COMANDO,
  "python tools/run_p06b_hot_validation.py" (levanta lo que necesita y ejecuta el
  arnes completo).
- AVISO IMPORTANTE (regla 5.18): la suite solo corre ENTERA si
  CE_V5_OPERATOR_DATABASE_URL esta en el entorno. SIN esa variable se SALTAN 21
  tests de integracion en silencio. Un test que se salta en silencio es un test que
  no existe: el barrido de cierre debe reportar SIEMPRE el numero de skips, y CERO
  es el valor por defecto.
- Worker de ingesta (P07): proceso propio,
  "python -m ce_v5.entrypoints.worker_ingestion". El datasource se elige por
  CE_V5_MARKET_DATASOURCE ('binance' = connector REAL por defecto; 'fake' = arranque
  local SIN red, para ver que el proceso levanta).
- Rol de ingesta (P07, regla 5.20): CE_V5_INGESTION_DATABASE_URL (rol
  ce_v5_ingestion, UNICO que escribe market data) y CE_V5_INGESTION_DB_PASSWORD (para
  provisionar el rol). Guardias de arranque BIDIRECCIONALES: la API aborta si porta el
  DSN de ingesta (podria fabricar velas); el ingestor aborta si porta el DSN de app u
  operador (portaria un poder que su funcion no necesita).
- Checks activos tras P07: 7.1-7.9 + audit + identity + MARKET (NUEVO,
  tools/check_market_access.py; enganchado al job backend-integration: ingesta
  estrecha y ventanilla ciega, regla 5.20) + registro event_type->payload +
  integracion DB/bus/API. Migracion 0012 (market_candle, market_instrument,
  market_subscription_intent, ventanilla market_public_demand, rol ce_v5_ingestion).
- Validacion en caliente de P07: tools/validate_p07_hot.py (las cuatro obligatorias
  del Roadmap contra PostgreSQL real) y tools/validate_p07_binance_live.py (Binance
  REAL: streaming, reconexion + bootstrap AUTONOMO del motor, dedup).
  tools/probe_binance_live.py (sonda REST de alcanzabilidad, previa al arnes). CI
  HERMETICO: las herramientas que tocan la red viven en tools/ (pytest no las
  recolecta); ningun test de CI abre un socket.
- AVISO OPERATIVO (P07, regla 5.18): para correr la suite ENTERA en local hacen falta
  los CUATRO DSN (app, migraciones, operador, ingesta). Las variables de operador e
  ingesta son SOLO para la suite; NO se dejan permanentes en el entorno (los guardias
  de arranque abortarian la API o el worker si portan un DSN ajeno).
- Conectores de market data (T-03): OKX Spot y Bybit v5 Spot anadidos como ADAPTADORES DE INFRA en infra/connectors/okx/ y infra/connectors/bybit/, detras del puerto MarketDataSourcePort, SIN manifest (no son Componentes). Feed publico, sin credenciales. Seleccionables por CE_V5_MARKET_DATASOURCE ('binance'|'okx'|'bybit'|'fake') via el ConnectorRegistry (T-03-A).
- T-03 sustituyo el if-chain de seleccion de conector del composition root por un ConnectorRegistry minimo por convencion (register/resolve, fail-loud). Anadir un exchange = su carpeta + una linea plana de registro. VEREDICTO CE-14: SE CUMPLE (nucleo de P07 intacto).
- Commits de T-03 (Actions VERDE 3/3): registro T-03-A f1024ba (run #12); OKX 1daa784 + fix formato 8fdf15f (run #14); Bybit 2061f89 (run #15). CI: Actions verde.
- Herramientas de validacion en caliente por exchange (tools/, no en CI): probe_okx_live.py, validate_okx_live.py, probe_bybit_live.py, validate_bybit_live.py. Barridos 5.15: docs/BARRIDO_SEGURIDAD_T03_OKX.md y ..._BYBIT.md.
Hash del commit de cierre de contexto "docs(contexto): cierre T-03": ee40df620b28f8549cc396ea5cab4c733ef3850d. (Regla 5.9: un commit no puede contener su propio hash; se registra en este commit inmediato posterior.)
- Worker de reglas (P08): proceso propio, "python -m ce_v5.entrypoints.worker_rules". Es
  el UNICO que escribe rule_lifecycle_state; NO escribe la autoria (rule_definition, que
  es de ce_v5_app) y NO ingiere market data.
- Rol de reglas (P08, regla 5.20): CE_V5_RULES_DATABASE_URL (rol ce_v5_rules) y
  CE_V5_RULES_DB_PASSWORD (para provisionarlo). Guardias de arranque BIDIRECCIONALES,
  como las de ingesta: el worker de reglas ABORTA si porta el DSN de aplicacion o el de
  ingesta. Su acceso a la autoria es SOLO la ventanilla SECURITY DEFINER
  rules_for_market; de mercado solo tiene SELECT sobre market_candle (migracion 0016).
- Checks activos tras P08: 7.1-7.9 + audit + identity + market + RULES (NUEVO,
  tools/check_rules_access.py; enganchado al job backend-integration por la regla 5.22:
  motor estrecho y ventanilla cross-tenant, CA-P08-02/03/07) + registro
  event_type->payload + guardarrail 5.21 (tools/check_envelope_base_usage.py) + paridad
  de artefactos de contrato (tools/check_contract_artifacts.py) + integracion DB/bus/API.
  Migraciones 0013 (rule_definition, rule_lifecycle_state, ventanilla rules_for_market,
  rol ce_v5_rules, outbox acotada), 0014 (estado operacional), 0015 (ce_v5_app lee el
  estado de su tenant) y 0016 (ce_v5_rules lee market_candle y nada mas).
- AVISO OPERATIVO (P08, regla 5.18): para correr la suite ENTERA en local hacen falta
  ahora los CINCO DSN (app, migraciones, operador, ingesta y REGLAS). Si hay base de
  datos y falta el de reglas, sus tests FALLAN EN ALTO; no se saltan.
- Validacion en caliente de P08 (tools/, no en CI): validate_rules_hot.py,
  validate_rules_cycle.py, validate_rules_intents.py, validate_rules_worker.py y
  validate_rules_correction.py. Exigen CE_V5_DATABASE_URL, CE_V5_RULES_DATABASE_URL y
  CE_V5_MIGRATIONS_DATABASE_URL.
- REGLA 5.22 (nace en el cierre de P08): un check bloqueante que existe pero no esta
  enganchado en ci.yml es un check que NO existe. El DoD de cierre debe verificar el
  enganche y demostrar el verde en Actions, no solo en el barrido local.
