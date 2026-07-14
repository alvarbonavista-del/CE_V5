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
5.13 Desde T-01, el barrido local NO sustituye a GitHub Actions. Una pieza no
     se cierra hasta que Actions esta en VERDE sobre el commit de la pieza.
     La formula "Actions pendiente por ausencia de remoto" (5.6) queda
     derogada. Los cierres historicos que la contienen NO se reescriben: eran
     ciertos cuando se escribieron.
5.14 CORRECCION DE UNA MIGRACION PRE-COMMIT. Una migracion puede corregirse EN
     SITIO solo si se cumplen TODAS estas condiciones: (a) NO esta commiteada (no
     existe en la historia de git); (b) la pieza NO esta entregada; (c) solo existe
     en una base local desechable; (d) ningun otro entorno la ha aplicado y el CI
     recrea la base desde cero; (e) la base local se RECREA desde cero, de modo que
     el guardia de checksum NUNCA ve una discrepancia y JAMAS se silencia ni se
     parchea. Si falla CUALQUIERA de las cinco, se crea una migracion SUCESORA, sin
     excepcion. Desde el instante en que una migracion se COMMITEA, es HISTORIA: no
     se edita nunca. Precedentes: CA-05 (0007) y CA-09 (0010).
5.15 BARRIDO DE LINEA BASE DE SEGURIDAD POR SUPERFICIE NUEVA. Toda pieza que
     ABRA UNA SUPERFICIE EXTERNA NUEVA (internet, exchange, wallet, proveedor de
     terceros, dispositivo) incluye en su DoD un BARRIDO EXPLICITO de la linea
     base de seguridad de esa superficie. Para CADA control: se construye ahora,
     o se registra con PIEZA DUENA y justificacion bajo la regla 5.11. No se
     cierra la pieza sin ese barrido escrito. Motivo: el ROADMAP enumero
     capacidades, no lineas base de seguridad; los huecos aparecieron uno a uno
     (T-01, CA-10) y eso no escala. Afecta como minimo a P06b (internet), P07
     (exchanges), P10a (credenciales de terceros) y P10b (dinero real).
5.16 CENTRAL FIJA EL INVARIANTE, NO EL RECIPIENTE. Cuando Central ordene que un
     hecho quede REGISTRADO, no designara un artefacto concreto (tabla, enum,
     registro) sin haber verificado su esquema y su vocabulario reales. Por
     defecto, Central expresara el REQUISITO como INVARIANTE (append-only, por
     sujeto, no editable por el auditado, con motivo veraz) y sera el periferico
     quien elija o adapte el recipiente, ELEVANDO si ninguno encaja. Motivo: dos
     correcciones de Central (CA-04 p.5 y CA-10 c.1) prescribieron destino sin
     leer el molde, y ambas hubo que enmendarlas (CA-05, CA-11).
5.17 EL COMMIT NO ES LA ENTREGA. El commit de pieza se hace ANTES de la firma,
     porque la regla 5.13 exige Actions en verde y Actions no puede correr sin un
     commit empujado. La FIRMA de Alvaro no gatea el commit: gatea la TANDA DE
     CIERRE y el estado ENTREGADA. Orden correcto: commit de pieza -> push ->
     Actions verde -> revision Central -> revision CSA -> firma de Alvaro ->
     tanda de cierre -> commit de contexto -> arbol limpio. Cualquier cambio que
     pida la doble revision entra como commits adicionales ANTES de la tanda.
     Precisa 5.7 y 5.13.
5.18 CERO SKIPS, O SKIPS DECLARADOS. Un test que se salta en silencio es un test
     que no existe. El barrido de cierre DEBE reportar el numero de tests
     saltados. CERO es el valor por defecto. Todo skip se declara EXPLICITAMENTE
     en el informe con motivo, condicion de salida y dueno; skip sin motivo =
     fallo de suite. Si Actions ejecuta mas que el barrido local, el informe debe
     decirlo. Origen: 21 tests de integracion nunca ejecutados en local y DOS
     rotos, salvados solo por Actions (T-01).
5.19 TABLAS CON SECRETOS: VENTANILLAS ESTRECHAS. Toda tabla que contenga SECRETOS
     (hashes de contrasena, tokens, credenciales de terceros, claves de API,
     material criptografico) sigue el patron firmado en CA-07: el rol de
     aplicacion NO tiene privilegios directos de tabla; el acceso va por funciones
     SECURITY DEFINER minimas (search_path fijo, sin SQL dinamico, sin comodines,
     retorno minimo, EXECUTE revocado a PUBLIC, convencion p_/v_), y un CHECK
     BLOQUEANTE lo hace cumplir. Cero logica de negocio en la DB. VINCULANTE para
     P10a (credenciales BYOC de exchange).
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
=====================================================================
14. T-01: REMOTO, COPIA DE SEGURIDAD Y VERIFICACION REAL DE CI
=====================================================================
Trabajo FUERA DEL ROADMAP, ordenado por Alvaro tras el cierre de P06.
MOTIVO: deuda prohibida SIN PIEZA DUENA (regla 5.11). Dos hechos, arrastrados
SIETE PIEZAS desde M0: (1) el proyecto no tenia NINGUNA copia fuera del disco de
Alvaro; (2) el fichero .github/workflows/ci.yml NUNCA se habia ejecutado. Los
commits daban historia, pero en el MISMO disco: un fallo de hardware se habria
llevado el codigo y su historia a la vez. Y el workflow era un plan sin ensayar.
No existia decision escrita que respaldase seguir asi; era un hueco de proceso.

FASE 1 - AUDITORIA DE SECRETOS DEL HISTORIAL COMPLETO (bloqueante, superada)
Motivo: el historial de git es PARA SIEMPRE. Un secreto commiteado y borrado
despues SIGUE en el historial y queda comprometido en cuanto se empuja.
Herramienta: gitleaks v8.30.1 (version verificada), sobre TODOS los commits
(--log-opts=--all), mas revision manual.
RESULTADO: LIMPIO. 23 commits escaneados, "no leaks found". Ningun fichero .env,
.pem, .key, secret ni credential ha existido JAMAS en el historial (verificado con
git log --diff-filter=A sobre todo el arbol). .gitignore cubre .env. El unico
fichero de entorno versionado es la plantilla .env.example, con valores CAMBIAME.
Anotacion (no es fuga): .env.example contiene el DSN de migraciones con
credenciales del contenedor Docker LOCAL (ce_v5:ce_v5@localhost), servicio
desechable en la maquina de Alvaro, sin exposicion.

FASE 2 - REMOTO PRIVADO Y COPIA DE SEGURIDAD
Repositorio PRIVADO github.com/alvarbonavista-del/CE_V5. Privado por norma: es
codigo propietario y contiene el diseno de seguridad (kill switch, RLS, roles de
DB). Empujado TODO el historial (23 commits, 548 objetos). Verificado que el HEAD
del remoto coincide con el local (d57b8d32e47e068ed6c4a7427d5b17ef4a1eff28).
DESDE ESTE MOMENTO EXISTE COPIA DEL PROYECTO FUERA DEL DISCO DE ALVARO.

FASE 3 - PRIMER ESTRENO DE GITHUB ACTIONS (sin maquillar)
El ci.yml se ejecuto por PRIMERA VEZ en la vida del proyecto, sobre el commit
d57b8d3. RESULTADO: ROJO. Estaba roto.
- Backend (lint, format, types, fronteras, tests): VERDE a la primera.
- Backend integration (PostgreSQL 18.4 + Redis + tenancy + RLS): VERDE a la
  primera. El sustrato completo de siete piezas funciona en una maquina limpia.
- Frontend: ROJO. Causa: ERR_PNPM_BAD_PM_VERSION. La version de pnpm estaba
  declarada DOS VECES (la clave "version: 11" del workflow y "packageManager":
  "pnpm@11.10.0" del package.json). pnpm aborta ante la doble declaracion.
  FIX (commit fff7788): se retira la clave "version" del workflow; package.json
  queda como UNICA fuente de verdad. Actions VERDE en los tres jobs.
- Warnings de deprecacion (no errores): cuatro actions corrian sobre Node 20, que
  GitHub va a retirar; el CI se habria roto solo, sin tocar el repo. Se adelanto el
  cambio (commit 64330c7) con versiones VERIFICADAS contra sus paginas de releases:
  actions/checkout v4 -> v6; actions/setup-node v4 -> v6; pnpm/action-setup v4 ->
  v6; astral-sh/setup-uv v5 -> v8.1.0.
  REGLA DE SEGURIDAD: setup-uv se fija con VERSION EXACTA porque desde su v8.0.0 el
  proyecto DEJO DE PUBLICAR etiquetas moviles, a proposito: una etiqueta movil
  comprometida ejecutaria codigo ajeno en el CI sin que nadie cambie nada en el
  repositorio (ataque de cadena de suministro tipo tj-actions).
RESULTADO FINAL: Actions VERDE en los TRES jobs sobre el commit 64330c7, sin un
solo warning. Total: dos fixes, ninguno en codigo de producto.

CONCLUSION HONESTA: el ci.yml estaba roto y nadie lo sabia. El backend no. La
sospecha de que el estreno saldria rojo se cumplio, y se cumplio en el sitio menos
peligroso. Descubrirlo ahora, y no en M5 con dinero real, es exactamente el motivo
de T-01.

REGLA NUEVA: 5.13 (ver seccion 5). El barrido local NO sustituye a Actions.
=====================================================================
15. CIERRE DE PIEZA P06b - API/AUTH/REALTIME GATEWAY (LA PUERTA PUBLICA)
=====================================================================
Estado: ENTREGADA. CIERRA EL HITO M2 (4 de 4).
Commit de pieza: 6864c2af23dbaca1b04f41a0cfff3c0323247223
  ("feat(p06b): API/Auth/Realtime Gateway").
Commit final: 52b26dba7e291611bfa6c050a6cba657fad477b9
  ("fix(p06b): la limpieza de tests dejaba tenants huerfanos", PASO 0 del cierre).
ACTIONS VERDE 3/3 (backend, backend-integration, frontend) sobre el commit FINAL.
598 tests en verde con CERO SKIPS (regla 5.18).
Doble revision Central + CSA conforme; firmado por Alvaro. Fecha: 2026-07-14.

NOTA DE REGISTRO (hueco detectado y CERRADO; se deja escrito en vez de disimularlo).
Las reglas 5.14, 5.15 y 5.16 que esta seccion referencia se DICTARON en los
dictamenes de CA-09, CA-10 y CA-11 respectivamente. El PERIFERICO LAS OMITIO en la
primera tanda de cierre de P06b: no llegaron a la seccion 5 de este archivo, que
saltaba de 5.13 a 5.17 dejando tres referencias colgantes. Claude Code lo DETECTO al
no encontrarlas en disco (ni en docs/, ni en el historial de git) y se NEGO A
REDACTARLAS DE MEMORIA: una norma inventada por el periferico es peor que una norma
ausente, porque se lee como acordada sin haberlo sido. Alvaro dicto su texto y las
tres se anadieron VERBATIM a la seccion 5 en un commit posterior
("docs(contexto): reglas 5.14, 5.15 y 5.16 (omitidas en el cierre)").
Es el MISMO fallo que el cierre de P04 registro sobre la regla 5.11 ("no estaba en
disco; no se anadio en el cierre de M1"): las normas que solo viven en el chat se
pierden. Queda escrito para que la reincidencia sea visible.

LAS SEIS CONSULTAS ARQUITECTONICAS ELEVADAS Y FIRMADAS
- CA-07: VENTANILLAS DE IDENTIDAD (SECURITY DEFINER). Existio porque el canon de
  identidad no se puede proteger solo con RLS: el LOGIN busca por email ANTES de
  que exista identidad alguna (no hay sesion, no hay usuario, no hay tenant), y una
  policy RLS atada al tenant devolveria CERO FILAS, con lo que nadie podria entrar
  jamas. Solucion firmada: el rol de aplicacion NO tiene privilegios de tabla sobre
  app_user/user_credential/user_session; el acceso va por funciones SECURITY
  DEFINER minimas. Origen de la regla 5.19.
- CA-08: ubicacion de los contratos de la API en contracts/source/api.
- CA-09: correccion PRE-COMMIT del defecto de auth_rotate_session (ver DEFECTOS,
  n.1) + regla 5.14 + convencion de nombres p_/v_/out_ que hace la colision entre
  parametro y columna ESTRUCTURALMENTE IMPOSIBLE, no solo "corregida".
- CA-10: LINEA BASE DE SEGURIDAD de la puerta publica (rate limiting, CSRF, CORS,
  cabeceras, limites, logs sin secretos, guardias de arranque) + regla 5.15.
- CA-11: discriminador audit_kind en la auditoria + regla 5.16.
- CA-12: latest_offset en el cursor del realtime (ver DEFECTOS, n.3).

DECISIONES DE CONSTRUCCION D1-D7 (con su motivo)
- D1. AUTH PROPIA, no proveedor externo. CONSECUENCIA DECLARADA: los puertos
  OAuth/PKCE y el user-agent externo de ADR-019 NO se implementan en v5.0. NO ES
  UNA DESVIACION: ADR-019 los fija PARA EL CASO OAUTH, y no hay OAuth. Se
  implementaran SOLO si llega el login social, que sera decision y pieza aparte.
  Sin stubs "por si acaso" (prohibido por 5.11).
- D2. El JWT NO LLEVA EL TENANT. Si lo llevara, una pertenencia REVOCADA seguiria
  concediendo acceso hasta que caducara el pase: el tenant se resuelve en el
  backend en cada peticion, contra la pertenencia viva.
- D3. La IP sale de la CONEXION, no de X-Forwarded-For (salvo proxies PROPIOS
  declarados explicitamente; por defecto CERO). Confiar en esa cabecera permitiria
  FINGIR OTRO PAIS y burlar el geo-bloqueo, que es justo el control que M5 usara
  para no ejecutar donde la regulacion lo prohibe.
- D4. El refresh token se guarda HASHEADO con SHA-256, no con Argon2. Son 32 bytes
  ALEATORIOS, no una contrasena adivinable: no hay diccionario que atacar. Un hash
  lento aqui seria un AUTOATAQUE DE DoS en cada refresh, que ocurre constantemente.
  (Las CONTRASENAS si van con Argon2id: esas si son adivinables.)
- D5. El consumidor de policy.* usa CURSOR PRIVADO, no consumer group. Un kill
  switch debe llegar a TODAS las instancias; un consumer group lo REPARTIRIA entre
  ellas y solo una se enteraria: el resto seguiria concediendo la capability.
- D6. El ALTA es ATOMICA: usuario + credencial + tenant + pertenencia + evento de
  outbox, todo en UNA transaccion. Si no lo fuera, un fallo a medias dejaria un
  usuario sin tenant, es decir, un usuario que no puede entrar.
- D7. user.registered NO LLEVA EL EMAIL. Un evento acaba en logs, en replays y en
  procesos que hoy no existen: el dato personal no se difunde por el bus.

LINEA BASE DE SEGURIDAD A-N (CA-10) Y SUS 16 PRUEBAS
Catorce controles (A-N), verificados con 16 pruebas: contrasenas con Argon2id;
tokens de acceso cortos y firmados; refresh rotatorio con DETECCION DE REUSO
(reusar un refresh ya rotado invalida la cadena); cookies HttpOnly/Secure/SameSite
(el token JAMAS accesible al JS); CSRF; CORS sin comodin (un "*" impide el
arranque); cabeceras de seguridad; limite de cuerpo rechazado ANTES de leerlo;
rate limiting por email y por IP con huellas (el almacen no guarda emails ni IPs en
claro); logs sin secretos; guardias de arranque (secreto corto o ausente -> NO
ARRANCA; DSN de operador en runtime -> NO ARRANCA); enforcement fail-closed en el
borde realtime; identidad solo desde sesion verificada; y ventanillas estrechas
sobre las tablas de secretos. Veredicto: las 16 pruebas SUPERADAS.
NOTA HONESTA DE LA PRUEBA 13 (comparacion en tiempo constante): la suite NO
CERTIFICA IGUALDAD TEMPORAL ESTADISTICA. Certifica el USO DE LA PRIMITIVA CONSTANTE
(hmac.compare_digest). Una medicion fiable de tiempos exige muchisimas repeticiones
y una maquina sin ruido; en CI daria FALSOS ROJOS constantes. Se dice, no se
disimula: la prueba verifica el CONTROL, no la propiedad fisica.

LOS NO CONSTRUIDOS, CADA UNO CON DUENO O CONDICION DISPARADORA (regla 5.11)
- El REGISTRO REVELA EXISTENCIA con un 409: al intentar registrar un email ya
  existente, la respuesta permite deducir que esa cuenta existe. DUENO: P09a.
  Cerrarlo exige verificacion por email, que exige el router de notificaciones, que
  ES P09a. Va junto con el password reset (misma dependencia).
- Contador GLOBAL de rate limit: DESCARTADO CON MOTIVO, no diferido. Seria una
  PALANCA DE DoS DE PLATAFORMA: un atacante barato lo dispara y deja fuera a TODOS
  los usuarios legitimos a la vez. El limite es por email y por IP a proposito.
- Contador de conexiones WS COMPARTIDO entre replicas: no es una pieza, es una
  CONDICION DISPARADORA. Hoy el contador es por proceso, lo cual es correcto con una
  sola replica. PRERREQUISITO DURO antes de CUALQUIER despliegue multi-replica (ver
  T-02).
- require_capability en el primer endpoint SENSIBLE: VINCULANTE para P10a/P10b. Las
  cinco capacidades sensibles (connect_broker, execute_order, activate_autotrade,
  manual_order, manage_api_key) son SUYAS; hoy no existe ningun endpoint sensible al
  que ponerselo, y construirlo seria "por si acaso".
- plan y role en PolicyInputs: hoy None, lo que DENIEGA lo sensible (fail-closed
  correcto). Los rellenan P11 y la via v5.1.
- Proveedores reales de geo/KYC/VPN: SELECCION COMERCIAL DE ALVARO, no decision de
  ingenieria.

DEFECTOS HALLADOS EN P06b (sin maquillar)
1. auth_rotate_session era AMBIGUA y LA ROTACION DE SESION NO FUNCIONABA: un
   parametro colisionaba con una columna del mismo nombre. La cazo PostgreSQL REAL
   en un test de integracion; NINGUN MOCK PUEDE VALIDAR SEMANTICA DE PL/pgSQL.
   Corregida PRE-COMMIT (CA-09) y, mas importante, la CATEGORIA ENTERA de defecto se
   elimino con la convencion de nombres p_/v_/out_, que el check verifica.
2. EL PROCESO REAL NO PODIA SERVIR WEBSOCKETS: faltaba la dependencia que Uvicorn
   necesita para el protocolo. 577 TESTS EN VERDE Y EL PRODUCTO ROTO, porque el
   TestClient de Starlette NO PASA POR UVICORN: los tests probaban la aplicacion,
   no el servidor. Lo cazo LA VALIDACION EN CALIENTE. Es la razon EXACTA por la que
   el ROADMAP la declara NO REBAJABLE.
3. El cursor del realtime entregaba HISTORIA RANCIA COMO NUEVA, EN SILENCIO, en
   cuanto el topic pasaba de 100 mensajes (CA-12). Corregido con latest_offset y
   demostrado con un test que se pone ROJO si se restaura el apano.
4. 21 TESTS DE INTEGRACION NUNCA SE HABIAN EJECUTADO en local (se saltaban en
   silencio por falta del DSN de operador) y DOS estaban ROTOS. Solo Actions los
   habria cazado. ORIGEN DE LA REGLA 5.18.
5. La limpieza de tests dejaba +71 TENANTS HUERFANOS POR EJECUCION (695 -> 766 ->
   837, incremento DETERMINISTA medido con el rol de migraciones, con CERO usuarios
   y CERO pertenencias en la base: la firma exacta de la fuga). La fixture autouse
   borraba app_user (cuya cascada arrastra credenciales, sesiones y pertenencias,
   0005/0010) pero NO el tenant, que policy_entitlement, policy_override y
   sensitive_action_audit referencian SIN CASCADA (0007). Lo creaban los FIXTURES
   VERSIONADOS del repo, NO la base local desechable: por eso se corrigio EN LA
   PIEZA (PASO 0 del cierre, commit 52b26db) en vez de diferirlo. Asignarselo a otra
   pieza habria sido DEUDA FALSA (5.11). El borrado va en el orden que exige el
   esquema, con el rol de MIGRACIONES sobre una base de JUGUETE; los roles de
   RUNTIME siguen SIN PODER borrar auditoria (se lo prohibe el motor y el check
   "audit" lo verifica en cada build): esa garantia NO SE TOCA.
=====================================================================
16. CIERRE DE HITO M2 - SUSTRATO DE PLATAFORMA
=====================================================================
Estado: CERRADO. Doble revision (Central + CSA) conforme; firmado por Alvaro.
Fecha: 2026-07-14.
Piezas: P04 (raiz Componente/manifest/discovery/lifecycle), P05 (tenancy + RLS),
P06 (PolicyEvaluator + kill switch), P06b (API/auth/realtime gateway).

DEMOSTRACION DE LA DEFINICION DE M2: un Componente se descubre POR CARPETA (copiar
carpeta + reiniciar, CE-14), opera AISLADO por tenant con RLS fail-closed, sus
capacidades pasan por el GATE FAIL-CLOSED (DENY > ALLOW en sensibles, entitlement
explicito obligatorio), la API/auth/realtime esta EN PIE como puerta publica, y el
kill switch CORTA EN CALIENTE.

LA PRUEBA DEL HITO. El operador activa un kill switch desde OTRO PROCESO y con OTRA
CREDENCIAL, y la capability pasa a DENY EN EL BORDE DE LA API en 0,52 s, SIN
reiniciar nada (mismo PID) y POR EVENTO, recorriendo la cadena completa: operador ->
DB -> outbox -> bus -> invalidacion de cache -> DENY. El TTL del cache es de 60 s y
queda DESCARTADO POR DISENO DEL ARNES, que ABORTA si el corte tarda lo que dura el
TTL: LA DEMOSTRACION NO PUEDE MENTIR (si el corte se debiese a la caducidad del
cache y no al evento, la prueba FALLA en vez de aprobar). Al soltar el switch, la
capability vuelve a ALLOW en 0,52 s, tambien en caliente.

LO QUE M2 NO INCLUYE (y no se finge que incluya): ejecucion de ordenes, reglas
reales, market data real, PWA ni notificaciones. El sustrato esta en pie; encima no
hay todavia producto.

Proximo hito: M3 (datos, reglas y notificacion backend): P07 (ingesta de market
data), P08 (motor de reglas) y P09a (router de notificaciones backend).
=====================================================================
17. T-02: BASELINE DE DESPLIEGUE Y PRODUCCION (TRABAJO TRANSVERSAL)
=====================================================================
Trabajo FUERA DEL ROADMAP de piezas funcionales, registrado en el cierre de M2.

HUECO ESTRUCTURAL: el ROADMAP NO TIENE PIEZA DE DESPLIEGUE. No es un olvido de una
pieza concreta: es un hueco OPERATIVO que ninguna ficha del roadmap reclama como
suyo, y por tanto nadie lo pagaria nunca (el mismo patron que T-01).

DISPARADOR: antes de CUALQUIER entorno compartido, staging real, despliegue
multi-replica o demo externa persistente. Mientras todo corra en la maquina de
Alvaro contra bases de juguete, no aplica.

CONTENIDO MINIMO:
- Lock de aplicacion de migraciones (VIENE DE P02b, sec.8: hoy dos aplicaciones
  concurrentes podrian pisarse).
- Validacion de configuracion de PRODUCCION (secretos presentes y largos, cookies
  seguras, CORS sin comodin, sin DSN de operador en runtime).
- Contador de conexiones WS COMPARTIDO si hay mas de una replica (viene de P06b:
  PRERREQUISITO DURO del multi-replica).
- Verificacion de secretos y entorno.
- Backup/restore basico.
- Smoke test de API/WS contra el despliegue real.
- Despliegue REPRODUCIBLE con Actions.

NO MODIFICA el Roadmap de piezas funcionales: cubre un hueco OPERATIVO descubierto
en construccion. Decide Alvaro cuando abordarlo.
