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
5.20 MENOR PRIVILEGIO POR PROCESO: NADIE FABRICA HECHOS AJENOS. Ningun proceso tiene
     privilegios de DB para ESCRIBIR HECHOS QUE NO PRODUCE. Cada proceso de runtime opera
     con un rol de DB acotado a su funcion: la API (expuesta a internet) NO puede escribir
     market data, ni ordenes, ni senales; el ingestor NO toca identidad ni ordenes; el
     worker de ejecucion NO fabrica market data. Rol sin login por migracion, credencial
     del entorno (CE-13), guardia de arranque que impide portar un DSN ajeno, y CHECK
     BLOQUEANTE con pruebas negativas en las dos direcciones. Generaliza CA-03/CA-04/CA-07
     y amplia la 5.19 (que solo cubria SECRETOS) a los PRIVILEGIOS DE FABRICAR HECHOS.
     VINCULANTE para P07, P08 y P10b.
5.21 SOBRE NO VACIO VALIDADO EN CONSTRUCCION: ningun camino de construccion puede
     producir un Envelope cuyo payload serializado este vacio o no case con el schema
     registrado de su event_type. Se hace cumplir en CONSTRUCCION (check estatico +
     test de round-trip por registro), no solo al publicar. Nace de la Enmienda
     Historica 1 de P03, reincidente en P08 (B6.5).
5.22 CHECK BLOQUEANTE ENGANCHADO Y DEMOSTRADO: un check bloqueante que existe pero NO
     esta enganchado en .github/workflows/ci.yml (y por tanto no corre sobre el commit
     de pieza) es un check que NO existe. El DoD de cierre de toda pieza debe VERIFICAR
     que cada check bloqueante de la pieza esta enganchado en CI y DEMOSTRAR que corre
     en verde sobre el commit de pieza (Actions, no solo barrido local). Misma familia
     que 5.18. Nace del cierre de P08 (check_rules_access construido pero no
     enganchado), reincidencia del patron de la Enmienda Historica 1 de P03 (verde
     ilusorio).
5.23 AGRUPACION DE MICROPASOS COMO PRINCIPIO. Los micropasos de construccion se agrupan en
tandas mayores SIEMPRE QUE SEA POSIBLE, para reducir su numero y acortar el tiempo del proceso.
Eleva la 5.1 de "agrupacion permitida" a "agrupacion por defecto". EXCEPCIONES (NO se agrupan):
(a) los pasos que el diseno obliga a que sean correlativos (uno necesita la salida del anterior);
(b) los puntos de VALIDACION EN CALIENTE obligatoria del Roadmap/DoD, que son NO REBAJABLES (una
regla de proceso no puede rebajar una validacion que el Roadmap marca obligatoria); (c) los
puntos donde agrupar un defecto lo haria caro de deshacer. Motivo: los checkpoints de validacion
en caliente son el mecanismo que ha cazado los defectos grandes del proyecto (payload vacio de
P03, cache de P06, websockets de P06b, patron de simbolo de P07, B6.5 de P08, backend de T-05);
agrupar por velocidad no debe llevarselos por delante.

5.24 PRIORIDAD DE CLAUDE CODE SOBRE POWERSHELL. Se prioriza Claude Code sobre otros metodos, en
particular sobre PowerShell, para acortar operaciones y tiempos de espera. PowerShell queda solo
para lo que Claude Code no cubra: ejecutar checks/tests, instalar dependencias, commit, y VER LA
SALIDA REAL (donde vive la validacion en caliente). Priorizar Claude Code es para escrituras y
operaciones; NO reduce el uso de PowerShell para validar viendo el output. Extiende la 5.2.

5.25 PROMPTS A CLAUDE CODE EN UN SOLO BLOQUE COPY-PASTE. Todo prompt dirigido a Claude Code se
escribe en UN SOLO bloque de texto plano, separado y con funcion copy-paste, para que Alvaro
trabaje mas deprisa. Confirma y consolida la 5.10.

5.26 PROHIBIDO CREAR ARCHIVOS EN EL CHAT DEL PERIFERICO. El periferico NO crea archivos en su
propio chat -- ni por el puente al disco ni como adjuntos -- salvo autorizacion EXPLICITA y
ESPECIFICA de Alvaro para una accion concreta. La persistencia en disco la hace Claude Code.
Regla nueva; nace de la escritura directa de ficheros en el arranque de P07b.

5.27 MARCA [CLAUDE CODE] Y PROMPT PRECISO ANTI-FIX. Antes de dirigirse a Claude Code, el
periferico escribe la etiqueta [CLAUDE CODE] delante del prompt (entre corchetes, en mayusculas
y resaltada). El prompt lleva instrucciones precisas y los comandos exactos. El objetivo es la
precision para EVITAR FIXES y reducir el numero de interacciones con Claude Code. Extiende la
regla 3 de DOC_ENTREGABLES (destino etiquetado de cada instruccion) y la 5.10.

5.28 MISMO PATRON EN ELEVACIONES E INFORMES. Las elevaciones a Central y los informes se
redactan con el mismo patron de las reglas anteriores: bloque limpio, copy-paste, ASCII-safe y
preciso.

5.29 AISLAMIENTO DE COMMIT POR TANDA (construccion en paralelo). Cuando varias piezas o tareas
construyen EN PARALELO sobre el mismo repositorio, cada commit incluye SOLO los ficheros de su
propia tanda: git add con LISTA EXPLICITA DE RUTAS, nunca git add . ni git add -A ni git commit
-a/-am (todos pueden arrastrar trabajo concurrente sin commitear de otra pieza). CADA SESION
COMMITEA SOLO SUS FICHEROS. La etiqueta del commit refleja EXACTAMENTE su contenido. Si un commit
ya empujado resulto mixto, NO se reescribe la historia (5.14): se DOCUMENTA la realidad (que
trabajos contiene) en el registro y se da a cada trabajo su trazabilidad. Nace del commit mixto
abb7324 (P07b 3a-i + T-05) durante la construccion paralela de M3; T-05 la aplico despues sin
fallo: cada sesion commiteo SOLO sus rutas (borde 422 en 5acc9e0; visor en f7890e1), y las tandas
OKX/Bybit de P07b lo mismo (295770a, 5dba7af). Regla HERMANA de la 5.30 (verde = bateria
completa): juntas fijan que cada push lleva su contenido exacto y pasa el CI entero.

5.30 VERDE = LA BATERIA COMPLETA DEL CI, NO UN SUBCONJUNTO. Antes de cada push, el periferico
corre EN LOCAL la bateria COMPLETA que corre el CI -definida por .github/workflows/ci.yml, que
es la FUENTE DE VERDAD y CRECE con el proyecto-, no un subconjunto elegido a ojo: todos los
checks Python del workflow (check_generated, check_tenancy, check_market_access,
check_rules_access, lint-imports y los que se anadan), el check de TS (check_generated_ts) y las
dos suites (unit+components, e integration con los DSN que exija). El periferico NO decide que
checks son "relevantes": si el CI lo corre, se corre en local antes del push. Un fallo de un
check bloqueante es un fallo de la tanda, aunque el codigo compile. MECANISMO: la bateria local
se invoca por UN SOLO comando espejo de ci.yml (para que "la bateria completa" no sea un
ensamblaje manual que pueda olvidar un check -el fallo que origina esta regla-); si ese comando
no existe, se crea y se mantiene. Nace del run #26 (437a1dc verde en ruff+mypy+pytest pero rojo
en check_tenancy: la tabla public_market market_trade_gap no estaba declarada).
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
=====================================================================
18. CIERRE DE PIEZA P07 - INGESTA DE MARKET DATA (HIBRIDA, ADR-014)
=====================================================================
Estado: ENTREGADA. ABRE el hito M3 (no lo cierra: M3 = P07 + P08 + P09a).
Commit de pieza: e7c92be. Commit final (cierre de huecos de la doble revision):
f62e4e0. ACTIONS VERDE 3/3 (backend, backend-integration, frontend) sobre f62e4e0
(run #9). 870 tests; cero skips en local. Doble revision Central + CSA conforme;
firmado por Alvaro 2026-07-15.

LAS SIETE CONSULTAS FIRMADAS (CA-P07-A..G)
- CA-P07-A (outbox vs directo por MADUREZ): candle_closed y candle_corrected van por
  OUTBOX en la MISMA transaccion que su persistencia (atomico, imposible divergencia);
  candle_updated va DIRECTO al bus, fail-loud, validando el contrato, y NO se persiste
  (no es historia, es una vista viva).
- CA-P07-B (menor privilegio por proceso): rol de DB ce_v5_ingestion + regla 5.20
  (dictada aqui). El ingestor es el UNICO que escribe market data; la API no la escribe.
- CA-P07-C (provisional gateado): candle_updated se emite SEGUN LA DEMANDA, con
  retencion corta, backpressure (pull con tope) y metricas observables de descartes.
- CA-P07-D (ventanilla agregada): funcion SECURITY DEFINER market_public_demand que
  agrega la demanda PUBLICA sin fuga de identidad -- devuelve SOLO la clave del stream y
  CUANTOS la piden, jamas QUIENES.
- CA-P07-E (7.7 version-aware): NO se dispara porque P07 es ADITIVO sobre los contratos
  (no evoluciona ninguno de forma incompatible); verificado empiricamente.
- CA-P07-F (tres exchanges por CAMINO B): uno REAL en P07 (Binance Spot); OKX y Bybit
  en T-03 (segundo y tercer connector, antes de P08).
- CA-P07-G (ventanilla vs R5 del 7.8): la ventanilla del dueno chocaba con R5 (toda
  policy de una tabla tenant/user ata la fila al tenant). Opcion 1: allowlist explicita
  de policies (POLICIES_SIN_TENANT_PERMITIDAS) MAS reglas nuevas R8a-d y R9 que hacen la
  excepcion MAS ESTRECHA que la regla que relaja. El 7.8 se ENDURECE, no solo se afloja;
  doce pruebas negativas leidas del CATALOGO (pg_policies), no de regex sobre .sql.

INVARIANTE HACIA P08 (registrado ahora)
Las REGLAS y SENALES se evaluan sobre market.candle_closed (determinista, reproducible),
JAMAS sobre market.candle_updated (vista viva que puede perderse). Evaluar sobre
provisional seria un CAMBIO ARQUITECTONICO A ELEVAR: rompe reproducibilidad, backtesting
y el SimulatedClock (ADR-007).

DISTINCION DE DEFENSAS (regla de diseno; NO copiar sin criterio el patron de P06b)
- IDENTIDAD (P06b) usa REVOKE TOTAL como defensa PRIMARIA: el rol de aplicacion no tiene
  NINGUN privilegio de tabla sobre app_user/user_credential/user_session; solo ventanillas
  SECURITY DEFINER; la RLS es secundaria. Motivo: la API no debe poder leer hashes ni por
  error.
- market_subscription_intent usa RLS atada a tenant/user como defensa PRIMARIA, porque el
  rol de aplicacion SI necesita escribir los intents del usuario autenticado. La ventanilla
  agregada es una EXCEPCION SECUNDARIA para el worker de ingesta, NO una sustitucion de la
  RLS. Defensas distintas para necesidades distintas.

CONECTOR REAL: Binance Spot, SOLO feed publico (sin credenciales), elegido y justificado:
campo 'x' = vela cerrada = maturity_state servido por el EXCHANGE; corte de 24 h
GARANTIZADO que ejercita la reconexion; endpoint data-stream sin datos de usuario; limites
publicados verificados (barrido 5.15). Coinbase: descartada por decision de producto de
Alvaro. Kraken: vetada por Alvaro.

AUTO-BOOTSTRAP TRAS RECONEXION (BLOQUEANTE 2 de la re-revision, CONSTRUIDO en P07)
El conector senala las reconexiones (drain_reconnected) y el MOTOR (drain_once, que el
componente ejecuta en cada tick) dispara el bootstrap REST por el MISMO camino de
normalizacion+dedup, con fault isolation por stream (un bootstrap fallido de un stream no
tumba a los demas). Demostrado en caliente contra Binance real: fetch_recent(10) tras la
reconexion dedupo el solape ya persistido y persistio las velas del hueco, sin duplicar.

DEFECTOS HALLADOS Y QUIEN LOS ENCONTRO (D1-D9)
- D1: CLOSED_PIECES sin P06/P06b (periferico, leyendo el guardia).
- D2: un test de integracion usaba market.* como diferido (la suite; omision del
  periferico).
- D3: la ventanilla chocaba con R5 del 7.8 (el check 7.8) -> CA-P07-G.
- D4: deriva estructural ce_v5/core/market (periferico en revision; revertido).
- D5: datos de test imposibles cazados por la frontera de confianza (la suite).
- D6: la guardia 5.20 abortaba el proceso de test/worker -- NO es bug, es la guardia
  mordiendo (al ejecutar); se resolvio acotando el ENTORNO por rol, sin tocar la guardia.
- D7: el patron de simbolo {2,15} rechazaba tickers de 1 caracter legitimos (Binance 'T',
  Threshold) -- la validacion en caliente REAL; se amplio a {1,20}.
- D8: sync_catalog crasheaba (CheckViolation) ante un simbolo no-ASCII de Binance -- la
  validacion en caliente REAL; se aplico ADR-006 al catalogo (saltar y CONTAR, fault
  isolation), sin ampliar mas el patron.
- D9: el veredicto del arnes asumia "cero crecimiento" del historico y el contador de
  reconexiones no contaba los cierres LIMPIOS -- la validacion en caliente; el codigo de
  PRODUCCION era correcto (el bootstrap DEBE rellenar el hueco, y una reconexion limpia ES
  una reconexion). Se corrigio el veredicto y el contador, no la logica.
D7 y D8 JUSTIFICAN la exigencia de Central de un conector REAL: solo el mundo real los
revela; un fake jamas habria mandado 'T' ni un simbolo chino.

CONTEO DE SKIPS (regla 5.18) sobre f62e4e0
Job backend corre 661 (639 unit + 22 componentes) y SALTA los 209 de integracion (skipif
de modulo por ausencia de DSN); job backend-integration corre los 209 con
PostgreSQL+Redis+los cuatro roles; CERO grietas (todo test de DB vive en
tests/integration/). Local con los cuatro DSN: 870, cero skips.
=====================================================================
19. T-03: SEGUNDO Y TERCER CONECTOR PUBLICO (OKX, BYBIT v5) - TRABAJO TRANSVERSAL
=====================================================================
DISPARADOR: INMEDIATAMENTE DESPUES de P07, ANTES de P08. Es la PRUEBA DE FUEGO de CE-14
(copiar carpeta + manifest + reiniciar): un exchange nuevo debe ser un adaptador nuevo, no
tocar el ingestor.

REGLA DURA: si anadir OKX o Bybit exige tocar contratos, fronteras de capa o
MarketStreamKey, SE PARA Y SE ELEVA -- significaria que P07 esta MAL construida (la
abstraccion de exchange no seria tal).

BARRIDO POR EXCHANGE: se repite el barrido 5.15 POR CADA exchange, entero. NO se copia el
de Binance: cada exchange tiene su propio heartbeat (Bybit usa 15 s, no 20), su formato de
vela, su semantica de cierre y su reconexion. Copiar el barrido de uno a otro es
exactamente el error que Central prohibio.

REFINAMIENTO REGISTRADO: el pre-filtro de simbolos por-exchange en el connector (rechazar
en list_instruments lo que ese exchange lista y no representamos) es endurecimiento
POR-EXCHANGE de T-03; el filtro GENERICO de sync_catalog (saltar y contar, D8) ya cubre a
todos por igual mientras tanto.

=====================================================================
20. CIERRE DE T-03 - SEGUNDO Y TERCER CONECTOR PUBLICO (OKX, BYBIT); VEREDICTO CE-14
=====================================================================
Estado: COMPLETADO. Doble revision Central + CSA conforme; firmado por Alvaro 2026-07-16. Trabajo transversal (no cierra M3). Commits (Actions VERDE 3/3): registro T-03-A f1024ba (run #12); OKX 1daa784 + fix formato 8fdf15f (run #14); Bybit 2061f89 (run #15).

VEREDICTO CE-14: SE CUMPLE. OKX y Bybit se anadieron SIN tocar el nucleo de P07 (ni el motor IngestionEngine, ni el ingestor, ni los contratos market.*, ni MarketStreamKey, ni la frontera de confianza normalize.py, ni el patron outbox, ni el subscription manager, ni el check 7.8). Un exchange nuevo = su carpeta en infra/connectors/<exchange>/ + UNA linea plana de registro en build_default_registry. Es registro EXPLICITO, no una rama; es el minimo irreducible para un adaptador de infra (el contrato de capas 7.1 prohibe que infra importe el registro, y el discovery por carpeta para infra-adapters esta prohibido por el encargo).

T-03-A (CORRECCION ARQUITECTONICA FIRMADA). El if-chain de seleccion de conector del composition root de P07 (worker_ingestion/composition.py, _build_datasource) era la UNICA fuga de extensibilidad: anadir un exchange exigia editar una rama del nucleo, contra la letra de CE-14 y ADR-009. Se sustituyo por un ConnectorRegistry minimo por convencion (register/resolve; factories tipadas que devuelven MarketDataSourcePort, no la clase concreta). El if-chain quedo ELIMINADO (cero "if kind =="); 7.1 KEPT (el motor sigue dependiendo solo del puerto).
PRUEBAS DEL ConnectorRegistry (por nombre, condicion del CSA), en tests/unit/entrypoints/worker_ingestion/test_connector_registry.py:
  - test_resuelve_binance_por_kind: resolve("binance") -> BinanceSpotConnector.
  - test_resuelve_fake_por_kind: resolve("fake") -> FakeMarketDataSource.
  - test_kind_desconocido_falla_fuerte: un kind no registrado -> UnknownConnectorKindError (fail-loud, jamas default silencioso).
  - test_colision_de_kind_rompe: registrar dos veces el mismo kind -> DuplicateConnectorKindError.
  - test_resuelto_satisface_el_puerto: lo resuelto expone los metodos del puerto MarketDataSourcePort (devuelve el PUERTO, no una clase concreta filtrada al motor).
  - test_registro_por_defecto_expone_los_kinds_de_serie: el registro de serie expone {binance, fake, okx, bybit}. La resolucion de okx y bybit queda afirmada aqui y ademas EJERCITADA en caliente por validate_okx_live.py y validate_bybit_live.py (resolve("okx")/resolve("bybit")).

HALLAZGO DE PROCESO. Ni la revision de Central ni la del CSA cazaron el if-chain en la doble revision de P07: la abstraccion parecia limpia porque el motor si depende del puerto. Lo cazo T-03 en el PASO 0 (volcado de solo lectura), leyendo el codigo antes de escribir. Valida hacer T-03 (probar la extensibilidad con dos exchanges) ANTES de construir P08 encima. P07 sigue historicamente ENTREGADA; la fuga se corrigio HACIA DELANTE (registro), sin maquillar su cierre.

UBICACION (corrige el prompt de T-03). OKX y Bybit viven como ADAPTADORES DE INFRA (infra/connectors/okx/, infra/connectors/bybit/), detras de MarketDataSourcePort, SIN manifest. NO son Componentes. El prompt de T-03 pedia "components/<exchange>/ con manifest"; era un error del prompt. P07 es consistente con ADR-014 y DOC_ESTRUCTURA sec.6 (el conector es infra; el Componente es el ingestor publico).

DEFECTOS Y HALLAZGOS, Y QUIEN LOS ENCONTRO.
  - D2 (OKX): el canal de velas de OKX va por wss://ws.okx.com:8443/ws/v5/business, NO por /ws/v5/public (migracion OKX del 20-jun-2023). Las fuentes secundarias decian "public". Lo cazo la VERIFICACION contra la doc oficial; de fiarme de memoria/blogs, el WS no habria recibido jamas una vela (fallo silencioso).
  - D3 (OKX): HTTP 403 en REST por el User-Agent por defecto de urllib (Cloudflare). Lo cazo la SONDA (probe_okx_live.py). Fix: cabecera User-Agent en REST y user_agent_header en el handshake WS. Se distinguio de un geo-block reintentando con UA.
  - D4 (Bybit): heartbeat de 20 s (no 15 s como decia la ficha de Central). Lo cazo la VERIFICACION contra la doc; la ficha estaba mal. Se usa ping JSON {"op":"ping"} cada ~18 s, SIEMPRE.
  Las sorpresas de T-03 fueron de INFRAESTRUCTURA (endpoint, UA, ping), no de DATOS: la frontera de confianza compartida (normalize.py) y el "saltar y contar" generico de sync_catalog, heredados de P07, absorbieron OKX y Bybit sin tocarse. Otro punto a favor de CE-14.

REGLA NUEVA (refinamiento de proceso, del D6). El barrido previo a commit incluye "ruff format --check ." del repo COMPLETO, ademas de "ruff check". Origen: en OKX, validate_okx_live.py paso "ruff check" pero fallo "ruff format --check ." en Actions (el formateador es distinto del linter, y la verificacion local no lo cubria sobre tools/). Actions no debe ser el primer detector de formato. No se repitio en Bybit.

BARRIDOS 5.15. docs/BARRIDO_SEGURIDAD_T03_OKX.md y docs/BARRIDO_SEGURIDAD_T03_BYBIT.md, uno POR exchange, fecha 2026-07-16, URLs vigentes; NO copiados del de Binance. Diferencias clave: OKX velas por /business, 240 subs/conexion (error 60014), ping texto en inactividad (<30 s); Bybit 10 args por peticion de suscripcion, 21.000 chars/conexion, 500 conexiones/5min, ping JSON periodico.

VALIDACION EN CALIENTE (salida real, exchange REAL, feed publico, jamas dinero). OKX: reconnections=1, bootstrap_candles=9, duplicates_skipped=1, filas=9 == claves distintas=9 (cero duplicados), catalogo 1307 activos. Bybit: reconnections=1, bootstrap_candles=10, duplicates_skipped=1, filas=10 == claves distintas=10 (cero duplicados), catalogo 592 activos. En ambos el MOTOR se rebootstrapeo SOLO tras la reconexion forzada (el arnes no llamo a fetch_recent). CI HERMETICO: connector.py (IO) no se prueba en CI (5.18, declarado); se valida en caliente. Cero skips silenciosos.

RECORDATORIO OPERATIVO. Para conectores reales, la fuente es la DOC OFICIAL VIGENTE del exchange, jamas memoria ni snippets secundarios (D2 y D4 lo demuestran).
=====================================================================
21. EXPANSION DE M3 A PARIDAD FUNCIONAL v4 (EXP-M3-01) Y DECISIONES ASOCIADAS
=====================================================================
EXP-M3-01 (firmada 2026-07-17; doble revision Central + CSA): M3 EXPANDIDO a paridad
funcional v4. NO reabre ADR (cubre el hueco del catalogo concreto de DataSources;
ADR-014/008/015 ya lo preveian). DOC_ROADMAP_V5 se mantiene CONGELADO; la expansion vive
AQUI (el conteo original de 19 era previo). Piezas y orden:
  P07 [ENTREGADA] -> T-03 [ENTREGADA] -> P07b (trades+footprint) -> P07c (orderbook L2
  con estado) -> P08 -> P08b (DataSources candle-derived) -> P08c (DataSources
  footprint/L2-derived) -> P09a.
Paralelismo: P08 || P07b || P07c || P08b; P08c tras P07b+P07c; P09a tras P08.
Inventario: 19 -> 23 unidades.
CONSTRAINTS:
  - CE-14 en P07b/P07c: si tocan nucleo, ELEVAR.
  - trades INDIVIDUALES (trade, no aggTrade).
  - orderbook por STREAM DE DELTAS publico sin login; integridad POR SECUENCIA sin
    checksum (el checksum de OKX esta DEPRECADO = 0).
  - semilla + resync como ancla (Binance REST /api/v3/depth; OKX/Bybit primer msg WS).
  - DA-02-1 RESUELTA: OKX tiene el libro publico mas profundo (400/100ms).
Detalle: dictamen CSA 2026-07-17.

DEC-PARIDAD-01: paridad v5.0 = lo que v4 DISENO/construyo, aunque no se integrara. La
vision futura explicita (flat_with_delta, AREA 4-pre) -> v5.1.

DEC-PROVISIONAL-01: el provisional (pivote/divergencia) es VALOR de DataSource con
maturity_state + confianza en v5.0 (consultable/dibujable); reglas y alertas disparan
SOLO en confirmado; alertas provisionales retractables -> v5.1. P08 en v5.0 retracta solo
por CORRECCION DE DATOS (H-02-3), no por supersion.

DEC-PROVISIONAL-02: el modelo "predecir" consume SOLO datos CERRADOS (candle_closed +
footprint/delta hasta el ultimo cierre). El pivote es provisional solo porque sus R barras
derechas aun no cerraron; se RE-EVALUA en cada candle_closed. NO se lee microestructura
intrabar viva. RESPETA el invariante de P07 (evaluar solo sobre candle_closed, jamas sobre
candle_updated); no es cambio arquitectonico. Leer intrabar vivo SERIA cambio del
invariante de P07 -> fuera de v5.0, a ELEVAR.

DEC-CVD-01: CVD (delta acumulada) = DataSource NUEVA en v5.0.

DEC-ABSORCION-01 (cierre de Q-D con el estado real de v4): absorcion CORE footprint-based
(tipos bid/ask + exhaustion) = v5.0 (el AbsorptionZoneEngine de v4 era footprint-based).
Absorcion por refill de limitadas / L2-based = v5.1. flat_with_delta ("oculta") = v5.1.

DEC-DIVERGENCIA-01 (cierre de Q-E): divergencia precio-vs-RSI = v5.0 (v4 la tenia).
Precio-vs-volumen = v5.1 (feature nueva; el DivergenceEngine de v4 no tiene campos de
volumen).

DEC-AHP-01 (Analisis Historico Previo, POLITICA): obligatorio ANTES de fijar cualquier
detector estadistico/heuristico-compositivo (absorcion, scores de orderflow, climax, void,
notrade, pivotphase, cualquier score con umbrales/pesos/ventanas). NO exigido para
deterministas cerrados (EMA/SMA/RSI/MACD): ahi la prueba es formula + fixtures +
comparativa. Un AHP registra: hipotesis; variables observadas; formula/score; ventanas;
umbrales iniciales; limites de interpretacion; falsos positivos esperables;
dataset/fixtures reproducibles (semilla fija); criterio de aceptacion; razon de descarte.
Umbrales sin soporte = "a calibrar por AHP", NUNCA inventados. Origen: dictamen CSA de la
expansion M3.

DEC-SNAPSHOT-REPLAY-01 (formaliza "D-04"): ante correccion append-only (candle_corrected u
orderbook), los DataSources derivados se recomputan por SNAPSHOT + REPLAY. El snapshot es
CACHE derivada (NO fuente de verdad); lleva as_of, input_range, formula_version,
price_source, bucket_offset y referencia al canon del DataSource. Se reabre desde el ultimo
snapshot valido y se replaya el tramo afectado; NO se muta el pasado canonico; reproducible
con historico + plan + Clock/SimulatedClock (ADR-007). Exacto y O(1).

CA-P08-01 (firmada 2026-07-17): CLARIFICACION de ADR-015 (no enmienda).
rule.evaluation_completed se emite solo por TRANSICION DE ESTADO, con EvaluationResult
granular; rule.firing/resolved = FLANCOS; rule.firing = ANCLA CAUSAL de signal.*/alert.*
(causation_id). [Ya relayada a P08; se registra aqui como registro central.]

DA-I03-1: ancla determinista del swing = pivote por FUERZA SIMETRICA N=R (el fractal es el
caso N=R=2); ZigZag/ATR solo para vista/encadenado (repintan).

H-02-5: CERRADA / ABSORBIDA. Provisional-as-of vs confirmado-determinista con correcciones
append-only cubre AMBAS definiciones de "reproducible"; sin interpretacion extra de
ADR-007.

DA-I03-4 (cerrada por Central): UNA sola primitiva swing.* (mismo metodo y mismo N/R) para
los pivotes de PRECIO, de RSI y de CVD. NO se admite una segunda definicion de pivote.

DA-I03-5 (Central; costura importante): la DIVERGENCIA DE CVD (evidencia de orderflow,
v5.0, alimenta pivotphase y el modo predecir) NO es la DIVERGENCIA DE VOLUMEN (precio
contra serie/velas de volumen, v5.1 por DEC-DIVERGENCIA-01). Son DISTINTAS; P08c NO las
confunde ni las mezcla.

RULING OA-1 (reinicio del CVD, mercado 24/7): NO se fija punto unico. El CVD es DataSource
con reset_policy como PARAMETRO declarado (session-UTC | rolling), en la cache_key, NO
hardcodeado. RESTRICCION DURA para el modelo de pivote: la divergencia de CVD entre los dos
swings comparados exige un CVD CONTINUO a traves de ambos (rolling o anclado antes del swing
anterior); un reinicio NO puede caer entre los dos pivotes. Default por AHP.

RULING OA-2 (correccion de premisa): queda SUPERADA la premisa "OKX da libro publico mas
grueso". I-02-V (doc primaria) cerro que OKX books = 400 niveles a 100ms, el libro publico
MAS PROFUNDO de los tres; DA-02-1 resuelta. NO existe la asimetria de profundidad. La
absorcion por refill/iceberg (que exige L2 fino) es v5.1 (DEC-ABSORCION-01); en v5.0 la
absorcion es footprint-based, robusta a 100ms. OA-2 RETIRADA de v5.0. Residuo v5.1: graduar
el refill por cadencia por-exchange (Bybit 10-20ms mas fino).

RULING OA-6 (frontera de la confianza del pivote): la confianza del pivote provisional es
una DataSource PROPIA (swing.confidence, ADR-008), REFERENCIABLE por las Rules como
cualquier otra fuente; NO un output enterrado en pivotphase. Coherente con el contrato de
I-03.

DA-I04-1 (regla anti-doble-conteo INTERNA): pivotphase CONSUME swing.confidence y anade SOLO
su capa estructural propia (FSM de fases 0-5, vp_level_price/zona, veto notrade); NO
re-gradua la misma evidencia de orderflow (absorcion/CVD/imbalance/VP) que swing.confidence
ya incorporo. Doble consumo (swing.confidence + orderflow crudo) contaria DOS VECES la misma
evidencia. Complementa a la regla semantica 5 (nivel Rule) en el nivel INTERNO. El reparto
exacto se finaliza en P08c con la FSM de v4 (GAP-P08c).

RULING OA-4 (estructura de combinacion del modelo de probabilidad): APLAZADA a P08c con
fixtures; no se elige sin datos. Empezar por lo EXPLICABLE (score con pesos AHP) y escalar a
logistico/calibracion (Platt/isotonica; la isotonica pide >~1000 muestras) SOLO si el volumen
de fixtures lo justifica. Gateado por DEC-AHP-01.

RULING OA-5 (nivel de rotura que marca retracted): a calibrar por AHP.

GAP-P08c (dependencia de construccion; CERRAR ANTES DE P08c): la FSM de fases 0-5 de
pivotphase y el mapeo POC/VA de vp_level_price son BESPOKE de v4 y NO estan en el knowledge
(solo se confirma que existen). Se RECUPERAN del codigo de v4 al construir P08c. Si el
fuente de v4 no estuviera disponible -> RE-DERIVACION con AHP y RATIFICACION EXPLICITA de la
deriva por Alvaro (no seria paridad literal).

ESTADO DE INVESTIGACIONES (refleja tambien ESTADO_CONSTRUCCION_V5):
- I-03 (pivotes/divergencias): COMPLETO (5 secciones). Pendiente solo GAP-P08c en
  construccion.
- I-04 (orderflow): EN CURSO. Partes 1 (primitivas) y 2 (modelo de probabilidad)
  ENTREGADAS; PENDIENTES Partes 3 (AHP), 4 (reproducibilidad) y 5 (decisiones).

ADDENDUM (APPEND-ONLY, cierre de I-04):

OA-7 (clarificacion Central): "AHP" en el proyecto = ANALISIS HISTORICO PREVIO (el
artefacto de diez campos de DEC-AHP-01), NO el Analytic Hierarchy Process de Saaty. Si
se usa el metodo de Saaty para derivar pesos w_i, se le nombra "metodo de Saaty" para no
colisionar. Por defecto: pesos iguales de baseline + refinamiento logistico.

OA-10 / E-1 (Central; confirma DEC-SNAPSHOT-REPLAY-01 para el CVD): el snapshot+replay
CUBRE el CVD como INTEGRADOR. PRECISION: a diferencia de EMA/RSI (que olvidan; el efecto
de una correccion decae en ~N barras), el CVD NO olvida: una correccion en la barra k
desplaza TODO el CVD posterior. La propagacion es ILIMITADA hacia delante SALVO que la
acote el reset_policy (OA-1: reset de sesion o ventana rolling). CONSTRAINT de
construccion del DataSource CVD: snapshotear el acumulador POR VENTANA DE RESET; ante
correccion en k, replay desde el snapshot dentro de la ventana afectada. NO reabre
ADR-007 ni DEC-SNAPSHOT-REPLAY-01.

OA-8 / OA-9 (APLAZADAS a P08c): el objetivo tolerable de FP y el margen de lift se
PRE-REGISTRAN en P08c ANTES de ver el test; la granularidad de estratificacion por
regimen (simbolo/familia/bucket de volatilidad) se decide con datos.

E-2 (nota, sin elevacion): el contrato de pivotphase.confidence como DataSource (id,
unidades de historia, cache_key con version de formula + reset_policy) se cierra con
ADR-008 en P08c.

ESTADO DE INVESTIGACIONES (ACTUALIZADO; refleja tambien ESTADO_CONSTRUCCION_V5): I-04
COMPLETO (Partes 1-5 consolidadas). La investigacion de pivotphase/divergencias/orderflow
(I-03 + I-04) queda CERRADA; alimenta la construccion de P08b/P08c.

CORRECCION DE DECISION (2026-07-18) -- EL ROADMAP SE AMPLIA, NO SE CONGELA.
El 2026-07-17, al firmar EXP-M3-01, se eligio -por recomendacion de Central-
mantener DOC_ROADMAP_V5 CONGELADO como historico, dejando la ampliacion solo
en este registro. Esa decision se REVIERTE el 2026-07-18, firmada por Alvaro.
MOTIVO, sin maquillar: el prompt de cada periferico le manda leer "la ficha de
su pieza" en DOC_ROADMAP. Con el roadmap congelado, los perifericos de P07b,
P07c, P08b y P08c abririan el documento y NO ENCONTRARIAN SU PROPIA PIEZA. El
periferico I-04 ya choco con referencias a piezas inexistentes y tuvo que
elevarlo. Un plan desactualizado no es historia preservada: es desinformacion
operativa. La regla de oro protege la ARQUITECTURA (los ADR), no el PLAN.
CORRECCION: DOC_ROADMAP_V5 recibe una SECCION DE AMPLIACION A-1 en APPEND-ONLY
con el M3 ampliado y la ficha completa de las cuatro piezas nuevas. El
contenido original v1.0 queda INTACTO y marcado historico (mismo criterio que
la nota T-01 y que los cierres que dicen "1 de 3 de M3": eran ciertos cuando se
escribieron). NUNCA reescritura silenciosa.
El CSA ya habia admitido esta via en su dictamen de EXP-M3-01: "si se edita
roadmap, debe ser append-only o seccion de ampliacion, no reescritura
silenciosa".
=====================================================================
22. CIERRE DE PIEZA P08 - MOTOR DE REGLAS (ADR-015/016/017)
=====================================================================
Estado: ENTREGADA. Doble revision Central + CSA conforme; firmado por Alvaro 2026-07-21.
NO cierra M3 (quedan P07b, P07c, P08b, P08c y P09a).
Commit de pieza: 59855bf (59855bfceb66dce28eb62858dec9788da2008e45). Refinamiento
documental de las puertas de revision: 107e94f
(107e94f476a7b9de1067cd9d214069ec084b2ee5).
ACTIONS VERDE 3/3 sobre 107e94f, cabeza del PR wip->main (run #18): Backend,
Backend-integration y Frontend, los tres Success. El job backend-integration corrio por
PRIMERA VEZ la provision de ce_v5_rules y el check_rules_access sobre un PostgreSQL
VIRGEN del runner, que es justo lo que la regla 5.22 exige demostrar (no basta el barrido
local). El merge a main se hizo por git con --no-ff para PRESERVAR ambos hashes, que este
registro cita: la caja "Merge" de GitHub los habria reescrito.
Suite: 1040 tests, CERO SKIPS en local con los CINCO DSN -- app, migraciones, operador,
ingesta y REGLAS (regla 5.18). El quinto (CE_V5_RULES_DATABASE_URL) lo estrena esta pieza;
una version anterior de esta linea decia "cuatro" y se corrige aqui.

LAS NUEVE CONSULTAS FIRMADAS (CA-P08-01..09)
- CA-P08-01 (emision por TRANSICION): se emite solo en el FLANCO; firing/resolved son
  flancos, no estados repetidos por vela. La auditoria por-vela se persiste, NO va al bus.
- CA-P08-02 (estado y atomicidad): rule_lifecycle_state tenant-scoped con RLS + FORCE;
  estado y outbox en UNA sola transaccion; reparto de poder segun 5.20; el hash canonico
  incluye schema_version; la cuota por plan se difiere a P11.
- CA-P08-03 (ventanilla): rules_for_market SECURITY DEFINER cross-tenant; manda el tenant
  de la COLUMNA, NUNCA el del JSON de la definicion; nace SystemScopedDatabase, patron
  reutilizable en P09a/P10b.
- CA-P08-04 (FSM): K3 + veto fail-safe. NOT_EVALUABLE mantiene; RESOLVED solo con FALSE
  real; STALE tras M velas; QUARANTINED por CompilationError o N excepciones. Sin "for"
  en v5.0.
- CA-P08-05 (motivos tipados): veto_outcome tipado; resolved_reason y stale_reason;
  STALE/QUARANTINED como estado OPERACIONAL (no de la FSM); migracion 0014.
- CA-P08-06 (cuarentena observable): rule.quarantined como tipo de la familia rule.*;
  ce_v5_app LEE rule_lifecycle_state de su propio tenant (migracion 0015).
- CA-P08-07 (mercado de solo lectura): ce_v5_rules LEE market publico (SELECT sobre
  market_candle y NADA MAS), migracion 0016; el SubscriptionIntent lo escribe la AUTORIA,
  atomico con la regla; el ciclo de vida va por enabled, no por salud.
- CA-P08-08 (correccion): re-evaluado por candle_corrected v5.0 SOLO point-local, ventana
  [T, T+h-1]. Ante una regla cuyas fuentes NO son point-local, el manejador de correccion
  OMITE LA CORRECCION con el motivo logueado y deja la regla NO CONFORME v5.0: NO la
  cuarentena (no es un fallo de la regla, es alcance no construido todavia) y se difiere a
  P08b/P08c.
  NOTA DE VOCABULARIO (regla 5.18): esa omision es COMPORTAMIENTO DE RUNTIME del motor y
  NO tiene ninguna relacion con los skips de pytest. Este documento afirma CERO SKIPS de
  suite; para que las dos cosas no se confundan al leerlas juntas, el comportamiento de
  runtime se dice "OMITE la correccion", nunca "skip".
- CA-P08-09 (correction_revision): pasa a int (no opcional) en CandleCorrectedPayload
  (familia market). Correccion pre-consumidor CROSS-FRONTERA sin bump de version
  (precedente CA-01). Se retira la barrera local 7.3-c del worker (el TIPO la hace
  innecesaria) y el 7.7 se re-baselina en el commit de pieza.
  LA REJA DE CINCO EVIDENCIAS. "Sin bump" no es una afirmacion suelta ni una comodidad:
  es la conclusion de CINCO evidencias verificadas una a una en la tanda CIERRE-1, y son
  tambien lo que justifica NO aplicar aqui el prerrequisito del 7.7 version-aware. Se
  dejan por escrito para que cualquiera pueda re-verificarlas sin fiarse del recuerdo:
    1. El validador de CandleCorrectedPayload YA prohibia None (rechazaba el payload al
       construirlo); el cambio traslada esa prohibicion del validador al TIPO.
    2. Ningun PRODUCTOR entregado emite None: no existe camino de emision que construya
       un candle_corrected sin revision.
    3. Ningun CONSUMIDOR entregado trata None como valido: nadie lee el campo esperando
       ausencia.
    4. No hay fixture, baseline ni evento valido con correction_revision=None en el
       repositorio.
    5. El cambio solo ESTRECHA el tipo (int|None -> int, ge=1). No cambia la semantica
       del campo ni el significado del evento.
  CONCLUSION: con las CINCO se cumple correccion pre-consumidor y procede sin bump
  (precedente CA-01). REGLA DE PARADA: si faltara UNA SOLA, se DETIENE y se reclasifica
  -- opcion (b) de CA-P08-09, o construir ANTES el 7.7 version-aware --, porque entonces
  habria un consumidor o un dato real al que el estrechamiento le rompe el contrato, y
  eso ya no es correccion pre-consumidor sino evolucion incompatible.

DECISIONES DE CONSTRUCCION (dentro de area; ninguna reabre un ADR)
- Presupuesto de complejidad movido a contracts como FUENTE UNICA; se elimina el
  duplicado MAX_INTENTS_PER_RULE (= MAX_GROUPS_PER_RULE).
- Mecanismo del guardarrail 5.21: tools/check_envelope_base_usage.py (AST) + test de
  round-trip por registro, ambos EN CI.
- tools/check_contract_artifacts.py: paridad EVENT_PAYLOAD_REGISTRY <-> schemas <-> TS,
  EN CI.
- Cinco familias de P08 generadas: rule.evaluation_completed, rule.firing, rule.resolved,
  signal.raised, alert.raised (mas rule.quarantined por CA-P08-06).
- memory_model en DataSourceDeclaration (market.close = POINT_LOCAL).
- log_event movido a core/observability para que el worker no importe FastAPI.
- ENDURECIMIENTO 5.22 (integrado en el cierre, no pieza aparte). Auditoria de los 15
  checks contra ci.yml: check_rules_access.py era el UNICO dormido. Se engancha en el job
  backend-integration (misma ubicacion y forma que check_market_access de P07); se
  provisiona ce_v5_rules alli (CE_V5_RULES_DB_PASSWORD) y se exporta
  CE_V5_RULES_DATABASE_URL, calcando a ce_v5_ingestion. Sin secretos de repositorio
  nuevos: el job usa valores inline contra el Postgres efimero del runner.
  Y el piso de integracion que faltaba (37 tests, Postgres real, role-switching):
  (a) FRONTERA 5.20 en tests/integration/test_rules_access.py, positivos y negativos
      BIDIRECCIONALES, cada negativo rechazado por el MOTOR (permission denied /
      row-level security), no por codigo nuestro: el motor no escribe ni lee
      rule_definition fila a fila, no toca identidad/policy/kill switch/auditoria, no ve
      market_instrument ni market_subscription_intent ni market_public_demand (D1 de
      CA-P08-07), no fabrica ni reescribe market data, no encola familias ajenas
      (execution./policy./market./billing.) y no borra de la outbox.
  (b) CICLO NUCLEO ATOMICO en tests/integration/test_rules_cycle.py: candle_closed ->
      evalua -> FIRING -> evaluation_completed + firing + alert.raised con causation al
      firing, y estado + eventos en la MISMA transaccion. La PRUEBA DE ATOMICIDAD fuerza
      el fallo del INSERT de outbox (evento prohibido por la policy de 0013) y demuestra
      que el estado hace ROLLBACK: sigue en firing con el open_time viejo y la outbox
      queda intacta. SE VERIFICO QUE EL TEST MUERDE: replicada la version NO atomica
      (estado y outbox en transacciones separadas) fuera del repositorio, el estado
      avanzaba a resolved con el evento rechazado; el test lo caza.
  La fixture rules_db FALLA EN ALTO si hay base de datos y falta su DSN (regla 5.18),
  como ya hacian operator_db e ingestion_db: verificado.
- Ninguna migracion se edito. Las 0013-0016 y sus grants quedan bajo la regla 5.14:
  cualquier correccion futura de un grant va como migracion SUCESORA (0017...), NUNCA
  editando una migracion ya commiteada.

LIMITACIONES DE v5.0 REGISTRADAS (test 18; NO son deuda oculta, son alcance firmado)
- La correccion por candle_corrected solo actualiza el ESTADO VIGENTE (reevalua en L):
  NO reescribe las transiciones historicas de las velas intermedias.
- Solo fuentes POINT-LOCAL. Las recursivas (EMA/RSI/MACD) y el integrador (CVD) quedan NO
  CONFORMES en v5.0, diferidas a P08b/P08c (snapshot+replay de VALOR); el snapshot de
  ESTADO de la FSM se difiere mas alla.
- El anti-flap / "for" (D4) NO existe en v5.0 (diferido).

DIFERIDOS CON DUENO (regla 5.11)
- Catalogo de fuentes + investigacion por fuente -> la pieza CONSUMIDORA de cada fuente.
- Veto con contexto propio (CA-P08-02 p.8) -> pieza de edicion de reglas.
- Cuota por plan -> P11.
- Edicion de reglas (intents huerfanos, idempotencia de re-activar) -> pieza de edicion.
- shared_evaluation + orden por coste -> progresivo, cuando haya volumen que lo pida.
=====================================================================
23. T-05 -- VISOR DE DESARROLLO (TAREA TRANSVERSAL) Y NOTA DE TRAZABILIDAD DE abb7324
=====================================================================
NATURALEZA: tarea TRANSVERSAL (no pieza del roadmap), autorizada por Alvaro y Central 2026-07-21.
Herramienta de validacion desechable / semilla de P13; NO es el chart del producto.
ALCANCE (SI): pagina minima de KLineChart que pinta series CALCULADAS POR EL BACKEND (chart
tonto, I-01); velas de P07 (ya servibles) y, segun lleguen, DataSources de P08b/P08c; enchufe
con forma metainfo ligera (nombre, overlay-vs-panel, precision). Vive en frontend/src/dev-viewer
(cubierto por los checks; aislado de ui-core por depcruise).
DECISIONES DE T-05 (firmadas): (1B) live por SONDEO al endpoint publico, NO por realtime -el
canal de P06b es fail-closed y no entrega public_market; ampliarlo tocaria auth, descartado para
una herramienta-; (2) ubicacion frontend/src/dev-viewer; (3) Vite minimo (solo dev-server, no
build de producto; siembra la tooling de M4).
ALCANCE (NO): no es P13; sin herramientas de dibujo, sin PWA, sin overlays de producto, sin
logica de dominio en el chart. No VERIFICA (la verificacion es numerica: TradingView/fixtures).
CIERRE: ligero (informe breve + revision Central + OK Alvaro + commit + Actions verde); CSA solo
si toca superficie arquitectonica.
PORCION YA ATERRIZADA (commit abb7324): API publica de LECTURA de velas -- endpoint
GET /v1/public/market/candles (SOLO lectura, publico sin superficie de tenant; market_candle es
public_market), read_ohlcv_window + CandleOHLCV en infra/db/market_candles.py, wiring en
app.py/composition.py, tests. La 5.20 queda INTACTA: ce_v5_app solo tiene SELECT sobre
market_candle (la API no fabrica velas). read_close_window (camino de lectura que usa P08) quedo
factorizado en _WINDOW_SQL compartido, BYTE A BYTE identico y con test -> refactor cross-pieza
aceptable por preservar comportamiento, registrado aqui. (El visor en si se remato despues; ver
CIERRE.)
NOTA DE TRAZABILIDAD DE abb7324 (feat(p07b): maquinaria de ingesta de trades, Tanda 3a-i;
empujado a main, Actions verde 3/3): contiene DOS trabajos AUTORIZADOS -- P07b 3a-i (completo) +
la porcion de T-05 de arriba. La etiqueta solo menciono P07b: infravaloracion documentada AQUI,
sin reescribir la historia (5.14). CAUSA: un git add amplio en la tanda de commit de P07b
arrastro trabajo concurrente de T-05 sin commitear; origen de la regla 5.29.
CIERRE (T-05 ENTREGADA/CERRADA): construida en dos checkpoints sobre la rama wip/t-05-visor
(PR #2), cada sesion commiteando SOLO sus ficheros (5.29).
  - Checkpoint 1 (lectura de velas): porcion aterrizada en abb7324 (ver arriba) + REMATE DEL
    BORDE en 5acc9e0 -- symbol validado contra SYMBOL_PATTERN canonico y timeframe como enum:
    lo mal formado falla en 422 (ADR-006), antes salia 200 [] indistinguible de "sin datos";
    exchange libre; par canonico sin datos -> 200 []. read_ohlcv_window (hermana de
    read_close_window, dedup por revision vigente via _WINDOW_SQL comun) + endpoint publico
    GET /v1/public/market/candles (read-only, public_market, sin tenant) + market_db en
    ApiContext (rol ce_v5_app, solo SELECT por el GRANT de la 0012).
  - Checkpoint 2 (el visor): commit f7890e1. Visor minimo en frontend/src/dev-viewer, aislado
    (solo importa klinecharts + sus modulos; sin ui-core/app-core/device-*, depcruise verde).
    Vite 8.0.14 SOLO dev-server (proxy /v1 -> API local, evita CORS; configurable por
    CE_V5_DEV_VIEWER_API), KLineChart 10.0.0 via setDataLoader (getBars historico anclado por
    open_time = event_time ADR-007; vivo por SONDEO en subscribeBar, fusion por timestamp).
    Estados explicitos (cargando / sin datos / error / OK). Tipos DOM localizados con
    /// <reference lib="dom" /> sin tocar el tsconfig raiz. Hueco de DataSources (datasources.ts,
    forma metainfo: nombre, overlay-vs-panel, trazos, Via A pegada / Via B por stream) VACIO,
    listo para enchufar RSI (P08b) y pivotphase/divergencias/footprint (P08c) sin rehacer el
    visor; documenta el CRITICO 1 de I-01 (el calc de KLineChart devuelve array POR POSICION, no
    por timestamp; la doc oficial miente; exige comprobacion empirica antes del primer indicador
    real). datasources.ts es INERTE hoy (no se importa, no pinta nada): andamiaje, no codigo
    muerto.
  - Bateria completa local (5.30) VERDE en cada push; Actions VERDE 3/3 en el run #30010566621
    (PR #2) sobre f7890e1.
  - MERGE: wip/t-05-visor -> main con git merge --no-ff (preserva los hashes de la rama, como en
    P08; NO se usa el boton "Merge" de GitHub si reescribe hashes). El backend de T-05 ya vivia
    en main (abb7324); el merge aporta el remate 422 (5acc9e0) y el visor (f7890e1).
=====================================================================
24. P07b -- FASE 3a CERRADA: CONECTORES DE TRADES + MODELO HONESTO DE BACKFILL
=====================================================================
NATURALEZA: sub-fase de P07b (trades individuales, previa a 3b footprint). Se registra a
DISCO como cierre de PROCESO ("nada importante vive solo en el chat"); NO es el cierre
FORMAL de P07b (ese, con doble revision Central+CSA, va TRAS 3b). Los TRES conectores de
trades verdes (ci_local 24/24) y validados EN CALIENTE contra los exchanges reales.
COMMITS EN main: 78920bf (Binance: multiplexado de trades sobre la conexion de velas),
437a1dc + 308f812 (modelo honesto de backfill + allowlist de tenancy 7.8 para
market_trade_gap), e53fa22 (doc de la regla 5.30), e08bf6d (tools/ci_local.py), 295770a
(OKX), 5dba7af (Bybit).

MODELO HONESTO DE BACKFILL (ratificado por Central). El nucleo NO finge que un hueco no
existe: lo DECLARA.
- is_complete: bool en FootprintPayload, DEFAULT False = FAIL-SAFE (una barra nace
  incompleta hasta que se demuestre lo contrario). OPCIONAL (no required) A PROPOSITO, para
  respetar el check de compatibilidad de evolucion 7.7. ORTOGONAL a maturity_state (una
  barra puede estar CERRADA y a la vez INCOMPLETA).
- Tabla market_trade_gap (migracion 0018): APPEND-ONLY, public_market (SIN tenant, SIN RLS;
  su allowlist de tenancy es el check 7.8 -> de ahi el fix 308f812). Registra huecos por
  (gap_from_event_time_ms, gap_to_event_time_ms) con UNIQUE(exchange, market_type, symbol,
  gap_from, gap_to). La CONSUME 3b para poner is_complete=False en las barras que se solapen
  con un hueco.
- PORTS y motor: backfill_after_reconnect(key, last_seen) -> TradeBackfillResult es el UNICO
  metodo; fetch_recent_trades y bootstrap_limit ELIMINADOS (un N de config no guarda
  relacion con lo que duro el corte; la cota real la pone el techo del endpoint publico). La
  DECISION de cobertura vive DETRAS del Port, en cada conector (CE-14: cambiar de exchange o
  anadir uno es escribir un adaptador, no tocar el motor). FAIL-SAFE: covered=False ante
  cualquier duda. El motor pide last_seen a la BD (no a la memoria: asi un REINICIO con un
  hueco mayor que el techo REST tambien se detecta), hace el BACKFILL ANTES del poll (si
  drenase primero, last_seen apuntaria al OTRO lado del agujero y el hueco se cerraria solo,
  en silencio), y ante covered=False llama record_gap IDEMPOTENTE (+ metrica uncovered_gaps,
  que cuenta huecos REALES, no reconexiones). VINCULANTE aguas abajo: una barra incompleta
  -> NOT_EVALUABLE en P08 (CA-P08-04 D2): una regla no dispara sobre un footprint al que le
  faltan trades.

COBERTURA POR CONECTOR (verificada por SONDEO EN VIVO -- condicion de Central: la doc de los
exchanges es una SPA no citable, asi que se comprueba contra el exchange real; los sondeos
scratch se GRADUAN a tools/validate_*_trades_live.py, no se commitean como scratch):
- Binance: GET /api/v3/trades SIN fromId (las 1000 recientes, NO pagina). Cobertura por id
  MONOTONO; un hueco > 1000 -> incompleto. historicalTrades DESCARTADO: exige API key, que
  violaria el cero-credenciales del feed publico.
- OKX: canal 'trades-all' en /ws/v5/BUSINESS (no /public, ahi da error 60018). REST
  history-trades PUBLICO pagina por id (&after hacia atras) -> TAPA EL HUECO ENTERO (bucle
  acotado; tope de esfuerzo ~40 paginas / 12000 trades, luego fail-safe). CAP SILENCIOSO a
  300 (pides 1000 -> 300 con code=0): se trata 300 como techo de pagina, jamas se asume mas.
  Cobertura por id CONTIGUO.
- Bybit: 'publicTrade' en /v5/public/spot (compartido con velas; ping {"op":"ping"} < 20 s
  SIEMPRE). trade_id NUMERICO y contiguo (NO UUID) -> cobertura por id. recent-trade acotado
  a 60 y SIN paginar -> incompleto FRECUENTE (es lo ESPERADO en Bybit, no un bug). WS
  (i/p/v/S/T) y REST (execId/price/size/side/time) usan NOMBRES distintos pero el MISMO
  espacio de id (empalman). set_symbol_map (native<->canonical, simbolo pegado, como Binance).
- AGRESOR DETERMINISTA (se LEE del exchange, no se estima; de ahi footprint reproducible bit
  a bit): Binance flag 'm' (INVERTIDO: buyer-maker -> agresor 'sell'), OKX 'side', Bybit 'S'.

REGLAS DE PROCESO (ya registradas en la seccion 5, aqui por trazabilidad de esta fase): 5.29
(commit de rutas EXPLICITAS, nunca git add -A/. ni commit -a; cada sesion commitea SOLO sus
ficheros; nace del commit mixto abb7324) y 5.30 (verde = bateria COMPLETA de ci.yml;
mecanismo tools/ci_local.py, 24 pasos, con guardia anti-deriva que compara con ci.yml en las
DOS direcciones; nace del run #26). Ambas se aplicaron sin fallo en las tandas OKX/Bybit y
en T-05.

NOTAS ABIERTAS:
- T-05 (visor + endpoint de lectura de velas): YA ENTREGADA y EN main (seccion 23); estuvo
  "sin commitear en wip/t-05-visor a la espera de Alvaro" hasta el merge faf1d70. Ficha
  propia, NO es P07b.
- Candidatos futuros (no bloqueantes): hook pre-push que corra ci_local; unificacion "A" de
  ci.yml (un solo comando como fuente de verdad de la bateria).

SIGUIENTE -- 3b (agregacion footprint; topologia RATIFICADA):
- DISPARO por market.candle_closed (Opcion A): el footprint de una barra se agrega cuando su
  vela cierra, no por un reloj.
- BUCKETING por floor(event_time / tf_ms) en UTC dentro del cache_key: cada trade cae en su
  barra por su event_time de ORIGEN (ADR-007), no por cuando se proceso.
- Agregacion CONMUTATIVA (dedup por identidad natural del trade + suma): los mismos trades en
  cualquier orden producen el MISMO footprint -> reproducibilidad BIT A BIT sin necesidad de
  un orden total entre trades del mismo milisegundo.
- Outbox ATOMICO: footprint_closed / footprint_corrected en la MISMA transaccion que el
  estado (patron P02b).
- CONSUME market_trade_gap para fijar is_complete en las barras solapadas con un hueco.
- Derivacion demanda-footprint -> stream de trades: un interes en footprint suscribe el flujo
  de trades subyacente, bajo CE-14.
=====================================================================
25. T-04 -- FEASIBILITY DEL COMPARADOR TRADINGVIEW (CERRADA)
=====================================================================
NATURALEZA: tarea TRANSVERSAL de INVESTIGACION (feasibility), no pieza del roadmap.
SIN codigo de producto, sin repo, sin commit de producto, sin CI. Entregable = diseno
de verificacion por FIXTURE que alimenta el DoD de P08b. Informe completo en el knowledge:
claude/INFORME_T04_FEASIBILITY_TRADINGVIEW_v5.md. Dictamen de Central 2026-07-23; no
reabre ADR; sin CA/CSA.

PREGUNTA CENTRAL Y RESPUESTA. Expone TradingView, de forma programatica y por via oficial,
la SERIE del RSI (o las velas) de un indicador para compararla numericamente con la nuestra?
NO. No hay API de consumo de valores ni de velas; la unica REST oficial (Broker Integration)
va en sentido INVERSO (los brokers ALIMENTAN datos hacia TradingView); los Terminos prohiben
el uso non-display (maquina a maquina); ni la tarifa Ultimate anade una API de datos. Coherente
con T-05 (PineJS calcula en el CLIENTE; TradingView ALIMENTA, no ENTREGA valores).

GO/NO-GO (RATIFICADO por Central).
- Comparador EN VIVO por API oficial: NO FACTIBLE. RETIRADO.
- Comparador por FIXTURE (CSV "Download chart data", velas + RSI de TradingView sobre las
  MISMAS velas, en un solo fichero): FACTIBLE y es LA via para P08b. Verificado de primera mano
  en el dialogo de la web app (texto "...including the symbol & indicators will be saved to a
  CSV file"; selector de tiempo "ISO time"/"UNIX timestamp" en UTC; indicador "RSI 14 close").

DECISIONES DEL DICTAMEN.
- Q-T04-1 (RATIFICADA): DoD de P08b "verificacion contra TradingView" = "dentro de tolerancia
  tras warm-up", NO igualdad bit a bit. NO reabre ADR-007 (su bit-a-bit es sobre NUESTRO motor,
  no contra la caja negra de TradingView).
- Q-T04-2 (RESUELTA): puerta de arranque = tras warm-up (~100-150 velas), delta absoluto maximo
  <= 0,1 puntos de RSI; se AFINA al ULP real cuando se exporte el CSV (frontera de pago de Alvaro).
- Q-T04-3 (FRONTERA de Alvaro): contratar plan de pago para exportar y lectura LEGAL de los ToS.
  Central NO opina. DESACOPLE: el referente Wilder preferido es TradingView SI la asesoria lo
  despeja; si NO, referente Wilder publicado / dataset no restringido (I-01 fijo la convencion).
  P08b NO queda bloqueada por la resolucion legal de TradingView.
- Q-T04-4 (RESUELTA): GOLDEN FIXTURE = un CSV por simbolo/timeframe, versionado como fixture de
  test; sin refresco periodico; actualizaciones deliberadas y versionadas.

NO VERIFICADO restante: nombres exactos de columna y decimales exactos de la columna RSI en el
FICHERO descargado; se cierra al EXPORTAR, dentro de P08b (accion de pago, frontera de Alvaro).

FRONTERA (registrada, sin opinar): la contratacion del plan y la legalidad del uso del dato de
TradingView son de Alvaro (comercial/legal). T-04 solo documento lo que dicen doc y Terminos.
=====================================================================
26. P07b -- CIERRE FORMAL: AGREGACION DEL FOOTPRINT (3b) Y ENTREGA DE LA PIEZA
=====================================================================
NATURALEZA: cierre FORMAL de P07b (trades + footprint), con doble revision Central+CSA.
Cierra la fase 3b (agregacion del footprint) SOBRE la 3a ya cerrada (conectores de trades
+ modelo honesto de backfill, seccion 24 -- NO se duplica aqui). Sin ADR nuevo (5.12 no se
activa: market.* ya existia). Reglas 5.29/5.30 ya persistidas.

DECISIONES (3b).
- CELDA = TICK NATIVO (precio exacto), LOSSLESS: una celda por nivel de precio del
  exchange, sin agrupar por price_step ni capar el numero de celdas. Agrupar o capar
  reintroduciria perdida de informacion. Observabilidad: metrica celdas-por-barra (sin
  cap; se vigila el maximo), NO un limite.
- INVARIANTE DE REPRODUCIBILIDAD BIT A BIT: dedup por identidad natural del trade
  (exchange, market_type, symbol, trade_id) + agregacion CONMUTATIVA por celda (suma de
  Decimal). Los mismos trades en cualquier orden producen el MISMO footprint byte a byte,
  sin necesidad de un orden total entre trades del mismo milisegundo.
- BUCKETING: floor(event_time / tf_ms) en UTC; ventana semiabierta
  [open_time, open_time+tf_ms). Un trade en la frontera cae en UNA sola barra.
- BACKFILL ACOTADO + is_complete FAIL-SAFE, con cobertura POR-PORT (cada exchange decide
  con el criterio que su API permite; al nucleo llega la MISMA forma comun):
    Binance: una ventana REST de 1000 por id monotono.
    OKX: paginacion con &after hasta empalmar o hasta el TOPE de esfuerzo
      (_BACKFILL_MAX_PAGES); agotado el tope sin empalmar -> covered=False -> hueco ->
      barra INCOMPLETA. Cap silencioso de 300 respetado (se pide EXACTAMENTE 300).
    Bybit: recent-trade de 60 SIN paginar -> un corte mayor de 60 trades deja hueco ->
      barra INCOMPLETA (ver LIMITACION CONOCIDA).
  is_complete=False si algun market_trade_gap solapa la ventana; True solo si ninguno la
  toca. NUNCA se publica una barra truncada como completa.
- PERSISTENCIA: market_footprint + outbox en LA MISMA transaccion (ADR-013), idempotente
  por footprint_idempotency_key. candle_corrected -> footprint_corrected append-only, con
  idempotency_key que NO colisiona con el closed (event_type + maturity_state + revision).
- CONSUMO POR DEMANDA (3b-1): worker propio bajo ce_v5_ingestion, poll+ack SIN inbox
  (idempotente por la clave), que agrega al recibir market.candle_closed (Opcion A).
- TESTS DE COSTURA (cierre): tests/integration/test_footprint_okx_gap_seam.py sella en UNA
  sola prueba cap OKX -> covered=False -> record_gap -> footprint is_complete=False (y la
  fila de outbox lo refleja). Asercion explicita de outbox del corrected en
  test_market_footprint.py. Ambos MUERDEN (verificados por mutacion: forzar covered=True /
  saltar el encolado -> ROJO).

REDEFINICION DE DoD -- RETENCION ESCENARIO B (VERBATIM, ratificada por Alvaro 2026-07-23):
  ESTADO: sin mecanismo de retencion/trimming; tablas de mercado (market_trade,
    market_footprint, market_trade_gap) APPEND-ONLY con DELETE/TRUNCATE REVOCADO a
    runtime (5.20) -> cero borrado, dedup y correccion vigente intactos por construccion.
  DUENO: tarea de retencion/ops POSTERIOR (post-P08c); NO entregable de P07b.
  MOTIVO: (1) el mandato de P07b es "medicion empirica de volumen ANTES de dimensionar
    retencion", representativa solo con footprint fluyendo (post-P08c); metricas ya
    instrumentadas (celdas-por-barra, uncovered_gaps). (2) el camino de BORRADO + rol de
    mantenimiento es diseno de acceso que no se precipita en el cierre.
  CONDICION DE SALIDA: se construye/dimensiona el trimming cuando (a) haya medicion
    empirica de volumen, (b) antes de escala de produccion, y (c) con revision de acceso
    del rol de mantenimiento (5.20/5.19).
  POR QUE NO ROMPE v5.0: append-only + DELETE revocado -> cero perdida; dedup y correccion
    intactos; footprint no fluye en produccion hasta P08c (crecimiento acotado a
    dev/test); fail-safe y reproducibilidad independientes de la retencion.

LIMITACION CONOCIDA v5.0: Bybit incompleto frecuente (recent-trade 60 sin paginar;
  fail-safe is_complete=False; NO falsifica completitud; P08c vera NOT_EVALUABLE cuando
  necesite footprint completo; pieza duena de mejorar cobertura identificada si se decide).

NOTA Binance: geo-bloqueo INTERMITENTE en dev; footprint validado en Bybit (agnostico del
  exchange); sin impacto en codigo.

HEREDA P08c:
  - Spec 3b-2: footprint_stream_keys(E,S,tf) -> {trades(E,S), candles(E,S,tf)} intents,
    edge-only, source_type=DATASOURCE, atomico. Auto-expand en el nucleo/ventanilla
    RECHAZADO.
  - Consumo del footprint: is_complete=False -> NOT_EVALUABLE (CA-P08-04 D2); la ausencia
    de is_complete se trata como incompleto (spec heredada; no hay consumidor aun).

REGLAS Y CHECKS: 5.29 (rutas explicitas) y 5.30 (bateria completa antes del push) ya
  persistidas. check_market_access (5.22) enganchado y demostrado EN ACTIONS (job
  backend-integration de ci.yml). Sin ADR nuevo; 5.12 no se activa.
