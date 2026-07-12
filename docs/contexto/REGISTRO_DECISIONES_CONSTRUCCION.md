# REGISTRO DE DECISIONES DE CONSTRUCCION - Crypto Engine V5
Archivo vivo (sin logica). Registra decisiones de proceso y cambios
aprobados durante la construccion, con motivo escrito (DOC_ENTREGABLES
sec.9). Mantenido por Claude Code; Alvaro lo resube al knowledge al
cerrar cada pieza o hito. Append-only: no se borra historial.
Creado: 2026-07-08 (cierre de M0).
Actualizado: 2026-07-09 (cierre de pieza P01).
=====================================================================
1. REGLA DURA DE CONSTRUCCION PASO A PASO (aplica a TODAS las piezas)
=====================================================================
El Claude periferico NUNCA entrega la pieza completa de golpe (ni como
paquete, ni como tanda unica gigante). Se construye en MICRO-PASOS:
  1. El periferico da UN micro-paso (un fichero, un comando, una idea).
  2. Lo explica a nivel principiante (Alvaro no programa).
  3. Alvaro lo ejecuta (en su maquina o via Claude Code) y PEGA la
     salida real.
  4. Solo tras ver la salida, el periferico da el siguiente micro-paso.
Motivo: Alvaro es decisor y relay, no programa; el ritmo paso a paso
evita errores en cascada, mantiene el control en Alvaro y hace observable
cada avance. OBLIGATORIA de P01 en adelante. Un periferico puede afinar
el tamano del micro-paso, nunca saltarselo ni entregar la pieza entera
de una vez. Persistencia en disco via Claude Code, no pegando ficheros a
mano en PowerShell.
=====================================================================
2. DIFERIDOS DE P00 (se materializan en su pieza, no en P00)
=====================================================================
En P00 no se crearon ficheros cuyo OBJETO aun no existe (crearlos vacios
seria placeholder, prohibido):
- contracts/VERSIONING.md ..... P01 (reglas de evolucion, ADR-005).
- tools/gen_schemas.py ........ P01 (Pydantic source -> JSON Schema).
- tools/gen_ts_types .......... P01 (JSON Schema -> tipos TS).
- tools/check_manifests ....... P04 (validacion de manifests, 7.5).
- tools/check_orphans ......... P04 (huerfanos de componentes, 7.6).
En P00, 7.1 lo corre import-linter (contrato en pyproject) y 7.2
dependency-cruiser; 7.4 lo corre tools/check_generated.py; el type-check
del frontend es un gate de madurez (tools/check_types_frontend.mjs).
=====================================================================
3. TAREAS DE ENTRADA HEREDADAS (diferidos como tareas de la pieza)
=====================================================================
P01 (Contratos base y envelope):
  [ ] tools/gen_schemas.py (source Pydantic -> contracts/schemas).
  [ ] tools/gen_ts_types (schemas -> frontend generated).
  [ ] contracts/VERSIONING.md (reglas de evolucion dual, ADR-005).
  [ ] Activar checks 7.3 y 7.7 (contratos y compatibilidad de schema).
P04 (Raiz Componente, manifest, discovery, lifecycle):
  [ ] tools/check_manifests (validacion de manifests, check 7.5).
  [ ] tools/check_orphans (huerfanos, check 7.6).
=====================================================================
4. ENTORNO Y ARREGLOS ACOTADOS REGISTRADOS EN M0
=====================================================================
- Windows local: import-linter (via rich) exige consola UTF-8; fijados
  PYTHONUTF8=1 y PYTHONIOENCODING=utf-8 persistentes para el usuario. Fin
  de linea del repo forzado a LF via .gitattributes (* text=auto eol=lf).
- Arreglos acotados en la validacion de P00 (sin deuda, sin tocar logica):
  quitar BOM de 19 ficheros (Set-Content de PowerShell), ruff format en 2,
  normalizar biome.json a LF.
- Soporte anadido en P00 (no arquitectura): pnpm-workspace.yaml,
  .python-version, .gitattributes, tools/check_types_frontend.mjs.
Ninguna de estas decisiones reabre un ADR.
=====================================================================
5. REGLAS DE PROCESO ANADIDAS DURANTE M1 (dictadas por Alvaro)
=====================================================================
Complementan (no sustituyen) a DOC_ENTREGABLES. Se registran aqui para
que no vivan solo en el chat (anti-deriva, DOC_ENTREGABLES sec.9).
5.1 Agrupacion de micro-pasos: por velocidad se permite agrupar varios
    micro-pasos afines en UNA sola tanda [CLAUDE CODE] cuando son
    escritura en disco, explicando antes a Alvaro que hace el grupo y sin
    mezclar validacion en caliente intermedia. Refina la regla 1, no la
    anula.
5.2 Prioridad [CLAUDE CODE] sobre [POWERSHELL]: escribir/editar ficheros
    siempre por Claude Code (mas rapido; evita el BOM de Set-Content).
    PowerShell solo para instalar dependencias, ejecutar checks/tests,
    commit y ver la salida real.
5.3 Un periferico por pieza (no por hito): arranca leyendo el estado en
    disco/knowledge, no la memoria de un chat anterior. Excepcion a
    criterio de Central: dos piezas muy pequenas y muy acopladas.
5.4 Central define QUE debe contener cada tanda; el periferico la REDACTA.
5.5 Procedimiento estandar de cierre de pieza: (a) el periferico produce
    informe de entrega; (b) Central prepara el dossier para el CSA; (c) el
    CSA dictamina; (d) Alvaro firma; (e) el periferico monta la tanda de
    cierre que actualiza los CUATRO archivos de contexto
    (ESTADO_CONSTRUCCION_V5, REGISTRO_HITOS_V5,
    REGISTRO_DECISIONES_CONSTRUCCION, CHATGPT_CSA_CONTEXTO_CONSTRUCCION)
    mas commit + barrido limpio + hash; (f) Alvaro resube los cuatro al
    knowledge.
5.6 Formula exacta de CI mientras no haya remoto: "checks equivalentes al
    workflow validados en local; Actions pendiente por ausencia de
    remoto". Nunca "Actions verde".
5.7 Una pieza no es ENTREGADA hasta commit + git status limpio + barrido
    limpio posterior + hash registrado.
5.8 Doble revision por pieza (Central + CSA) antes de la firma de Alvaro,
    ademas de la consolidacion en el cierre de hito.
5.9 La tanda de cierre de cada pieza termina COMMITEANDO sus propios
    cambios de contexto (docs(contexto): cierre Pxx), para no dejar cola
    en el arbol git. Origen: cola detectada en el cierre de P01.
5.10 Formato de instrucciones [POWERSHELL]: los comandos van en un bloque
     copy-paste que contiene UNICAMENTE comandos ejecutables; PROHIBIDO
     meter enunciados, explicaciones, comentarios # o numeraciones dentro
     del bloque; la explicacion va FUERA, antes del bloque; cada comando o
     grupo logico en su propio bloque. Las tandas [CLAUDE CODE] van en un
     UNICO bloque de texto plano copy-paste sin partir. Origen: friccion en
     el arranque de P02b.
5.11 Deuda tecnica (afina sec.7 y DOC_ENTREGABLES): prohibida por norma.
     Unica excepcion admitida: cuando resolverla EXIGE construir una pieza
     POSTERIOR en el roadmap (o la condicion que esa pieza introduce). Toda
     tarea futura registrada debe cumplir esto: la pieza actual queda
     completa y correcta para su DoD y sus ADR, y lo diferido depende de una
     pieza/condicion posterior; adelantarlo seria construir "por si acaso"
     (tambien prohibido). Si algo se pudiera resolver ya sin una pieza
     posterior, NO se difiere: se hace ahora.
5.12 Cuando una pieza crea un ADR nuevo, la tanda de cierre pasa de CUATRO a
     CINCO archivos: los cuatro de contexto MAS ADRS_PROPUESTOS.md en
     APPEND-ONLY (los ADR previos quedan intactos, nunca se renumeran ni se
     reescriben). El ADR se escribe tambien en docs/adr/ del repo. Alvaro
     resube los cinco al knowledge.
=====================================================================
6. CIERRE DE PIEZA P01 - CONTRATOS BASE Y ENVELOPE
=====================================================================
Estado: ENTREGADA. Commit: 17bb584
(17bb58490bb2091b8469b30503bab5c03915b7cf).
Doble revision Central + CSA conforme; firmado por Alvaro.
CI: checks equivalentes al workflow validados en local; Actions pendiente
por ausencia de remoto.
Condicion operacional del CSA cumplida: commit + git status limpio +
barrido limpio posterior + hash registrado. Tras este commit, el check
7.7 tiene baseline real en git.
Tareas de entrada de P01 (seccion 3), todas cerradas:
  [x] tools/gen_schemas.py (source Pydantic -> contracts/schemas).
  [x] tools/gen_ts_types.mjs (schemas -> frontend generated).
  [x] contracts/VERSIONING.md (reglas de evolucion dual, ADR-005).
  [x] Activar checks 7.3 y 7.7 (contratos y compatibilidad de schema).
Decisiones de construccion de P01 (dentro de area; ninguna reabre un ADR):
- D1. JSON Schema con el exportador NATIVO de Pydantic v2
  (model_json_schema): cero dependencia extra (ADR-006).
- D2. Tipos TS con json-schema-to-typescript 15.0.4: opera sobre el
  artefacto JSON Schema y respeta el flujo de tres zonas (DOC_ESTRUCTURA
  2.5), frente a pydantic-to-typescript que saltaria el schema intermedio.
  Versiones verificadas con web_search; pydantic==2.13.4.
- D3. contracts/source como raiz importable (pytest pythonpath, mypy_path,
  ruff src); paquetes envelope/ y families/ directos, sin capa intermedia,
  para no alterar el arbol de DOC_ESTRUCTURA sec.3. Plugin pydantic.mypy.
- D4. tools/check_generated.py (7.4) AMPLIADO a regenerar-y-comparar, como
  su propio comentario de P00 anticipaba; 7.3 y 7.4 quedan como una misma
  comparacion por zona (schemas y TS).
- D5. Se emite family.schema.json (+ family.ts) ademas del envelope, para
  exponer al frontend el conjunto cerrado de familias; artefacto aditivo,
  coherente con ADR-004/006.
- D6. biome excluye la carpeta generada; tsconfig la incluye a proposito
  (tsc valida que los tipos generados compilan).
Validacion en caliente (recomendada, superada): demostrado que el check
7.3/7.4 muerde (edicion manual -> FALLA; regenerar -> OK) en las dos
zonas. La deteccion del 7.7 esta probada por sus tests.
Guardarrailes activos tras P01: 7.1, 7.2, 7.3, 7.4, 7.7, lint/format/type
(backend) y biome/tsc/depcruise (frontend), todos verdes en local.
Inactivos por no existir aun su objeto: 7.5, 7.6 (P04), 7.8 (primera tabla
tenant/user), 7.9 (primer Componente).
=====================================================================
7. CIERRE DE PIEZA P02 - MODELO TEMPORAL Y CLOCK
=====================================================================
Estado: ENTREGADA. Commit (pieza): 271d677. Doble revision Central + CSA
conforme; firmado por Alvaro. CI: checks equivalentes al workflow
validados en local; Actions pendiente por ausencia de remoto.
- CA-01 (FIRMADO por Alvaro 2026-07-09): retipado de event_time,
  ingestion_time y processing_time del envelope de datetime a EpochMillis
  (int64 UTC epoch ms), cumpliendo ADR-007. Correccion pre-consumidor de
  un defecto de P01 (no habia payloads ni consumidores). ENVELOPE_VERSION
  se mantiene en 1 (un bump dejaria una v1 fantasma sin usuarios). El 7.7
  detecto el cambio en rojo (los 3 campos) y volvio a verde tras el commit
  firmado ecff426 que reestablece la baseline. time_anchor_ref se mantiene
  como referencia, no se retipa.
- Revision de D3 (Central, sin firma; registrada): 'time' es modulo
  built-in de Python; un paquete de primer nivel con ese nombre queda
  tapado e inimportable. Se adopta paquete padre 'source'
  (contracts/source/__init__.py); imports pasan a source.envelope /
  source.families / source.time; la raiz de importacion sube de
  contracts/source a contracts. La estructura de carpetas de
  DOC_ESTRUCTURA sec.3 NO cambia. D3 queda revisada, no anulada.
- Reemision: corrects_idempotency_key OBLIGATORIO en correction,
  PROHIBIDO en provisional/closed, OPCIONAL en reemission (ADR-007 solo
  fija la referencia para correction; el resto no se inventa).
- No reexportar maturity/market desde families/__init__ para evitar el
  ciclo envelope<->families (implementacion; no reabre ADR-004).
- Clock stdlib puro: Clock.now_ms() -> int; core/clock sin dependencia de
  contratos; EpochMillis valida en la frontera de contratos, no en el reloj.
- Deslinde temporal: la ASIGNACION (quien fija cada tiempo) y la HERENCIA
  de ADR-007 se enforceran en los COMPONENTES productores (P04+ manifest/
  Clock declarado; P07/P08/P09/P10 productores). P02 entrega tipo, ranura,
  Clock y regla documentada.
TAREA FUTURA registrada (aprobada por CSA; no es deuda de codigo de P02):
- 7.7 version-aware: el check actual detecta diferencias contra la baseline
  en git y NO lee envelope_version/event_schema_version. Basta en este
  estadio (sin evolucion real con consumidores). ANTES de la primera
  evolucion real de contrato con consumidores (a mas tardar P07/P08; se
  adelanta si P02b/P03 introducen consumo persistente que haga peligrosa
  una evolucion), el 7.7 debe extenderse a consciente de bump/versionado y
  reglas expand-and-contract (ADR-005). Responsable: la pieza donde ocurra
  esa primera evolucion.
=====================================================================
8. CIERRE DE PIEZA P02b - PERSISTENCIA BASE + OUTBOX TRANSACCIONAL
=====================================================================
Estado: ENTREGADA. Commit (pieza): ed3e78833ce6789d9e435876dea8ae2c094421d4.
Doble revision Central + CSA conforme; firmado por Alvaro. CI: checks
equivalentes al workflow validados en local; Actions pendiente por
ausencia de remoto.
Decisiones de construccion (dentro de area; ninguna reabre un ADR):
- Motor PostgreSQL 18.4; driver psycopg 3.3.4 (verificados con web_search,
  soporte Python 3.14). Sin ORM.
- Runner de migraciones PROPIO (aceptado por Alvaro frente a Alembic):
  forward-only, append-only, con checksum que rechaza editar una migracion
  ya aplicada (ADR-005; DOC_ENTREGABLES sec.6). Tabla schema_migrations. Sin
  down migrations: se adopta sucesor forward-only, no reescritura historica.
- Frontera DB: Session como Protocol (ports.py); psycopg_adapter.py unico
  conocedor del driver (REST-15); outbox.py depende solo de ports + stdlib;
  adapters concretos se cablearan en composition root cuando existan
  entrypoints.
- Outbox: event_id UNIQUE, idempotency_key UNIQUE (dedup de productor),
  stream_key, event_type, envelope jsonb, published_at. La DB NO valida el
  schema del envelope: la validacion contractual corresponde al productor
  antes de encolar y al publisher/bus en P03 (ADR-006). Envelope como jsonb
  opaco.
- Inbox: dedup por consumer_group/handler/idempotency_key. audit_log tecnico
  minimo.
- Tablas outbox/inbox/audit clasificadas isolation_scope=system (comentario
  SQL, no mecanismo). Sin tenant_id, sin RLS. 7.8/RLS diferido a P05; P05
  debera reconocer estas tablas como tecnicas de sistema (privilegios
  restringidos, no superficie de consulta por usuario), aunque su contenido
  incluya envelopes con scope tenant/user.
- Timestamps de infraestructura (applied_at/created_at/processed_at):
  DEFAULT now() del servidor; metadatos tecnicos, no tiempos de evento
  (ADR-007 Clock es para productores de eventos).
TAREAS FUTURAS registradas (aprobadas por CSA; no son deuda de codigo de P02b):
- Lock de aplicacion de migraciones: ANTES de entornos compartidos/prod o de
  cualquier flujo con aplicacion concurrente, el runner debe incorporar un
  lock (advisory lock de PostgreSQL o equivalente). Responsable: la pieza/
  momento donde aparezca ejecucion concurrente o el primer despliegue
  compartido.
- Cualificacion de idempotency_key: es UNIQUE global en la outbox. Al
  construir productores reales (P07/P08/P10), las formulas de clave deben
  quedar globalmente cualificadas por familia/scope/tenant/user/stream
  cuando corresponda, para evitar colisiones cross-tenant.
=====================================================================
9. CIERRE DE PIEZA P03 - SUSTRATO EVENTBUS (ABSTRACCION + ADAPTER REDIS)
=====================================================================
Estado: ENTREGADA. Commit (pieza): cb25b81e2948977dfd574d5c3aff137b8a11eed5.
Doble revision Central + CSA conforme; firmado por Alvaro. CI: checks
equivalentes al workflow validados en local; Actions pendiente por ausencia
de remoto.
Decisiones de construccion (dentro de area; ninguna reabre un ADR):
- D1. OutboxPublisher e InboxConsumer en infra/db (junto a outbox/inbox de
  P02b), broker-neutrales, dependientes solo de puertos; sin carpeta nueva.
- D2. Bus contract-agnostic: BusMessage lleva el envelope serializado opaco
  + claves de routing (stream_key, idempotency_key); la validacion de
  contrato vive en el OutboxPublisher, no en el transporte ni en la DB
  (REST-15, ADR-006). Cierra el punto de P02b: con P03 un envelope invalido
  no llega al broker.
- D3. Topic derivado de la familia del evento (event_type antes del punto,
  ADR-004).
- D4. Particionado basico por stream_key (crc32 % partitions, default 1);
  avanzado fuera de alcance (ADR-013).
- D5. Idempotencia de consumidor: INSERT ... ON CONFLICT DO NOTHING
  RETURNING en inbox; efecto + apunte en la misma transaccion; ACK solo
  tras commit.
- D6. DLQ como stream aparte con owner, reason_code, attempts,
  first_seen_at, last_seen_at, procedure; timestamps con hora del servidor
  Redis (metadato de infra, no tiempo de evento).
- D7. Tipado del borde redis-py (8.0.1 no generico): retornos como Any
  reconstruidos a tipos propios; 3 alias de tipo; cero type: ignore, cero
  deuda.
- D8. RedisBusConfig.from_env, simetrico a DbConfig.from_env, para el
  composition root.
- Empaquetado: pyproject.toml += redis==8.0.1 y packages del wheel +=
  "contracts/source" (el contrato se instala en RUNTIME, no solo en tests),
  porque P03 es el primer codigo backend en ejecucion que importa el
  contrato para validar envelopes. Completa REST-4/ADR-006; no cambia
  arquitectura.
- Versiones (web_search): Redis 8.8 imagen; redis-py 8.0.1 (Python 3.14).
TAREAS FUTURAS registradas:
- Mensaje-veneno en outbox: hoy el publisher es fail-loud (no publica, eleva
  OutboxPublishError, no avanza en silencio); un veneno persistente DETIENE
  el drenado (head-of-line). Aceptado para v5.0 (mejor parar que perder/
  duplicar). Tarea futura: cuarentena/side-lining de filas veneno de la
  outbox, con procedimiento operativo, sin romper ordering ni ocultar el
  fallo.
- 7.7 version-aware (AFINADA, actualiza la tarea de sec.7): desde el cierre
  de M1 hay consumo persistente real. Extender el 7.7 a version-aware pasa a
  ser PRERREQUISITO DURO antes de CUALQUIER evolucion futura de contrato
  (envelope, payload, event_schema_version, envelope_version, expand-and-
  contract), no solo "a mas tardar P07/P08".
=====================================================================
10. CIERRE DE HITO M1 - ESPINA DORSAL TECNICA
=====================================================================
Estado: CERRADO. Doble revision (Central + CSA) conforme; firmado por
Alvaro. Fecha: 2026-07-10.
Piezas: P01 (envelope y familias), P02 (modelo temporal y Clock), P02b
(persistencia base + outbox transaccional), P03 (EventBus + adapter Redis).
Demostracion de la definicion de M1: un evento viaja de punta a punta con
envelope, idempotencia y Clock sobre el bus externo, con outbox
transaccional; reinicio sin perdida. Evidencia end-to-end: la validacion en
caliente de P03 (outbox de P02b -> bus Redis -> consumidor idempotente ->
efecto persistido -> ACK/dedup -> reinicio de consumidor sin perder ni
duplicar). Mata el bus informal _bus(ev) de v4.
Proximo hito: M2 (sustrato plataforma): P04 (raiz Componente/manifest/
discovery/lifecycle), P05 (tenancy + RLS), P06 (PolicyEvaluator + kill
switch), P06b (API/auth/realtime).
=====================================================================
11. CIERRE DE PIEZA P04 - RAIZ COMPONENTE, MANIFEST, DISCOVERY, LIFECYCLE
=====================================================================
Estado: ENTREGADA. Commit (pieza): 866b434ec04dd3e04a9d43a9b3fa2f6f50dfd196.
Doble revision Central + CSA conforme; firmado por Alvaro. Abre el hito M2
(sustrato plataforma). CI: checks equivalentes al workflow validados en
local; Actions pendiente por ausencia de remoto.
Decisiones de construccion (dentro de area; ninguna reabre un ADR):
- D1. Vocabulario de lifecycle (LifecycleState, HealthStatus,
  ReadinessStatus, LifecycleScope) vive en contracts/source como contrato
  de los eventos component.*; el nucleo lo importa (direccion core ->
  contracts, base neutral; 7.1 KEPT) y aporta la maquina de transiciones y
  el contrato de enganches.
- D2. Familia component.*: ComponentEventType (uno por estado),
  event_type_for_state, ComponentLifecyclePayload (identidad de instancia +
  previous/new + health/readiness + reason/error_code, con validacion de
  coherencia de ambito). Primer payload concreto del sistema
  (component_lifecycle.schema.json + .ts).
- D3. Manifest type como StrEnum ComponentType (engine/worker/connector/
  notification_provider/auth_provider/exporter/ui_plugin); "abierto" = crece
  subiendo manifest_schema_version, sin texto libre (ADR-008).
- D4. capabilities genericas (kind+version+name+schema_ref+detail); P04
  valida buena forma y referencia de schema; la validacion semantica del
  detalle la hace la pieza duena de esa capability (ADR-008).
- D5. Campo entrypoint (str|None); su ausencia/inconsistencia la caza 7.6.
- D6. Discovery con loader INYECTADO e import dinamico: lee y valida el
  manifest ANTES de importar codigo; el nucleo NO adquiere dependencia
  estatica de components/* (ADR-009; 7.1 KEPT).
- D7. Manifest en JSON solo en v5.0; el YAML de ADR-009 se difiere hasta que
  un componente lo necesite (5.11).
- D8. Emision de lifecycle por el EventBus PORT (REST-15) SIN outbox, por no
  nacer de una transaccion de DB. REGLA OPERATIVA (exigida por CSA): el
  fallo de publish es FAIL-LOUD, nunca silencioso. Implementado como
  emitir-antes-de-aplicar: se publica el component.* y solo si el publish
  tiene exito se aplica el nuevo estado; si el publish falla, la excepcion
  PROPAGA y el estado local NO avanza; tests de regresion lo demuestran. Si
  en una pieza posterior el estado de componente pasa a persistirse
  transaccionalmente en DB, estos eventos se moveran al patron outbox.
- D9. Arista STOPPED -> FAILED anadida para el fallo de teardown (FAILED ya
  es estado de ADR-010; rellena arista operativa, no extiende el ADR). Las
  aristas de POLITICA (reintento desde FAILED, liberacion de QUARANTINED,
  backoff, fail-fast/quarantine por criticidad) quedan para P06.
- D10. health_status/readiness_status SEPARADOS en el contrato (ADR-010)
  pero derivados minimamente del estado en P04 (READY solo en RUNNING;
  UNHEALTHY en FAILED/QUARANTINED); el reporte rico (DEGRADED por
  dependencia opcional caida) se difiere a la resolucion de dependencias.
- D11. Discovery y los tres checks ignoran carpetas privadas/ocultas.
- D12. testpaths incluye backend/src/ce_v5/components (tests junto al
  componente; DOC_ESTRUCTURA sec.5).
- Checks activados desde P04: 7.5 (check_manifests), 7.6 (check_orphans),
  7.9 (check_component_docs); materializan los diferidos de P00.
TAREAS FUTURAS registradas (cumplen 5.11; dependen de pieza/capacidad
posterior):
- Soporte YAML de manifest: cuando un componente lo requiera.
- Health DEGRADED rico: cuando exista resolucion de dependencias/capabilities.
- Aristas de politica de lifecycle (reintento/quarantine/backoff): P06.
- Outbox de eventos de lifecycle: solo si el estado de componente se
  persiste transaccionalmente en DB.
=====================================================================
12. CIERRE DE PIEZA P05 - TENANCY SHARED-SCHEMA + RLS
=====================================================================
Estado: ENTREGADA. Commit (pieza): 795deb3. Doble revision Central + CSA
conforme; firmado por Alvaro. P05 es 2/4 de M2; no cierra el hito. CI: checks
equivalentes al workflow validados en local; Actions pendiente por ausencia de
remoto.
Decisiones de construccion (dentro de area; ninguna reabre un ADR):
- D1. Rol ce_v5_app creado SIN LOGIN por la migracion 0004 (sin secretos en el
  repo, CE-13); la credencial la provisiona el entorno; la contrasena nunca se
  interpola en SQL (parametro a set_config, aplicado con format(%L)).
- D2. La PK de tenant se llama tenant_id (no id), para que la regla "toda tabla
  tenant/user tiene tenant_id" sea literal y verificable, sin excepciones en el
  check.
- D3. user_tenant_membership SIN FK a tabla de usuarios (no existe hasta P06b) y
  SIN UNIQUE(user_id): la unicidad 1:1 de v5.0 la impone el RESOLVER
  (fail-closed ante 0 o >1 pertenencias), no el esquema, dejando abierta la
  costura de organizaciones (ADR-011).
- D4. DOBLE contexto transaccional: app.current_user_id (para que el resolver
  pueda LEER la pertenencia bajo RLS antes de conocer el tenant) y
  app.current_tenant_id (para operar tenant-scoped). La policy de lectura de
  user_tenant_membership permite leer SOLO las filas del principal autenticado;
  la escritura exige contexto de tenant.
- D5. SET LOCAL implementado con set_config(clave, valor, true): transaccion-
  local y parametrizable; valores parametrizados, claves controladas por codigo;
  ningun identificador interpolado en SQL.
- D6. Guardia de runtime: si el rol conectado tuviera SUPERUSER o BYPASSRLS, la
  aplicacion se NIEGA a operar (AppRoleError). Fail-closed ante despliegue
  incorrecto, no un aviso.
- D7. isolation_scope declarado con COMMENT ON TABLE; el enforcement lo hace el
  check 7.8; allowlist EXPLICITA de tablas sin tenant_id en
  tools/check_tenancy.py (anadir una linea es visible en el diff).
- D8. El check 7.8 lee pg_catalog/pg_policies y NUNCA information_schema (que
  oculta objetos segun privilegios y dejaria pasar una tabla sin grants); corre
  con el DSN de migraciones para visibilidad total del catalogo.
- D9. Toda la suite de integracion corre ahora bajo el rol de APLICACION
  sometido a RLS (las migraciones con el rol dueno); la limpieza pasa de
  TRUNCATE a DELETE. P02b/P03/P04 no pierden cobertura: ahora se validan bajo
  restricciones mas parecidas al runtime real.
- D10. Sin sharding, sin db-per-tenant, sin schema-per-tenant (ADR-011 los
  declara no construidos en v5.0).
OBLIGACION VINCULANTE SOBRE P06b (REGLA DURA DE SEGURIDAD):
app.current_user_id se fija EXCLUSIVAMENTE desde la sesion/JWT/auth VERIFICADA
por el backend. JAMAS desde datos controlados por el cliente: ni body, ni query
param, ni header no autenticado, ni selector de tenant, ni payload de WebSocket.
El cliente puede, como mucho, SOLICITAR un tenant activo; nunca imponer usuario
ni tenant. El backend resuelve y falla cerrado. Si esta regla se rompiera, un
cliente podria leer pertenencias ajenas y derivar tenants ajenos: caeria todo el
aislamiento de P05.
REGLA DURA DE PERSISTENCIA PARA TODA PIEZA FUTURA (desde P05):
- Semantica de DSN: CE_V5_DATABASE_URL = rol de APLICACION (sin bypass,
  sometido a RLS); CE_V5_MIGRATIONS_DATABASE_URL = rol de migraciones/dueno,
  fuera de runtime.
- Toda tabla nueva que persista datos debe: (a) declarar isolation_scope en
  COMMENT ON TABLE; (b) llevar tenant_id si el alcance es tenant, y tenant_id +
  user_id/owner si es user, o entrar en la allowlist explicita con
  justificacion; (c) activar ENABLE RLS + FORCE RLS con policy atada al contexto
  transaccional cuando proceda; (d) operar bajo TenantScopedDatabase, nunca con
  conexion cruda que salte el resolver.
- El check 7.8 rompe el build si no se cumple.
Validacion en caliente (7/7, salida real, rol de aplicacion): fuga de LECTURA
bajo A del tenant de B -> 0 filas; fuga de BORRADO -> 0 filas borradas y la fila
sigue bajo B; fuga de ESCRITURA -> rechazada por policy RLS; sin pertenencia ->
falla cerrado (TenantResolutionError); rol con bypass -> AppRoleError. Ademas se
demostro que el check 7.8 MUERDE (tabla tenant sin RLS -> FAIL; tabla sin
tenant_id fuera de allowlist -> FAIL; esquema limpio -> OK).
Check activado desde P05: 7.8 (tools/check_tenancy.py).
TAREAS FUTURAS registradas (cumplen 5.11):
- FK de user_tenant_membership.user_id al canon de usuario: P06b (migracion
  sucesora cuando exista la tabla).
- Claves de cache con tenant_id e invalidacion por rol/premium/jurisdiccion/KYC:
  P06 (ADR-012).
- Cualificacion de idempotency_key por tenant/scope (de P02b): productores
  reales P07/P08/P10.
- Aristas de politica de lifecycle (de P04): P06.
=====================================================================
13. CIERRE DE PIEZA P06 - POLICYEVALUATOR CENTRAL + KILL SWITCH (EL GATE)
=====================================================================
Estado: ENTREGADA. Commit (pieza): 06cb51ff4db3ab3943d374b339cf291e1541ec92.
Doble revision Central + CSA conforme; firmado por Alvaro. P06 es 3/4 de M2; no
cierra el hito (lo cierra P06b). CI: checks equivalentes al workflow validados en
local; Actions pendiente por ausencia de remoto.

CONSULTAS ARQUITECTONICAS ELEVADAS Y FIRMADAS
- CA-02 (opcion A): familia de evento policy.* creada por ADR-021, ejercitando la
  clausula de gobierno de ADR-004 (que queda VIGENTE e intacto). Cuatro tipos:
  kill_switch_activated, kill_switch_deactivated, version_published,
  subject_invalidated. FRONTERA DURA: policy.* = CAUSA (cambia la politica);
  component.* = CONSECUENCIA (cambia el lifecycle de una instancia). El supervisor
  emite component.quarantined con causation_id apuntando al event_id del policy.*
  que lo provoco. Un kill switch JAMAS se emite como component.*.
- CA-03 (opcion A reforzada): rol de DB ce_v5_operator ESTRECHO, unico que escribe
  kill switches. El rol de aplicacion solo LEE (un switch invisible es un switch
  inutil). GUARDIA DE ARRANQUE fail-closed: un proceso de runtime que encuentre
  CE_V5_OPERATOR_DATABASE_URL en el entorno NO ARRANCA. La separacion la hace
  cumplir el codigo, no un documento.
- CA-04 (opcion A1): el operador PONE EN VIGOR ediciones del reglamento, NO las
  redacta (la redaccion es catalogo comercial, dato de Alvaro, via migraciones).
  Motivo: la asimetria de riesgo. Un kill switch solo puede DENEGAR DE MAS;
  escribir reglas puede PERMITIR DE MAS. TRANSACCION ATOMICA: cambio de estado +
  auditoria + outbox en el MISMO commit; nunca "la DB dice bloqueado y los
  procesos no se enteran". OUTBOX DEL OPERADOR ACOTADA POR EL MOTOR: una policy
  RLS con WITH CHECK le permite encolar SOLO los cuatro policy.*; un intento de
  encolar un execution.* falso lo RECHAZA el motor (demostrado). El operador puede
  denegar de mas; jamas fabricar hechos.
- CA-05 (opcion A): operator_audit (system) es la auditoria CANONICA de la accion
  de operador; sensitive_action_audit (tenant-scoped) queda intacta como auditoria
  de seguridad POR SUJETO. Enmienda el punto 5 de CA-04, que exigia auditar un
  acto GLOBAL en una tabla TENANT-SCOPED y habria envenenado su RLS.
- CA-06: fix del defecto latente de P03 (ver ENMIENDA HISTORICA 1).

TRES AUDITORIAS SEPARADAS POR ALCANCE (regla de diseno)
- operator_audit (system): que hizo el OPERADOR a la plataforma. Append-only real.
- sensitive_action_audit (tenant/user, RLS): que le paso a un SUJETO.
- audit_log (system, P02b): traza tecnica de infraestructura.
Tres tablas, tres propositos. Mezclarlas habria envenenado la RLS de la de sujeto.

DECISIONES DE CONSTRUCCION (dentro de area; ninguna reabre un ADR)
- D1. La SENSIBILIDAD es CODIGO (lista cerrada: connect_broker, execute_order,
  activate_autotrade, manual_order, manage_api_key); el CATALOGO de capacidades es
  DATO. Motivo: si la sensibilidad fuese un dato, un UPDATE podria marcar
  execute_order como NO sensible y APAGAR EL FAIL-CLOSED sin tocar una linea de
  codigo. El candado no puede tener la llave dentro.
- D2. El kill switch es un instrumento ROMO a proposito: (scope, objetivo), y
  apaga todo lo que cae dentro. Las combinaciones finas son REGLAS de politica, no
  interruptores de emergencia. Un boton de panico con veinte parametros es un boton
  que falla cuando hace falta.
- D4. Endurecimiento del check 7.8 (ver ENMIENDA HISTORICA 2).
- D5. VPN INDETERMINADA -> DENY en capacidades sensibles. Deniega de mas a
  proposito: mejor bloquear una orden que ejecutarla sin saber de donde viene.
- D6. Una capacidad SENSIBLE exige ENTITLEMENT EXPLICITO. No se concede "porque
  ninguna regla la prohibe": es la diferencia entre el candado abierto y tener
  llave.
- D7. FAIL-LOUD ante datos de politica invalidos: el store valida al leer y lanza;
  el gate convierte CUALQUIER excepcion en DENY con auditoria. Una regla mal
  escrita DENIEGA y SE NOTA; jamas concede.
- D8. SI NO SE PUEDE AUDITAR, NO SE PERMITE. Si la escritura de auditoria falla en
  una capability sensible que la politica iba a PERMITIR, el gate DENIEGA
  (denied_audit_unavailable). Una accion sensible sin traza es una accion que el
  sistema no puede demostrar.
- D9. La UI es INFORMATIVA (cortesia: oculta/deshabilita) y NO se audita; el punto
  sensible del backend (require) es la LEY y se audita SIEMPRE, tanto el ALLOW
  como el DENY. Auditar cada refresco de pantalla inundaria la traza de ruido justo
  cuando importa. Asimetria asociada: un kill switch de exchange/connector solo
  aplica si el llamador APORTA el recurso; la UI no lo aporta, el punto sensible si.
- INVERSION DE DEPENDENCIA: el PUERTO LifecycleGate vive en core/component y el
  ADAPTADOR en core/policy, para que la dependencia fluya en un solo sentido
  (core.policy -> core.component) y no se cierre un ciclo de imports.

DEFECTO DE CACHE HALLADO POR LA VALIDACION EN CALIENTE (no por los tests)
La clave de cache no incluia las CAPACIDADES preguntadas. Un capability set
cacheado tras preguntar por ['execute_order'] se servia como respuesta valida a
['view_dashboard'], y la capability no evaluada salia NOT_APPLICABLE (o DENY
denied_not_evaluated si era sensible). Es decir: EL GATE DENEGABA CAPACIDADES QUE
LA POLITICA PERMITE. Fail-closed, pero ROTO. Ningun test lo cazo porque todos
preguntaban por la misma lista: hizo falta un sistema ENCENDIDO con dos preguntas
seguidas distintas. FIX: un digest ESTABLE (ordenado, deduplicado, determinista)
de las capacidades como componente mas de la clave, que ya incluye tenant_id,
user_id, policy_version, input_versions y evaluated_at. 5 tests de regresion que
FALLAN sin el fix. Este hallazgo es, por si solo, la justificacion de que el
Roadmap declare la validacion en caliente CRITICA y NO rebajable.

DEFERRED_EVENT_TYPES (condicion previa exigida por el CSA)
Un tipo de evento diferido se admite SOLO con entrada estructurada de siete campos
obligatorios: event_type, family, motivo, owner_piece (pieza duena concreta),
dependency_reason (que parte del payload exige esa pieza), status
(deferred_until_piece) y exit_rule (al cerrar la pieza duena, el tipo se REGISTRA
con su payload o se ELIMINA si ya no aplica). PROHIBIDO diferir a una pieza YA
CERRADA (nadie lo pagaria nunca: es deuda disfrazada) y PROHIBIDO diferir un tipo
que el codigo actual ya use (seria una mentira en el registro). El check
tools/check_event_payload_registry.py lo hace cumplir y se demostro que MUERDE
(entrada sin pieza duena -> FAIL). Sin esto, el mapa de diferidos seria un
vertedero.

ENMIENDA HISTORICA 1 (P03 / M1) - append-only, SIN MAQUILLAR
El OutboxPublisher de P03 validaba el envelope drenado contra Envelope[EventPayload]
BASE, que declara extra="forbid" y CERO campos. Consecuencia: la outbox SOLO PODIA
PUBLICAR PAYLOADS VACIOS y NO validaba NINGUN schema de payload. La afirmacion D2
de la sec.9 ("con P03 un envelope invalido no llega al broker") era cierta para el
ENVELOPE pero FALSA para el payload: la garantia era ILUSORIA.
AGRAVANTE: los DOS ficheros de test de la outbox de P03 (unitario e integracion)
usaban un event_type INEXISTENTE ('component.demo') con payload vacio. La suite de
P03 no solo no detectaba el defecto: LO CONSAGRABA. Ningun test de la pieza probo
jamas un payload real contra su schema. La doble revision de P03 (Central Y CSA) no
lo detecto porque la suite decia verde.
La validacion en caliente de P03 y la demostracion end-to-end de M1 se hicieron con
PAYLOADS VACIOS.
Las propiedades de transporte de M1 (envelope, idempotencia, Clock, bus externo,
outbox/inbox, ACK tras persistir el efecto, reinicio sin perder ni duplicar) SIGUEN
SIENDO VALIDAS: no dependen del contenido del payload. M1 NO se reabre; se matiza
con verdad.
El mismo defecto habria estallado en P07 (market.*), P08 (rule./signal./alert.*),
P09 (notification.*) y P10b (execution.*, DINERO REAL).
FIX (CA-06, firmado): registro canonico event_type -> clase de payload en
contracts/source/families/registry.py; el publisher valida contra la clase CONCRETA
y la coherencia de event_schema_version; fail-loud sin excepcion (no publica, no
marca la fila); check bloqueante nuevo. P02b, P03 y P04 siguen verdes.

ENMIENDA HISTORICA 2 (P05) - append-only, SIN MAQUILLAR
El check 7.8 entregado en P05 tenia una VIA DE FUGA: solo consultaba la allowlist
para tablas SIN tenant_id. Por tanto, una tabla CON columna tenant_id podia
autodeclararse isolation_scope=system en su COMMENT y esquivar A LA VEZ la allowlist
visible en el diff Y el requisito de RLS. P06 lo cierra (D4): TODA tabla clasificada
system debe estar en la allowlist explicita, tenga o no tenant_id. Demostrado con
tres pruebas negativas sobre la base real (tabla system con tenant_id no allowlistada
-> FAIL; tabla tenant sin RLS -> FAIL; tabla tenant sin tenant_id -> FAIL). P05 NO
se reabre; el guardarrail se corrige hacia delante.

OBLIGACION VINCULANTE SOBRE P06b (unica)
El SubjectInputsResolver debe derivar la identidad y el sujeto EXCLUSIVAMENTE de la
autenticacion backend VERIFICADA. JAMAS de datos controlados por el cliente: ni
body, ni query param, ni header no autenticado, ni payload de WebSocket. Es la misma
regla dura que P05 impuso sobre app.current_user_id. Sin esto, el gate del lifecycle
no puede evaluar sujetos.

VIA DECLARADA v5.1 (NO es obligacion de P06b)
El rol administrativo/compliance auditado (DOC_ROADMAP sec.8, herencia v5.1+) se
colocara DELANTE de la primitiva de operador cuando exista; no ampliara permisos del
runtime ni convertira ce_v5_operator en un admin general. En v5.0 el guardia es: la
CUSTODIA DE LA CREDENCIAL (solo Alvaro tiene el DSN de operador), el rol estrecho, el
runtime sin ese DSN (guardia de arranque), los privilegios y RLS, y la auditoria
append-only.

TAREA VINCULANTE SOBRE P07
Mover los tres market.* de DEFERRED_EVENT_TYPES a EVENT_PAYLOAD_REGISTRY con su
payload real (OHLCV/timeframe). El check no le dejara olvidarlo.

TAREAS PAGADAS EN P06 (venian de piezas anteriores)
- [x] Claves de cache con tenant_id e invalidacion por evento ante cambio de rol,
      premium, jurisdiccion o KYC (venia de P05, ADR-012).
- [x] Aristas de politica del lifecycle (reintento desde FAILED, liberacion de
      QUARANTINED, backoff acotado, fail-fast por criticidad), gate previo a
      INITIALIZE y kill switch -> QUARANTINED con causation_id (venian de P04).

VALIDACION EN CALIENTE CRITICA (superada; salida real)
Proceso vivo con el rol de aplicacion; operador en OTRA terminal con OTRA credencial.
TTL del cache fijado a 60 s A PROPOSITO para que la caducidad quedase DESCARTADA como
causa. Cadena demostrada: transaccion de DB -> outbox -> bus Redis -> consumidor ->
invalidacion de cache -> DENY, en ~1 segundo, en el MISMO proceso (contador de
iteracion continuo, sin reinicio), con el reason_code denied_by_kill_switch y el
kill_switch_id en la traza. La capability NO apuntada por el switch siguio en ALLOW
(precision quirurgica). Al soltar el switch, la capability volvio a ALLOW en caliente.
Extras demostrados: fail-closed ante sujeto no resoluble; la guardia CA-03 (un runtime
con el DSN de operador NO arranca); el operador NO puede encolar un execution.* falso;
y las auditorias no se pueden editar ni borrar.
