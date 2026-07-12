# ADRS_PROPUESTOS.md

Registro de ADRs (Architecture Decision Records) de Crypto Engine V5.
Formato: METODOLOGIA seccion 3. Se numeran desde ADR-001 por orden de
cierre; nunca se renumera. Autoridad de decision: Alvaro.

Creado: 2026-07-06 (al formalizar ADR-001).

===================================================================
ADR-001: Modelo de Componente por contratos compartidos (hibrido),
no por herencia de clase base unica.
===================================================================
Estado: Aceptado.
Fecha: 2026-07-06.

Contexto:
CE v5 es una plataforma multi-tipo (engines, workers, connectors,
plugins UI, auth/notification providers, exporters y tipos futuros)
que comparten patrones transversales (lifecycle, capacidades
declarativas, plugin discovery) pero con responsabilidades distintas.
El vocabulario ya estaba fijado (Componente raiz, Engine subtipo); lo
abierto era el MECANISMO estructural. En v4 no existia modelo raiz:
cada engine se instanciaba y cableaba a mano en main.py (16 engines =
16 modificaciones; DIFICULTADES sec.3, L4), con engines desconectados.
INFORME 8 ya modela lo transversal de forma uniforme (familia
component.*, manifest/capabilities, Clock declarado). La leccion L19
advierte de no forzar simetria entre componentes de naturaleza
distinta.

Decision:
"Componente" se define como un ROL por CONTRATOS, no como una clase
base a heredar. Se fijan contratos transversales neutrales que todo
componente satisface: Lifecycle (estados component.*, DAP-6),
Capabilities/Manifest (produce/consume, requires Clock, permisos,
config_schema; DAP-7) e integracion con el EventBus. Cada TIPO cumple
los contratos e implementa su comportamiento propio. Se ofrecen
mixins/base-helpers OPCIONALES para el boilerplate comun (p.ej. la
maquina de estados de lifecycle), no obligatorios. Implementacion en
Python via Protocol/ABC para el contrato y composicion sobre herencia.

Consecuencias:
Se gana: superficie transversal uniforme (discovery, lifecycle,
capacidades) que permite anadir componentes sin tocar un nucleo;
neutralidad de plataforma (la trading-ness vive solo en tipos
concretos, nunca en la raiz); aislamiento entre tipos divergentes;
extensibilidad aditiva para v5.1 (un tipo nuevo de dibujo/detector
solo implementa los contratos y declara manifest). Se acepta: riesgo
de boilerplate repetido (mitigado con mixins opcionales); necesidad de
definir pronto los contratos (DAP-6 y DAP-7); perdida del "unico sitio
donde mirar" de una clase base (mitigado: el manifest es el punto de
declaracion).

Alternativas consideradas:
A. Clase base Component unica con herencia de lifecycle y capacidades.
   Descartada: tiende a la clase-Dios que acreta conceptos de dominio,
   cara de tocar, y arriesga forzar simetria (contra L19).
B. Frameworks paralelos por tipo sin clase comun. Descartada: duplica
   codigo y reproduce el cableado por-tipo de v4; no escala.

Referencias:
Cierra DAP-8. INFORME 8 (component.*, manifest), INFORMES 2/5/6
(transversal), LECCIONES L4 y L19, DIFICULTADES sec.1-3. Cruza con
DAP-6, DAP-7, DAP-3, DAP-1. Es decision raiz que heredan DAP-16
(connector) y los notification providers de INFORME 4.

===================================================================
ADR-002: Monolito modular multiproceso (API + workers) sobre EventBus
externo, con costuras de extraccion a servicios; sin microservicios
en v5.0.
===================================================================
Estado: Aceptado.
Fecha: 2026-07-06.

Contexto:
CE v5 necesita fijar la forma macro de despliegue del backend con un
equipo de dos sin DevOps, para decenas de usuarios en v5.0 escalable a
miles sin refactor estructural (OBJ-2). La leccion de v4 (INFORME 2
sec.8) es que el problema no fue "ser monolito", sino el acoplamiento
sin contratos (bus informal, dashboard en proceso; L1, L2). Tres
decisiones ya firmes condicionan esta: REST-2 (UI fuera del proceso
del motor), INFORME 7 (API y workers como procesos separados, Docker
sin Kubernetes en v5.0) y DAP-17 (EventBus con transporte externo, no
in-process). Por tanto la opcion "un solo proceso" ya estaba
descartada; la eleccion real es monolito modular multiproceso frente a
microservicios desde v5.0.

Decision:
Se adopta un MONOLITO MODULAR MULTIPROCESO. "Monolito" significa un
unico codebase desplegable con los shared-contracts como frontera
unica (REST-4), NO un unico proceso. El runtime son pocos procesos:
API (peticiones) y worker(s) (evaluacion de reglas, notificaciones,
ejecucion, reconciliacion), separados por REST-2 e INFORME 7 y
comunicados por el EventBus externo (DAP-17). Los Componentes de DAP-8
viven como modulos; los modulos se comunican por contratos y eventos,
sin imports cruzados directos. Esa disciplina crea la COSTURA que
permite extraer un modulo caliente a servicio propio en el futuro sin
reescritura. Los microservicios no se adoptan en v5.0; quedan como
evolucion posible por modulo si una etapa futura lo exige.

Consecuencias:
Se gana: simplicidad operativa (un despliegue, un pipeline, un lugar
donde depurar), coste bajo para equipo de dos, y una costura limpia de
extraccion futura via contratos + bus. Se acepta: (a) hay que mantener
la disciplina de modulos o el monolito degrada como v4 (mitigado por
DAP-3 plugin discovery, DAP-7 capacidades y gates de CI); (b) un
despliegue unico implica que un mal deploy afecta a todo (mitigado por
gates de compatibilidad de schemas y migraciones expand-and-contract
de INFORME 7); (c) extraer un modulo a servicio, si llega el caso, es
trabajo real (mitigado: por la costura no es refactor estructural).

Alternativas consideradas:
B. Microservicios desde v5.0. Descartada: coste operativo alto desde
   el inicio (orquestacion, red, observabilidad distribuida, N
   despliegues), desproporcionado a la escala v5.0 y contra REST-14;
   es anti-patron explicito del CSA a esta escala.
Variante "un solo proceso" de A: descartada de facto por REST-2,
   INFORME 7 y DAP-17.

Referencias:
Cierra DAP-1. INFORME 2 (sec.8), INFORME 7 (procesos, hosting), DAP-17
(bus externo), DAP-8 (Componentes como modulos), DAP-3/6/7. REST-2/3/
4/14/15; L1, L2. Relacionado con ADR-001.

===================================================================
ADR-003: Envelope canonico unico.
===================================================================
Estado: Aceptado.
Fecha: 2026-07-06.

Contexto:
Cierra parte de DAP-2. En v4 el "bus" era un callback informal sin
contrato (L1); sin identidad estable no habia deduplicacion, replay ni
trazabilidad fiables. INFORME 8 sec.2 propone un envelope unico.

Decision:
Un envelope unico compartido por todos los eventos, con payload tipado
por tipo, en cuatro bloques mas payload:
- Identidad y tipo: event_id (UUID v4), event_type, envelope_version,
  event_schema_version, source.
- Identidad logica (separada de la fisica): idempotency_key (REQUIRED,
  identidad estable del hecho; dedup por ella), stream_key (REQUIRED),
  source_sequence (CONDITIONAL), source_event_id (CONDITIONAL).
- Alcance: scope (public_market|tenant|user|system); tenant_id
  condicional (obligatorio en tenant/user, prohibido en public_market,
  opcional en system); user_id si scope=user.
- Temporalidad (ranuras; semantica en DAP-4): event_time,
  ingestion_time, processing_time, time_anchor_ref.
- Linaje: correlation_id (REQUIRED), causation_id (CONDITIONAL).
- Payload: object tipado.

Consecuencias:
Deduplicacion, replay y tenancy limpios; base para consumer groups. Se
acepta mas metadatos por evento (compensado con payloads minimos) y
fijar la formula de idempotency_key por familia (de stream_key +
source_sequence si hay secuencia; de stream_key + event_time +
discriminador si no).

Alternativas consideradas:
B envelope minimo (dedup/scope al consumidor): descartada, reparte la
disciplina y repite v4. C sin envelope comun: descartada, cada familia
su forma.

Referencias:
Cierra parte de DAP-2. INFORME 8 sec.2; L1. Cruza DAP-4, DAP-10,
DAP-13, DAP-17. Relacionado con ADR-004/005/006.

===================================================================
ADR-004: Taxonomia de tipos de evento.
===================================================================
Estado: Aceptado.
Fecha: 2026-07-06.

Contexto:
Cierra parte de DAP-2. v4 mezclaba tipos genericos con campo "tipo". CE
v5 es plataforma: la taxonomia no puede girar en torno a trading
(criterio 4). INFORME 8 sec.3; execution.* aportada por INFORME 9.

Decision:
Naming dominio.accion, tipos especificos (no event_type=generic con
campo interno). Familias base CERRADAS: market.*, datasource.*, rule.*,
signal.*, alert.*, execution.*, notification.*, user.*, component.*,
billing.*. Gobernanza en dos niveles: (1) tipos nuevos dentro de una
familia se declaran en el manifest del componente (DAP-3/7) y
referencian su schema, que vive en shared-contracts (ADR-006) -el
manifest no sustituye al schema, lo referencia-; (2) familia nueva solo
por ADR o decision explicita de arquitectura. execution.* (INFORME 9)
es la familia de ejecucion multi-broker de la que depende DAP-16.
datasource.* generaliza el FeatureEvent de v4. signal.* es hija de
rule.*, no el eje.

Consecuencias:
Neutralidad de plataforma y extensibilidad gobernada (ni core rigido ni
taxonomia anarquica). Anadir capacidades no coordina despliegues del
core. Crear una FAMILIA nueva es una via deliberada (rara por diseno).

Alternativas consideradas:
B lista totalmente cerrada: descartada, cada tipo nuevo tocaria el core.
C totalmente abierta: descartada, sin gobierno.

Referencias:
Cierra parte de DAP-2. INFORME 8 sec.3, INFORME 9 (execution.*),
INFORME 4 (notification.*), INFORME 6 (DataSources). Cruza DAP-3/7,
DAP-13, DAP-16, DAP-17. Relacionado con ADR-003/006.

===================================================================
ADR-005: Versionado dual y evolucion de schemas.
===================================================================
Estado: Aceptado.
Fecha: 2026-07-06.

Contexto:
Cierra parte de DAP-2. En v4, sin versionado de schemas, cualquier
cambio rompia consumidores (L10). REST-20. INFORME 8 sec.5.

Decision:
Versionado dual independiente: envelope_version y event_schema_version
(por tipo) evolucionan por separado. Reglas de evolucion (envelope,
payloads y entidades persistidas): nunca renombrar ni retipar un campo
(anadir nuevo + deprecar viejo; expand-and-contract / tolerant reader);
campos nuevos con default; compatibilidad FULL (backward+forward) por
defecto en produccion; los schemas son codigo en git, revisados en PR,
con CHECK de CI que bloquea cambios incompatibles; entidades con
schema_version + migradores (migrar al cargar, nunca romper).

Consecuencias:
Evolucion segura sin coordinar despliegues (clave a escala; version skew
de INFORME 7). Se acepta el coste de mantener el CI de compatibilidad y
la disciplina de deprecacion.

Alternativas consideradas:
B version unica global: descartada, acopla la evolucion de envelope y
payloads. C solo tolerant reader sin versionado: descartada, insuficiente
para REST-20.

Referencias:
Cierra parte de DAP-2 (REST-20; cierra L10). INFORME 8 sec.5, INFORME 7
(version skew), INFORME 3 (entidades). Cruza ADR-006. Relacionado con
ADR-003/004.

===================================================================
ADR-006: Tecnologia de contrato y validacion.
===================================================================
Estado: Aceptado.
Fecha: 2026-07-06.

Contexto:
Cierra parte de DAP-2. Stack Python backend + TS frontend (REST-7),
shared-contracts como fuente unica (REST-4), decenas de usuarios en
v5.0, transporte tipo Redis Streams (INFORME 7), no Kafka/gRPC. INFORME
8 deja el formato como decision de stack.

Decision:
Autoria: modelos Pydantic v2 tipados en backend son la fuente; exportan
JSON Schema automaticamente; el JSON Schema vive en shared-contracts
como artefacto interoperable; de el se generan los tipos TypeScript del
frontend. Validacion: siempre en bordes externos (API, WebSocket
ingress, webhooks, conectores), siempre antes de publicar eventos
criticos al bus, siempre en CI/tests; en runtime interno, completa en
dev/test y selectiva/critica en produccion; tolerant reader al consumir.
No se adopta en v5.0 Avro/Protobuf como contrato principal ni schema
registry dedicado obligatorio; quedan como via al escalar.

Consecuencias:
Fuente unica (Pydantic) que genera contrato y tipos TS: menos piezas
para equipo de dos; escala a Etapa 3 con git+CI. Se acepta JSON mas
verboso que binario (irrelevante a esta escala). Nota de implementacion:
los modelos Pydantic-contrato viven en la capa shared-contracts o se
generan desde ahi (REST-4), no como detalle privado de un modulo.

Alternativas consideradas:
B JSON Schema como fuente con validadores por lenguaje: viable pero
pierde el idiomatismo Pydantic del backend. C Protobuf/Avro + registry:
descartada para v5.0 (peso desproporcionado, orientada a Kafka/gRPC).

Referencias:
Cierra parte de DAP-2. INFORME 8 sec.5/14, INFORME 2 (stack), INFORME 7
(transporte); REST-4/7/20. Cruza ADR-005. Relacionado con ADR-003/004.

===================================================================
ADR-007: Modelo temporal operativo (tres tiempos, Clock inyectado,
madurez y correcciones por familia).
===================================================================
Estado: Aceptado.
Fecha: 2026-07-06.

Contexto:
Cierra DAP-4 y el Bloque B (espina dorsal). En v4 el Clock fue
afterthought con time.time() disperso (L3): timestamps sin zona,
imposible reproducir; el refactor de Clock fue caro (26 ficheros,
DIFICULTADES). REST-19 exige modelo temporal explicito (Clock inyectado,
tres timestamps, UTC ms) desde el primer commit; REST-12 pide que el
backtesting futuro no obligue a refactor. INFORME 8 sec.6-7 lo propone.
DAP-2 (ADR-003) ya ratifico las ranuras temporales del envelope; DAP-4
decide su semantica.

Decision:
Contrato temporal operativo completo. Asignacion: event_time lo fija el
origen del hecho (heredado en derivados), nunca lo inventa el que
procesa; ingestion_time lo pone una vez el connector de borde y no se
sobreescribe aguas abajo; processing_time lo pone cada componente que
emite. Inmutabilidad: event_time e ingestion_time inmutables;
processing_time propio de cada emision. Herencia: los eventos derivados
declaran su ancla via time_anchor_ref. Formato canonico: UTC epoch
milliseconds (int64) en cable, resolucion al milisegundo; prohibidos
timestamps naive/locales; ISO 8601 UTC para display/logs; conversion a
zona del usuario en cliente. Clock/TimeProvider inyectado y DECLARADO en
el manifest (DAP-7) para todo componente que cree/transforme eventos,
procese ventanas, evalue reglas, notifique o calcule expiraciones;
prohibido time.time()/datetime.now() dispersos; habilita SimulatedClock
para backtesting sin tocar la logica. Madurez y correcciones:
maturity_state (provisional|closed|correction|reemission) se modela en
el schema de las familias que lo necesitan (market.*, datasource.*), NO
como campo universal del envelope; watermark por stream_key;
late_event_policy (accept|reject_after_watermark|route_to_correction) y
out_of_order_policy (reorder_by_sequence|best_effort|drop_older) por
stream/consumidor; una correction no muta el original (append-only),
emite evento nuevo que referencia el idempotency_key corregido y dispara
recomputo aguas abajo; velas: candle_updated / candle_closed /
candle_corrected.

Consecuencias:
Reproducibilidad, resiliencia a reconexion/replay/correcciones y base
para backtesting (SimulatedClock). Se acepta: todo componente temporal
debe recibir y declarar Clock (disciplina verificada por DAP-7/CI);
maturity_state y correcciones anaden logica de recomputo aguas abajo
(necesaria; su ausencia fue deuda en v4); la estrategia concreta de
watermark (fija|adaptativa) se dimensiona en operacion (INFORME 7).

Alternativas consideradas:
B tres timestamps sin contrato operativo: descartada, repite la deuda
conceptual de v4 con campos bonitos pero sin reproducibilidad. C un solo
timestamp: descartada, contradice REST-19 y las ranuras ya cerradas en
ADR-003.

Referencias:
Cierra DAP-4 (REST-19, REST-12; cierra L3). INFORME 8 sec.6-7, INFORME 6
(trigger candle_close), INFORME 7 (watermark). Cruza DAP-2 (ADR-003),
DAP-7, DAP-6, DAP-13, DAP-17, DAP-5. Relacionado con ADR-003.

===================================================================
ADR-008: Manifest de componente tipado y versionado como contrato de
capacidades.
===================================================================
Estado: Aceptado.
Fecha: 2026-07-06.

Contexto:
Cierra DAP-7 (primera del Bloque C). En v4 no habia declaracion: cada
engine se cableaba a mano en main.py (L4). ADR-001 fijo que el manifest/
capabilities es el contrato transversal que todo Componente satisface
(no solo los productores de DataSources); ADR-004 que el manifest
declara los tipos que produce/consume y referencia su schema en
shared-contracts; ADR-007 que declara la dependencia de Clock; ADR-006
que los contratos se autoran en Pydantic v2. INFORME 6 sec.12.2 define la
declaracion de DataSources; INFORME 5 separa autenticacion, autorizacion
y feature flags. CE-14: anadir componente = copiar carpeta + reiniciar.

Decision:
El manifest de componente es un modelo Pydantic v2 tipado y validado
(ComponentManifest), con manifest_schema_version propio (evoluciona bajo
ADR-005). Pydantic es la fuente de autoria/validacion y exporta JSON
Schema (ADR-006); el manifest se serializa a artefacto JSON/YAML para que
DAP-3 decida el mecanismo fisico de discovery, sin obligar a importar
codigo arbitrario. Campos: identity (id, version, manifest_schema_version,
type -enum ABIERTO como vocabulario controlado y validado por schema,
extensible via versionado del manifest, no string libre-); produces/
consumes (referencian schemas en shared-contracts, ADR-004); requires
(Clock/TimeProvider, DB, EventBus, servicios, componentes o capacidades);
capabilities (bloque generico extensible: datasources,
notification_channels, connector_capabilities, ui_capabilities,
exporter_capabilities, auth_capabilities, execution_capabilities,
custom_capabilities kind+schema_ref+version); capabilities.datasources
como capability especializada estandar (declaracion de INFORME 6 sec.12.2
con shared_evaluation, sharing_scope, cache_key_schema y unidades de
historia); ui (panel, widget, config_screen, supported_surfaces);
policy_requirements (permissions_required, feature_flags_required,
entitlements_required, sensitive_capabilities); config_schema (JSON
Schema del config). Validacion en dos capas: estatica en discovery
(estructura, campos requeridos, schemas referenciados, capabilities bien
formadas) y semantica en registro/runtime (dependencias resolubles, Clock
si es temporal, permisos/flags validos, servibilidad coherente). Minimo
obligatorio: id, version, manifest_schema_version, type; resto
obligatorio-si-aplica.

Consecuencias:
Permite construir el grafo de dependencias y validar el cableado
automaticamente, escalando a decenas de componentes sin registro central
manual (cierra L4). Superficie de capacidades neutral y extensible que
sirve a todos los tipos de Componente, no solo a DataSources (respeta
ADR-001 y el criterio 4). DataSource queda como capability especializada,
critica para DAP-13/15 pero no como eje. policy_requirements separa lo
que el componente NECESITA (permisos, flags, entitlements, capacidades
sensibles) de como se evalua (DAP-11). Se acepta: disciplina de mantener
el manifest fiel al comportamiento (mitigado: es la declaracion tipada
del propio codigo); el formato del manifest tiene su propio versionado.

Alternativas consideradas:
B YAML/JSON suelto escrito a mano: util solo como artefacto generado; a
mano deriva del codigo. C introspeccion/decoradores sin manifest
explicito: descartada, repite v4 (el sistema descubre lo que el codigo
hace, no lo que declara contractualmente).

Referencias:
Cierra DAP-7. INFORME 2/5/6/8; CE-14; L4. Cruza DAP-3 (discovery), DAP-6
(lifecycle), DAP-8 (rol Componente), DAP-2/ADR-004, DAP-4/ADR-007,
DAP-13/15, DAP-11, DAP-9, DAP-16. Relacionado con ADR-001, ADR-006.

===================================================================
ADR-009: Plugin discovery por convencion de carpetas + manifest
declarativo.
===================================================================
Estado: Aceptado.
Fecha: 2026-07-06.

Contexto:
Cierra DAP-3 (segunda del Bloque C). En v4 no habia discovery: los
engines se instanciaban a mano en main.py (L4), y cada engine nuevo
exigia tocar varios puntos. CE-14 fija el objetivo de DX: anadir
componente = copiar carpeta + reiniciar. ADR-008 dejo el manifest como
artefacto serializable que se lee sin importar codigo arbitrario; ADR-002
puso el sistema en un monolito modular multiproceso. INFORME 2 sec.9
(el stack soporta convencion de carpetas) e INFORME 6 sec.12.3 (los
componentes productores publican sus DataSources al catalogo por
discovery) aportan.

Decision:
Plugin discovery por CONVENCION DE CARPETAS + manifest declarativo
(opcion C) como mecanismo principal, con entry points de terceros
(opcion D) como extension declarada, no obligatoria en v5.0. Cada
componente vive en components/<nombre>/ con su manifest (JSON/YAML) y su
entrypoint. El discovery escanea al arranque y, por cada carpeta: lee el
manifest, lo valida (capa estatica de ADR-008), registra el componente,
publica sus DataSources al catalogo y solo despues carga explicitamente
el entrypoint declarado; nunca importa codigo arbitrario para saber que
un componente existe. Sin hot-reload en v5.0: el descubrimiento ocurre
al arranque/reinicio (coherente con CE-14); el ciclo en caliente
(activar/pausar/reiniciar un componente sin tumbar el sistema) es DAP-6.
Reglas de implementacion: manifest invalido no importa codigo; id o
version duplicados son error de discovery; en CI/dev el manifest
invalido falla fuerte; en produccion la politica fail-fast vs quarantine
se coordina con DAP-6; el resultado del discovery es observable
(descubiertos, registrados, rechazados y motivo).

Consecuencias:
DX simple (copiar carpeta + reiniciar), arranque predecible y testeable,
superficie de seguridad acotada (solo carpetas bajo la raiz confiable,
manifest validado antes de tocar codigo), catalogo de DataSources
poblado automaticamente. Cierra L4. Se acepta: sin hot-reload en v5.0
(anadir/quitar componente pide reinicio del worker afectado, aceptable a
esta escala); la convencion de carpetas exige disciplina de estructura
(mitigada por la validacion del manifest); el soporte de terceros por
entry points queda declarado, no cerrado en v5.0.

Alternativas consideradas:
A entry points en pyproject.toml como principal: descartada para v5.0
(empaquetar/reinstalar es friccion innecesaria); util solo como
extension para wheels de terceros. B decoradores + auto-import:
descartada, vuelve a hacer discovery importando codigo, contra el
criterio de ADR-008 (leer manifest declarativo antes de importar).

Referencias:
Cierra DAP-3. INFORME 2 sec.9, INFORME 6 sec.12.3; CE-14; L4. Cruza
DAP-7/ADR-008 (manifest), DAP-6 (lifecycle), DAP-8 (rol Componente),
DAP-13/15 (catalogo), DAP-11. Relacionado con ADR-008, ADR-002.

===================================================================
ADR-010: Lifecycle de ComponentInstance.
===================================================================
Estado: Aceptado.
Fecha: 2026-07-06.

Contexto:
Cierra DAP-6 y el Bloque C (sustrato de componentes). En v4 no habia
lifecycle: los engines se instanciaban en main.py y vivian hasta morir
el proceso (L4); no se podia activar/desactivar ni reiniciar uno sin
tumbar el sistema. ADR-001 fijo Lifecycle como contrato transversal del
rol Componente; ADR-009 dejo REGISTER como salida del discovery y
remitio aqui la politica de fallo en produccion; INFORME 5 sec.9
evidencia componentes de ambito por-usuario (connectors BYOC, gestor de
API keys) con geo-blocking como gate previo a INITIALIZE.

Decision:
Se distingue ComponentDefinition (lo que DAP-3 descubre desde carpeta +
manifest; global; component_id, version, type, capabilities) de
ComponentInstance (objeto vivo de runtime; component_instance_id;
lifecycle_scope global|tenant|user; tenant_id/user_id si aplica). El
lifecycle se aplica a la Instance. DAP-3 registra Definitions; el
supervisor de DAP-6 registra Instances creadas desde ellas; el estado
REGISTERED de la maquina de DAP-6 se refiere a la Instance registrada en
el supervisor, no al mero descubrimiento de la Definition. Maquina de
estados principal (pequena), gestionada por un supervisor/registry
central: REGISTERED -> INITIALIZING -> INITIALIZED -> STARTING ->
RUNNING <-> PAUSED -> STOPPING -> STOPPED -> UNLOADED, mas FAILED y
QUARANTINED. La salud se modela aparte: health_status (healthy|degraded|
unhealthy) y readiness_status (ready|not_ready); DEGRADED no es estado
de lifecycle. Cada transicion emite un evento component.* (ADR-004) con
envelope (ADR-003) y Clock (ADR-007), identificando la instancia
(component_id, component_version, component_instance_id, lifecycle_scope,
tenant_id/user_id, previous_state, new_state, health_status,
readiness_status, reason/error_code). El mismo contrato sirve a
instancias globales y por-usuario/tenant, que se instancian/paran por
(tenant,user); el gate geo/plan (DAP-11) actua antes de INITIALIZE.
PAUSE detiene el consumo y conserva registro y offset, sin buffer
ilimitado; el hueco se recupera por replay desde el offset. Las
dependencias se resuelven por el grafo de requires del manifest
(arranque topologico; obligatoria caida -> PAUSED/FAILED por politica de
arista; opcional caida -> RUNNING + health_status=degraded). Fallo en
INITIALIZE -> rollback de registros parciales y FAILED; en CI/dev
fail-fast; en produccion quarantine por defecto con reintentos backoff
acotado; instancia critica puede declararse fail-fast en su manifest.

Consecuencias:
Runtime controlable, observable y resiliente, con identidad de instancia
inequivoca (clave para connectors BYOC por-usuario) y una maquina
principal pequena. Cierra L4. Se acepta: el supervisor/registry es
infraestructura nueva (corazon del sustrato); la recuperacion por replay
exige consumidores idempotentes (garantizado por idempotency_key de
ADR-003); quarantine, backoff y health/readiness anaden logica de
supervision (proporcional a operar always-on workers de INFORME 7).

Alternativas consideradas:
B lifecycle ad-hoc por tipo: descartada, reintroduce fragmentacion
(connector, engine y notification provider se comportarian distinto ante
fallos). C sin lifecycle en runtime: descartada, repite v4 (instanciar
al arranque y vivir hasta que muera el proceso).

Referencias:
Cierra DAP-6 y el Bloque C. INFORME 2 sec.9, INFORME 5 sec.9, INFORME 7
(always-on), INFORME 8; L4. Cruza DAP-7/ADR-008, DAP-3/ADR-009,
DAP-8/ADR-001, DAP-2/ADR-003-004, DAP-4/ADR-007, DAP-11, DAP-17, DAP-16.
Relacionado con ADR-001, ADR-008, ADR-009.

===================================================================
ADR-011: Modelo de tenancy multiusuario (shared-schema + RLS).
===================================================================
Estado: Aceptado.
Fecha: 2026-07-06.

Contexto:
Cierra DAP-10 (primera del Bloque D). OBJ-2 exige datos aislados por
tenant desde el dia uno, escalable a miles sin refactor. En v5.0 el
tenant coincide 1:1 con el usuario (B2C), pero la arquitectura no asume
esa equivalencia como eterna: tenant es una abstraccion. ADR-003 dejo
scope=public_market|tenant|user|system con tenant_id condicional;
ADR-010 dejo el gate de policy previo a INITIALIZE. INFORME 5 aporta los
tres modelos de aislamiento y los gotchas de RLS verificados 2026.
Frontera: que jurisdicciones/planes existen y la obligatoriedad de KYC
son politica de Alvaro.

Decision:
Shared-schema + RLS (opcion A) como modelo base, con db-per-tenant
(hibrido, opcion D) como via futura declarada no construida en v5.0.
tenant_id en toda entidad por-tenant; tenant como abstraccion con
pertenencia user -> tenant (user_tenant_membership) como capa aparte. El
tenant efectivo lo resuelve exclusivamente el backend mediante un
TenantContextResolver a partir de la sesion autenticada y la pertenencia;
el cliente nunca lo impone; se fija app.current_tenant_id con SET LOCAL
dentro de la transaccion; sin pertenencia valida la operacion falla
cerrada. Toda tabla persistida declara isolation_scope (public_market|
tenant|user|system), alineado con el scope del envelope. Disciplina RLS
obligatoria: SET LOCAL transaccional; rol de app sin BYPASSRLS ni
SUPERUSER; el rol de migraciones no corre en runtime; checks de CI que
fallan si una tabla tenant/user no tiene tenant_id, si una tabla user no
tiene user_id/owner_user_id cuando aplique, si una tabla tenant/user no
tiene RLS, si una tabla sin tenant_id no esta allowlisted como
public_market/system, o si una policy RLS no usa el tenant context
transaccional; tests de aislamiento en CI en cada build; claves de cache
con tenant_id; invalidacion de caches derivadas de rol/premium/
jurisdiccion/KYC (mecanismo concreto en DAP-11). Como defensa en
profundidad, filtrado por tenant en la capa de aplicacion ademas de RLS.
Nota de implementacion: en v5.0, user_tenant_membership puede
inicializarse automaticamente con una pertenencia unica por usuario; no
implica soportar organizaciones en producto, solo deja preparada la
costura.

Consecuencias:
Menor coste operativo (una base, migraciones simples, buena densidad de
tenants), escala a decenas->miles sin refactor, y via limpia a
organizaciones futuras sin reescribir el aislamiento. Cierra los huecos
tipicos de fuga (tenant impuesto por cliente; tabla nueva sin tenant_id).
Se acepta: RLS + resolver + clasificacion arrastran disciplina estricta
obligatoria; db-per-tenant queda declarada no construida; RLS puede
complicar debugging de politicas ricas (mitigado con filtrado de app).

Alternativas consideradas:
B schema-per-tenant: descartada para v5.0 (migraciones caras, el catalogo
sufre con cientos/miles de schemas). C database-per-tenant: descartada
para v5.0 (aislamiento maximo pero coste y operacion desproporcionados
para B2C inicial); reservada, via D, a un futuro cliente enterprise/
white-label que exija aislamiento fisico.

Referencias:
Cierra DAP-10. INFORME 5 sec.1-2, INFORME 7; L8, REST-5, OBJ-2. Cruza
DAP-11 (flags tenant-scoped; invalidacion de caches), DAP-2/ADR-003
(scope/tenant_id), DAP-6/ADR-010 (gate; BYOC por-usuario), DAP-16
(ApiKey/ExecutionProfile), DAP-5 (streams privados). Relacionado con
ADR-003, ADR-010.

===================================================================
ADR-012: Feature flags como PolicyEvaluator central de plataforma.
===================================================================
Estado: Aceptado.
Fecha: 2026-07-06.

Contexto:
Cierra DAP-11 y el Bloque D (plataforma multiusuario). Los feature
flags son transversales (geo-blocking, premium, widgets, capacidades,
endpoints, permisos, dashboard); sin un modelo unico, INFORME 3/4/7/9
divergirian. ADR-011 dejo los flags resueltos como entidades tenant-
scoped y remitio aqui la invalidacion de caches derivadas; ADR-010
aplica el flag como gate previo a INITIALIZE; ADR-008 hizo que el
manifest declare feature_flags_required. REST-16 exige enforcement
backend (no solo UI); INFORME 5 sec.5 pide jerarquia de confianza IP+
KYC+VPN; INFORME 7 aporta el kill switch jerarquico; INFORME 9 exige
Execution Gate fail-closed. Frontera: que jurisdicciones/planes y la
obligatoriedad de KYC son politica de Alvaro.

Decision:
Feature flags mediante un PolicyEvaluator central propio (opcion A),
con servicio de terceros (LaunchDarkly/Unleash/Flagsmith) solo como
posible backend futuro no requerido en v5.0. El evaluador resuelve por
sujeto (tenant/usuario) un capability set consumido por API y UI: la UI
oculta/deshabilita como cortesia y el capability set que consume es
INFORMATIVO; la decision autoritativa es siempre la reevaluacion/
validacion backend en el endpoint sensible. Entradas: jurisdiccion
(IP+KYC con jerarquia de confianza configurable), IP/VPN, rol/plan,
entitlements, overrides, kill switches y config. Salida: decisiones por
capability ALLOW|DENY|NOT_APPLICABLE con reason_code y policy_version.
La resolucion no es suma de flags positivos: para capacidades sensibles
(connect_broker, execute_order, activate_autotrade, manual_order,
manage_api_key) cualquier DENY activo (kill switch, jurisdiccion, KYC no
valido, plan insuficiente, entitlement ausente, policy no disponible o
cache stale) prevalece sobre cualquier ALLOW inferior; los overrides
tenant/user solo conceden dentro del perimetro permitido por politicas
superiores. Kill switch jerarquico como entrada de primera clase con
scopes global, exchange, connector, tenant/user, market_scope y
capability (union de bloqueos activos; DENY gana en el mismo nivel; un
scope amplio bloquea inferiores; propagacion por evento sin reinicio;
auditado). Herencia plan -> tenant -> usuario (tenant-scoped, RLS de
ADR-011); reglas y overrides son datos versionados (ADR-005). Cache del
capability set con tenant_id, user_id, policy_version, input_versions y
evaluated_at; invalidacion por evento como mecanismo principal y
max_staleness/TTL acotado como red de seguridad; en endpoints sensibles,
capability set expirado, stale, de policy_version no vigente o no
recomputable resuelve DENY (fail-closed); en capacidades no sensibles se
admite degradar con cache stale si la politica lo declara, nunca para
ejecucion, API keys, autotrade ni acciones financieras. Enforcement en
backend a nivel API en todo endpoint sensible; auditoria de evaluaciones
y bloqueos sensibles (SensitiveActionAudit). La politica concreta de
jurisdicciones, premium y KYC se modela como datos configurables de
Alvaro, no como codigo.

Consecuencias:
Coherencia unica backend/UI, geo-blocking defendible a nivel API, kill
switch operativo con propagacion inmediata, y extensibilidad de
capacidades (premium, widgets, dibujo avanzado, pattern detection) sin
modelo nuevo. Se acepta: el evaluador central + cache/invalidacion +
kill switch es infraestructura transversal a construir; fail-closed
puede denegar de mas ante fallo o staleness (correcto en endpoints
sensibles); la fiabilidad de la jerarquia de confianza depende del
proveedor de deteccion VPN/KYC (seleccion de Alvaro).

Alternativas consideradas:
B flags dispersos por modulo: descartada, reintroduce divergencia (cada
endpoint/widget/worker resolveria permisos distinto). C servicio externo
de flags como motor principal: no recomendada para v5.0; puede ser
backend futuro, pero el contrato debe ser propio de CE v5, no una
dependencia estructural inicial.

Referencias:
Cierra DAP-11 y el Bloque D. INFORME 5 sec.2/5/7, INFORME 3 (G2/G3),
INFORME 7 (kill switch), INFORME 9 (Execution Gate); REST-16, OBJ-1/9.
Cruza DAP-10/ADR-011, DAP-6/ADR-010, DAP-7/ADR-008, DAP-2/ADR-004-005,
DAP-17, DAP-16, DAP-9, DAP-12. Relacionado con ADR-011.

===================================================================
ADR-013: Sustrato operativo de EventBus, colas y workers.
===================================================================
Estado: Aceptado.
Fecha: 2026-07-06.

Contexto:
Cierra DAP-17 (primera del Bloque E). En v4 el bus informal en proceso
(_bus(ev)) fue deuda estructural: acoplamiento, sin trazabilidad, sin
reintentos (L1). ADR-002 puso API y workers como procesos separados: el
bus no puede ser memoria local. ADR-003 dio idempotency_key/stream_key;
ADR-007 replay por offset y watermark; ADR-010 replay en PAUSE; ADR-012
kill switch por el bus. REST-3 exige EventBus formal; REST-15 pide
cambiar de backend sin tocar productores/consumidores. INFORME 2 dio la
comparativa de brokers 2026; INFORME 7, las capacidades operativas.

Decision:
Sustrato con transporte EXTERNO (opcion A) con capacidades operativas
obligatorias, independiente del broker: at-least-once, acks, retries con
backoff, DLQ observable, backpressure, consumer groups, ordering por
stream_key, particionado (por stream_key o tenant), equivalente local en
docker-compose y metricas. Productores y consumidores usan una
abstraccion propia EventBus/Queue (no la API nativa del broker; REST-15),
y el broker es una capability/Componente con manifest y lifecycle. Redis
Streams es el transporte de v5.0 (Kafka descartado por sobredimensionado;
NATS/JetStream candidato futuro con cautela por gobernanza CNCF/BSL y
hallazgos Jepsen; PG LISTEN/NOTIFY insuficiente). Politica de retencion/
replay/trimming por familia: Redis Streams es transporte operativo de
corto/medio plazo y el historico canonico persistente vive en la DB
append-only; el trimming es seguro solo por ventana suficiente o tras
avance de watermark/ack; un offset ya eliminado se reconstruye desde
fuente canonica o la instancia entra en FAILED/QUARANTINED observable,
nunca avanza en silencio. Idempotencia real de consumidor: idempotency_key
es identidad logica, no garantia automatica; todo consumidor con efectos
persistentes registra su procesamiento por consumer_group/handler/
idempotency_key o usa constraints/upserts, y hace ACK solo tras persistir
el efecto. Patron outbox/inbox: los eventos que nacen de una transaccion
de DB se escriben primero en una outbox transaccional en la misma DB, un
publisher worker los publica y marca enviado idempotentemente, y los
consumidores con efectos aplican inbox/dedup antes del ACK, garantizando
at-least-once end-to-end DB-bus sin Kafka ni event sourcing. La entrada de
DLQ incluye owner operativo, reason_code, numero de intentos,
first_seen_at, last_seen_at y procedimiento de reproceso.

Consecuencias:
Trazabilidad, reintentos, ordering, replay seguro, consistencia DB-bus y
capacidad de migrar de broker sin tocar componentes. Cierra el bus
informal de v4. Se acepta: transporte externo + outbox + ledgers de
idempotencia es infraestructura a operar; Redis Streams es memory-bound
(mitigado con politica de retencion, historico canonico en DB y
abstraccion para migrar); at-least-once obliga a que todo consumidor con
efectos sea idempotente (disciplina obligatoria apoyada en ADR-003).

Alternativas consideradas:
B bus in-process: descartada, reproduce la deuda de v4 y contradice
ADR-002 (API y workers son procesos separados, el bus no puede ser
memoria local).

Referencias:
Cierra DAP-17. INFORME 7 (operacion), INFORME 2 (brokers), INFORME 8
(contratos); REST-3/4/15, L1. Cruza DAP-2/ADR-003, DAP-4/ADR-007, DAP-5,
DAP-6/ADR-010, DAP-7/ADR-008, DAP-10/ADR-011, DAP-11/ADR-012, DAP-16.
Relacionado con ADR-002, ADR-003.

===================================================================
ADR-014: Streams de market data hibridos.
===================================================================
Estado: Aceptado.
Fecha: 2026-07-06.

Contexto:
Cierra DAP-5 y el Bloque E (transporte operativo). El problema de
escala: N usuarios x M pares explota en conexiones WebSocket por
exchange si se ingiere por-usuario. Ya cerrado condiciona: ADR-013
(transporte/retencion/ordering; stream_key), ADR-011 (public_market vs
user; publicos no se duplican por tenant), ADR-012 (geo-gate reduce
privados), ADR-003 (scope y stream_key), ADR-007 (candle_corrected,
watermark), ADR-010 (ingestor/connector como Componentes). INFORME 4:
la evaluacion de alertas/reglas vive en backend y corre 24/7 aunque la
PWA no este abierta. INFORME 5 sec.9 e INFORME 7 marcaban el hibrido
como candidato. INFORME 9 aporta los fills privados (DAP-16). Frontera:
que exchanges y en que jurisdicciones se habilita lo privado es politica
de Alvaro.

Decision:
Streams hibridos (opcion C). Datos PUBLICOS compartidos cross-tenant
(scope=public_market, sin tenant_id), un stream por flujo publico
identificado por MarketStreamKey = exchange + instrument/symbol +
data_family + granularidad aplicable (timeframe para candles, depth/
channel para orderbook, tipo para trades/ticker); el stream_key del
envelope se deriva de MarketStreamKey de forma determinista. Datos
PRIVADOS (execution.*/fills/balance) por-usuario (scope=user, tenant_id
+user_id, RLS), solo para BYOC en jurisdiccion habilitada por policy/
geo-gate. La demanda de suscripcion se agrega en un MarketInterestRegistry
mediante SubscriptionIntents procedentes de watchlists, widgets/layouts,
AlertRules, TradingSignalRules, ExecutionPlans, DataSources y tareas de
backfill/replay (y detectores v5.1); el subscription manager calcula la
union y deriva ref-counts runtime por MarketStreamKey, con histeresis
anti-flapping. El ref-count es estado operativo reconstruible, no fuente
de verdad: tras reinicio, las suscripciones deseadas se reconstruyen
desde entidades persistidas y reglas activas. Ingestor publico y
connector privado son Componentes con manifest y lifecycle. Reconexion
por bootstrap REST + replay/retencion del sustrato y candle_corrected;
fault isolation por stream; historico canonico en DB. Nota de
implementacion: cada SubscriptionIntent incluye source_type, source_ref,
MarketStreamKey, priority, created_at/updated_at y opcionalmente
lease_ttl (los intereses persistentes de reglas/alertas no dependen de
TTL; los efimeros de widgets pueden caducar para evitar suscripciones
zombis).

Consecuencias:
Coste de conexion reducido de usuarios x pares a pares/flujos publicos
unicos + usuarios con broker privado activo; escalabilidad a beta/
producto sin rediseno de ingesta; los evaluadores backend (alertas/
reglas) no quedan infra-suscritos; aislamiento de los privados por RLS y
geo-gate. Se acepta: el MarketInterestRegistry + subscription manager
con reconstruccion tras reinicio es pieza a construir; los publicos
compartidos son punto de agregacion (mitigado con reconexion, replay y
fault isolation por MarketStreamKey); el fair-use/limite de conexiones
por exchange se dimensiona en operacion (INFORME 7).

Alternativas consideradas:
A streams compartidos para todo: correcta para publico, insuficiente
para fills/balance/ordenes BYOC. B streams por-usuario: descartada,
reproduce la explosion N usuarios x M pares x exchanges que DAP-5 debe
evitar.

Referencias:
Cierra DAP-5 y el Bloque E. INFORME 5 sec.9, INFORME 4 (reglas backend
24/7), INFORME 7, INFORME 9. Cruza DAP-17/ADR-013, DAP-10/ADR-011,
DAP-11/ADR-012, DAP-2/ADR-003, DAP-4/ADR-007, DAP-6/ADR-010, DAP-13,
DAP-16, DAP-9. Relacionado con ADR-013, ADR-011.

===================================================================
ADR-015: Motor de reglas unificado (raiz Rule neutral, dos productos).
===================================================================
Estado: Aceptado.
Fecha: 2026-07-06.

Contexto:
Cierra DAP-13 (primera del Bloque F). INFORME 6 + decisiones A1/A2 de
Alvaro + 4 revisiones CSA. v4 tenia gramatica rigida (3 grupos fijos).
A1: alerta y tripleta son dos productos (avisar vs senalar trading con
overlay universal; el geo-blocking corta ejecucion, no visualizacion).
A2: la regla fractal 3-dominios es restriccion sin valor. Ya cerrado
condiciona: ADR-004 (rule.*/signal.*/alert.*; signal.* hija de rule.*),
ADR-003 (envelope, causation_id; DAP-2 dejo la proyeccion rule/signal/
alert para cerrar aqui), ADR-007 (transicion de estado, event_time),
ADR-008 (DataSource como capability), ADR-011/012 (tenant, RLS, limites
por plan, gate), ADR-014 (reglas activas -> SubscriptionIntent).

Decision:
Una sola maquinaria con raiz Rule NEUTRAL sin campos de mercado
(target_binding) y dos productos v5.0 como especializaciones (AlertRule
y TradingSignalRule, con market_scope en la hoja). Estructura: 1..N
grupos multi-contexto, cada grupo con 1..M features y cada feature con
1..K condiciones (max 3 fuentes distintas), veto guardian opcional y
combinacion por niveles. rule.* es la fuente de verdad neutral del
evaluation lifecycle (rule.evaluation_completed, rule.firing,
rule.resolved); signal.* y alert.* son proyecciones derivadas con
causation_id hacia rule.* (TradingSignalRule -> signal.*; AlertRule ->
alert.*); alert.acknowledged pertenece solo al attention/delivery
lifecycle; historial y deduplicacion se anclan en rule.*. El veto es un
bloque guardian OR opcional que bloquea la transicion a FIRING, no
dispara por si mismo y, mientras esta activo, impide proyectar signal.*/
alert.* (dejando veto_matched/veto_reason/nodos para trazabilidad).
trigger_policy = candle_close | event_arrival | schedule | manual |
mixed. La combinacion se persiste en forma canonica de alcance declarado
(normalizador con catalogo explicito de transformaciones y hash estable;
sin equivalencia semantica arbitraria); el pipeline que la compila es
DAP-15. Emision por transicion de estado (FIRING/RESOLVED); doble ciclo
evaluation/attention con attention_termination_policy por producto/canal.
Funciones canonicas neutrales (value_at/previous_value, average, change,
is_active, elapsed_since) con unidad de historia por evaluation_context o
tipo de DataSource; naming textual final en DAP-14. Reglas como datos
JSON versionados por-tenant, no-Turing-complete, con DataSource
declarativa (la Rule no conoce observables directos). Limites y
complexity budget gobernados por plan y por hard caps de plataforma
(N<=5, M<=3, K<=5, 3 fuentes/feature, tope de nodos booleanos y de
SubscriptionIntents derivados), validados antes de persistir o compilar
(DAP-13 define que canon es admisible; DAP-15 solo lo compila). Los
nombres exactos de event_type se cierran en shared-contracts con
consistencia gramatical, sin mezclar estado y accion.

Consecuencias:
UNA maquinaria (no dos motores) que sostiene AlertRule y TradingSignalRule
sin duplicar parser, normalizador, evaluator, historial ni catalogo de
DataSources; la raiz neutral protege el criterio 4 (trading en hoja/
proyeccion, no en la raiz); la proyeccion explicita evita motores
encubiertos; el veto guardian elimina la ambiguedad de tratarlo como
grupo booleano; el complexity budget impide reglas formalmente validas
pero operativamente caras. Se acepta: la forma canonica y el normalizador
con catalogo explicito son diseno acotado a proposito; una gramatica
canonica unica obliga a la localizacion (DAP-14) y al pipeline (DAP-15);
los hard caps son restricciones de producto conscientes ajustables por
plan.

Alternativas consideradas:
B dos motores separados con lenguajes propios: descartada, duplica logica
y rompe la convergencia alerta/senal. C estructura v4 fija de 3 grupos:
descartada por decision A2 de Alvaro (restriccion sin valor).

Referencias:
Cierra DAP-13. INFORME 6 (sec.10-11), INFORME 4 (AlertRule), INFORME 8
(normalizador, hash, schema, proyeccion). Cruza DAP-14, DAP-15,
DAP-2/ADR-003-004, DAP-4/ADR-007, DAP-7/ADR-008, DAP-10/11/ADR-011-012,
DAP-16, DAP-5/ADR-014, DAP-9. Relacionado con ADR-003, ADR-004.

===================================================================
ADR-016: Localizacion del lenguaje de reglas por canonico unico.
===================================================================
Estado: Aceptado.
Fecha: 2026-07-06.

Contexto:
Cierra DAP-14 (segunda del Bloque F). En v4 las keywords Y las funciones
del DSL estaban en espanol: texto hardcodeado en un idioma en el corazon
del producto, justo lo que REST-13 prohibe. Decision A3 de Alvaro: el
lenguaje no puede ser solo espanol (producto global ES/EN/FR v5.0, AR
v5.1, ZH v5.2). Ya cerrado condiciona: ADR-015 (funciones canonicas
neutrales, reglas como datos, DSL derivado no critico), ADR-008
(display-names de DataSources via catalogo/manifest), ADR-005 (schema
versionado). El flujo chatbot-genera-estructura (INFORME 6 sec.14)
reduce el peso de la superficie textual.

Decision:
Localizacion por canonico unico (opcion A). El canon del lenguaje
(keywords estructurales y funciones canonicas de ADR-015) se fija en
ingles como IDENTIFICADORES internos estables, no como texto de UI: es
la unica gramatica objetivo del chatbot, validador, normalizador y
compilador. La superficie principal de creacion es el chatbot
multiidioma, que produce forma canonica desde una descripcion en el
idioma del usuario; las explicaciones se renderizan en el idioma del
usuario de forma determinista desde la forma canonica, y el DSL textual
en ingles sobrevive como representacion derivada opcional (experto,
exportacion, documentacion), fuera del camino critico. La localizacion
vive en la capa de renderizado con catalogos i18n (keys de traduccion),
no en N parsers por idioma, lo que hace el sistema RTL-ready (AR) y
CJK-ready (ZH) sin refactor: anadir idioma es anadir catalogo y activar.
Los identificadores que el usuario da a sus reglas/grupos son texto libre
Unicode con normalizacion anti-colision (p.ej. NFC + defensa contra
homoglifos/confusables), tratados como datos y nunca como keywords. Los
display-names de las DataSources se traducen via catalogo declarado en el
manifest (ADR-008), mientras sus ids permanecen canonicos y estables. Los
errores de validacion, warnings, diagnostics y reason_codes del chatbot/
validador se emiten como code + params y la UI los renderiza por i18n,
nunca como texto hardcodeado.

Consecuencias:
Una sola gramatica y un solo renderizador, chatbot mas fiable (un unico
objetivo) y RTL/CJK-ready sin refactor de codigo. El ingles funciona como
convencion de identificadores tecnicos, no como idioma de producto, por
lo que no contradice REST-13. Se acepta: el usuario experto que use el
DSL lo vera en ingles (la superficie principal es el chatbot en su idioma
+ explicaciones renderizadas); el renderizado localizado y los catalogos
i18n son trabajo continuo de traduccion (ya obligatorio por REST-13). La
opcion B (lexico localizable por idioma) queda como via futura que no
rompe reglas guardadas, porque el canonico persiste igual.

Alternativas consideradas:
B lexico localizable multi-idioma sobre el canonico: descartada para v5.0
(N parsers, ambiguedades por idioma, keywords RTL/CJK no triviales,
errores de round-trip, para una superficie que ya no es el camino
critico). C solo espanol: descartada, repite la deuda de v4 y contradice
A3/REST-13.

Referencias:
Cierra DAP-14. INFORME 6 sec.14-15 (sec.10.9 funciones), REST-13,
decision A3. Cruza DAP-13/ADR-015, DAP-15, DAP-7/ADR-008, DAP-8,
DAP-11/ADR-012, DAP-12, DAP-9. Relacionado con ADR-015, ADR-008.

===================================================================
ADR-017: Pipeline de compilacion de Rules (Execution Plan derivado).
===================================================================
Estado: Aceptado.
Fecha: 2026-07-06.

Contexto:
Cierra DAP-15 y el Bloque F (motor de reglas). La solicito el CSA en las
revisiones de INFORME 6. v4 interpretaba el AST en cada evaluacion; a
escala SaaS (miles de reglas x usuarios x fuentes) cuesta CPU y dinero.
El motor no debe quedar atado al cierre de vela (ADR-015: trigger_policy)
ni la cache a claves de mercado (cada DataSource declara shared_evaluation
/sharing_scope/cache_key_schema, ADR-008). Ya cerrado condiciona: ADR-015
(forma canonica, hash, complexity budget ya validado; DAP-13 define que
canon es admisible, DAP-15 solo lo compila), ADR-016 (pipeline agnostico
al idioma), ADR-013/014 (triggers y datos), ADR-010 (fallo observable).

Decision:
Compilacion forma canonica -> AST -> Execution Plan -> runtime (opcion
B), con implementacion minima viable en v5.0. El Execution Plan es una
cache derivada reconstruible desde el canon y desde los catalogos/
manifests versionados, nunca fuente de verdad. Los planes se agrupan en
lotes indexados por clave de trigger (candle_close/event_arrival/
schedule/manual/mixed), de modo que al llegar un trigger solo se evaluan
las reglas de su lote. La evaluacion compartida de subexpresiones la
dirige la declaracion de cada DataSource (shared_evaluation/sharing_scope
/cache_key_schema) y se detecta sobre formas canonicas identicas (alcance
de ADR-015, sin equivalencia semantica arbitraria); dentro de un lote las
condiciones se ordenan por coste/selectividad. El plan se identifica por
un PlanFingerprint derivado de todos sus inputs contractuales
(canonical_rule_hash, rule_schema_version, compiler_version,
function_catalog_version, datasource_manifest_versions,
datasource_capability_schema_versions, cache_key_schema versions,
trigger_index_version, plan_policy_version); cualquier cambio en uno de
ellos invalida el plan y fuerza recompilacion, y si no puede recomputarse
la Rule queda DISABLED/FAILED/QUARANTINED observable (ADR-010), nunca se
ejecuta con un plan obsoleto en silencio. El PlanFingerprint se persiste
junto al ExecutionPlan y aparece en metricas/logs de compilacion. La
implementacion v5.0 puede ser trivial (AST anotado + indexado por
trigger, solo candle_close activo), con shared_evaluation y ordenacion
por coste como optimizacion progresiva; el compilador solo compila canon
que ya paso el complexity budget de ADR-015.

Consecuencias:
Escalabilidad del motor de decenas a miles de reglas sin refactor (se
sustituye el AST interpretado en cada evaluacion por lotes por trigger +
subexpresiones compartidas), y de forma SEGURA porque el plan sabe cuando
esta obsoleto. La costura de compilacion se disena desde el principio sin
obligar a un compilador industrial el dia uno. Se acepta: el Execution
Plan es una pieza a construir y testear (mitigada por la implementacion
minima); la deteccion de subexpresiones comunes se limita a formas
canonicas identicas (coste marginal, no afecta correccion); el
PlanFingerprint debe cubrir todos los inputs contractuales y mantenerse
al anadir nuevos (barato si se disena desde el principio).

Alternativas consideradas:
A forma canonica -> AST -> runtime interpretado: descartada como
arquitectura final (repite el patron de v4: interpretar el arbol en cada
evaluacion y optimizar tarde); admisible solo como implementacion interna
minima durante v5.0 dentro del marco de B.

Referencias:
Cierra DAP-15 y el Bloque F. INFORME 6 sec.13, INFORME 7. Cruza
DAP-13/ADR-015, DAP-14/ADR-016, DAP-7/ADR-008, DAP-6/ADR-010,
DAP-17/ADR-013, DAP-5/ADR-014, DAP-11/ADR-012, DAP-16. Relacionado con
ADR-015, ADR-008.

===================================================================
ADR-018: Arquitectura de ejecucion multi-broker.
===================================================================
Estado: Aceptado.
Fecha: 2026-07-06.

Contexto:
Cierra DAP-16 (Bloque G). Ninguna DAP previa cubria la ejecucion en si
(objeto central de INFORME 9). Es dinero real: las garantias deben ser
honestas y el default seguro. Ya cerrado condiciona: ADR-015 (el motor
no ejecuta; la config no vive en la regla), ADR-012 (gate fail-closed,
kill switch, SensitiveActionAudit), ADR-011 (ExecutionProfile/
credenciales tenant-scoped), ADR-010 (connector como ComponentInstance;
estados de credencial), ADR-008 (connector declara capacidades), ADR-013
(execution.* con idempotencia real, outbox, DLQ; at-least-once, no
exactly-once magico), ADR-014 (streams privados), ADR-007 (Clock),
ADR-003/004 (execution.*, causation_id). INFORME 5: BYOC no-custodial,
envelope encryption, minimo privilegio.

Decision:
Capa de ejecucion unica (opcion A) con ExecutionRequest neutral
(source_type signal|manual_ui|future_workflow); una sola maquinaria para
orden automatica y manual. Cadena: ExecutionRequest -> execution gate
fail-closed (ADR-012) -> risk manager (RiskDecision allow|block|reduce_
size|require_manual_confirmation, veto independiente del gate) -> order
manager -> connector. Semantica realista: CE v5 no promete exactly-once
externo frente al broker; el contrato es at-least-once en transporte,
efectos internos idempotentes, client_order_id determinista cuando el
exchange lo soporte (si no, el connector lo declara y la capacidad puede
quedar bloqueada fail-closed), reconciliacion obligatoria (periodica y
post-reconexion) y nunca reintento a ciegas de estado ambiguo. Maquina
minima de estados de orden normalizada (REQUESTED, GATE_BLOCKED, RISK_
BLOCKED, CONFIRMATION_REQUIRED, CONFIRMATION_EXPIRED, READY_TO_SUBMIT,
SUBMITTING, SUBMITTED, ACKNOWLEDGED, PARTIALLY_FILLED, FILLED, CANCEL_
REQUESTED, CANCELED, REJECTED, EXPIRED, UNKNOWN, RECONCILING, FAILED_
TERMINAL), append-only en ExecutionHistory; ningun estado externo
ambiguo pasa a FILLED/CANCELED/REJECTED sin evidencia o reconciliacion
verificable. require_manual_confirmation crea un PendingExecution
Confirmation con TTL (expires_at), emite execution.confirmation_required
y exige confirmacion idempotente del usuario; al confirmar se reevaluan
gate + risk justo antes de enviar; una confirmacion caducada/stale o con
policy/kill switch cambiado no ejecuta; la confirmacion no puede saltarse
el gate. El connector es un Componente que declara capacidades y traduce
a CCXT (base v5.0) o SDK (hibrido donde se justifique); CEX-BYOC en v5.0,
DEX como extension futura. BYOC no-custodial con envelope encryption,
api_key_ref, verificacion de permisos al conectar y minimo privilegio.
ExecutionProfile es la config de ejecucion del usuario, versionada y
tenant-scoped, fuera de la Rule. La familia execution.* lleva
causation_id, Clock inyectado, payloads minimos, y los fills llegan por
streams privados. SensitiveActionAudit registra confirmation_required/
confirmed/expired, risk reduce_size, cambios de ExecutionProfile y de
permisos del connector, ademas de los bloqueos del gate. El autotrade es
un source_type mas, no el eje.

Consecuencias:
Una maquinaria unica, resiliente, auditable, no-custodial y honesta en
sus garantias; los fallos reales de red/exchange (timeouts, estados
ambiguos) se vuelven estados operables (UNKNOWN/RECONCILING) en vez de
excepciones ad hoc; la confirmacion humana es autorizacion adicional, no
bypass. Se acepta: CCXT introduce una capa intermedia (latencia/quirks) a
cambio de coste bajo de integracion, mitigada con SDK hibrido; sin
exactly-once externo, algunos escenarios exigen reconciliar antes de
actuar; el flujo de confirmacion + reevaluacion anade pasos necesarios.

Alternativas consideradas:
B dos pipelines separados (autotrade vs manual): descartada, duplica
gate/risk/order manager/idempotencia/reconciliacion/auditoria y deriva a
divergencias peligrosas. C ejecucion acoplada al motor de reglas:
descartada, viola ADR-015 (la regla detecta y proyecta senal, no
ejecuta).

Referencias:
Cierra DAP-16. INFORME 9, INFORME 5 (BYOC), INFORME 8 (contratos). Cruza
DAP-2/ADR-003-004, DAP-4/ADR-007, DAP-5/ADR-014, DAP-6/ADR-010,
DAP-7/ADR-008, DAP-8/ADR-001, DAP-10/ADR-011, DAP-11/ADR-012,
DAP-13/ADR-015, DAP-17/ADR-013. Relacionado con ADR-012, ADR-015.

===================================================================
ADR-019: Cliente PWA-first migrable a nativa.
===================================================================
Estado: Aceptado.
Fecha: 2026-07-06.

Contexto:
Cierra DAP-12 (primera del Bloque H). INFORME 10 (ultimo INFORME, 4
revisiones CSA, 19 obs) amplio esta DAP con todos los contratos de
cliente. Realidad verificada 2026: iOS Web Push solo con PWA instalada
(16.4+), sin Background Sync fiable, storage evictable ~7 dias, sin Live
Activities; riesgo App Store anti-wrapper (Guideline 4.2). Ya cerrado
condiciona: ADR-006 (shared-contracts -> tipos del cliente), ADR-012 (el
cliente consume permisos, no los decide), ADR-013/014 (RealtimeClient
consume el envelope sin inventar campos; checkpoint como estado de
cliente), ADR-016 (i18n/RTL/CJK), ADR-018 (no operar desde cache en
acciones sensibles), INFORME 4 (Notification Router), INFORME 5 (auth
backend).

Decision:
Cliente PWA-first migrable a nativa (opcion A): la PWA como
implementacion de un cliente portable con capas ui-core/app-core/
device-ports/shared-contracts, y device-ports para todas las
capacidades de dispositivo. device-web es el adapter de v5.0. Capacitor
es la ruta de empaquetado preferente y reversible: mantiene la UI web y
sustituye/adapta los adapters de dispositivo mediante device-capacitor,
conservando ui-core web como codigo. RN/Flutter/nativo puro son
evolucion posible que puede reimplementar la capa de presentacion/
ui-core pero conserva app-core, shared-contracts, contratos de backend,
interfaces de device-ports y el modelo auth/realtime/permisos; la
arquitectura no promete reutilizacion total de UI fuera de Capacitor,
solo no reescribir dominio, contratos ni capacidades. Sin cerrar
framework. Service worker sin logica de negocio, con PWAUpdatePolicy;
offline limitado y explicito con prohibicion de operar desde cache. Se
adoptan los contratos de cliente de INFORME 10 (AuthSessionPort/
SecureStoragePort; AuthFlowPort/AuthRedirectPort con external user-agent
y PKCE; ApiClientPort/NativeHttpPort con el refresh token nunca al JS;
DeviceInstallation/PushSubscription; RealtimeConnectionPort +
RealtimeCheckpoint + RealtimeAuthContract; DeepLinkContract;
ServiceWorkerLifecycle; versionado de cliente). i18n/RTL/CJK desde el
primer commit. Niveles de sonido N1 in-app / N2 push del sistema / N3
nativo, con N3 condicionado a la via nativa (iOS Critical Alerts por
entitlement de Apple; Android canal de alta importancia con bypass DND
no universal). El spike de Capacitor y el testing en dispositivo real
son trabajo de construccion. Las politicas de stores/DMA/entitlements
son de Alvaro.

Consecuencias:
Portabilidad real con coste bajo y movil desde el primer commit; el
cliente escala a nuevas superficies por adapter (Capacitor) o por
reimplementacion de presentacion (RN/Flutter/nativo) sin reescribir
dominio, contratos ni auth/realtime. Se acepta: device-ports es
abstraccion extra frente a APIs web directas (justificada por la
migrabilidad); iOS PWA tiene limites reales mitigados por la via nativa;
fuera de Capacitor la presentacion puede requerir reimplementacion
(limite honesto); el spike y el testing en dispositivo quedan para
construccion.

Alternativas consideradas:
B PWA "cerrada" monolitica: descartada; funciona como demo pero no como
base de producto ni puente a nativo (la "peor decision" de INFORME 10:
logica en componentes, permisos dispersos, service worker opaco,
dashboard desktop comprimido en movil).

Referencias:
Cierra DAP-12. INFORME 10, INFORME 4 (notificacion), INFORME 5 (auth),
INFORME 2 (frontend, sin cerrar framework); REST-6/OBJ-3, REST-13. Cruza
DAP-9, DAP-11/ADR-012, DAP-2/ADR-003, DAP-17/ADR-013, DAP-5/ADR-014,
DAP-14/ADR-016, DAP-16/ADR-018. Relacionado con ADR-006, ADR-012.

===================================================================
ADR-020: Estrategia de charting responsive/PWA.
===================================================================
Estado: Aceptado.
Fecha: 2026-07-06.

Contexto:
Cierra DAP-9, ULTIMA de las 17 (segunda del Bloque H). CE v5 es SaaS
comercial (OBJ-1) y plataforma de widgets; el chart debe ser PWA movil
usable desde el dia uno (REST-6/OBJ-3). En v4 KLineChart v9 vivia dentro
de Dash; en v5 el chart vive en el frontend (REST-2) consumiendo datos
por API/WS. Decision heredada de v4 con motivo, documentada aqui para no
reabrirla sin causa: Lightweight Charts se descarto en v4 porque no
representaba indicadores como el RSI (su doc oficial confirma que no
trae indicadores integrados), no permitia algunas herramientas de dibujo
necesarias, y su diseno empuja a pagar Advanced Charts; por eso en v4 se
eligio KLineChart. Ya cerrado condiciona: ADR-019 (el chart es UI en
ui-core; los device-ports son capacidades de dispositivo, no de
presentacion), ADR-015/016 (overlay de signal.*; marcas con metadatos
por contrato; i18n/RTL), ADR-014 (datos por MarketStreamKey), ADR-006
(tipos de shared-contracts), ADR-007 (event_time), ADR-012/018 (el gate
es de ejecucion, no de visualizacion).

Decision:
Estrategia de charting en dos categorias detras de una abstraccion
ChartPort como UI adapter / presentation port dentro de ui-core (no
device-port), de modo que el cliente no se acopla a la libreria concreta
y cambiar de libreria toca solo el adapter y piezas visuales de ui-core,
nunca app-core, shared-contracts, contratos backend, reglas, DataSources
ni execution. Chart financiero principal: KLineChart (Apache-2.0),
continuidad de la decision de v4 (sin atribucion obligatoria, perfil
movil explicito, zero-dep, con indicadores y herramientas de dibujo);
Lightweight Charts descartada por el motivo de v4 arriba; TradingView
Advanced Charts descartada por licencia restrictiva de riesgo alto para
producto de pago. La fijacion definitiva de la libreria financiera queda
condicionada a validacion en PWA movil real (fuera de Dash, frontend TS
responsive), que se ejecuta en construccion. Charts genericos de widgets:
ECharts (Apache-2.0, sin tope de ingresos, responsive/movil) como
preferente, con Chart.js/Recharts para widgets simples. Criterio de
aceptacion duro: la libreria financiera debe soportar de forma aditiva
overlays, series y dibujos custom, marcas con metadatos por contrato,
hit-testing, interaccion tactil y resize, sin que el chart conozca
dominio. El overlay de senales es universal en todas las jurisdicciones
(el geo-blocking corta ejecucion, no visualizacion). Canvas por perfil de
bateria/repintado; anclaje temporal de overlays por event_time.

Consecuencias:
El chart financiero queda como un widget especializado detras de una
abstraccion de presentacion, no como el eje de CE v5, protegiendo el
criterio 4; la libreria es sustituible sin refactor del cliente si el
spike movil lo exige; se mantiene la continuidad con KLineChart ya
validada en v4, con su veto de Lightweight documentado para no reabrirlo.
Se acepta: mantener dos librerias (financiera + widgets) en vez de una;
KLineChart no queda 100% re-confirmada hasta el spike movil en
construccion; el ChartPort anade una capa de abstraccion (justificada por
el criterio 4 y la migrabilidad).

Alternativas consideradas:
Lightweight Charts (TradingView, Apache-2.0 con atribucion obligatoria):
descartada por decision heredada de v4 (sin indicadores integrados como
RSI, faltaban herramientas de dibujo, diseno orientado a llevar a
Advanced Charts). TradingView Advanced Charts: descartada por licencia
restrictiva (solo empresas en proyectos web publicos; riesgo de producto
detras de un plan de pago). Una sola libreria para todo: descartada
(ninguna cubre bien chart financiero y widgets sin friccion).

Referencias:
Cierra DAP-9 y todas las DAPs de DOC_ARQ_V5. INFORME 2 sec.4 (librerias,
licencias, mobile), INFORME 3 (dashboard, marcas con metadatos), INFORME
6 sec.16 (overlays programaticos); LECCIONES_V4 (veto de Lightweight,
motivo); REST-6/OBJ-3, REST-13. Cruza DAP-12/ADR-019, DAP-13/ADR-015,
DAP-5/ADR-014, DAP-2/ADR-006, DAP-4/ADR-007, DAP-11/ADR-012,
DAP-14/ADR-016. Relacionado con ADR-019.

===================================================================
ADR-021: Familia de evento policy.* para la propagacion de politica y kill switch.
===================================================================
Estado: Aceptado.
Fecha: 2026-07-11.

Contexto:
ADR-012 exige que el kill switch sea entrada de primera clase y que
PROPAGUE POR EVENTO, sin reinicio, y que la cache del capability set se
invalide por evento como mecanismo principal. ADR-004 declaro CERRADAS
las diez familias base (market, datasource, rule, signal, alert,
execution, notification, user, component, billing) y dejo una clausula
de gobierno explicita: familia nueva solo por ADR o decision explicita
de arquitectura. Ninguna de las diez familias cubre semanticamente "ha
cambiado la politica" ni "se ha activado un kill switch". No hay
contradiccion entre ADRs: hay un hueco que ADR-004 previo. Elevado como
CA-02 y firmado por Alvaro.

Decision:
Se crea la familia policy.*, con cuatro tipos:
policy.kill_switch_activated, policy.kill_switch_deactivated,
policy.version_published y policy.subject_invalidated. Scope (ADR-003):
system para los kill switch de plataforma (global, exchange, connector,
market_scope, capability); tenant o user para los dirigidos a un sujeto;
policy.subject_invalidated con el scope del sujeto invalidado. FRONTERA
DURA policy.* / component.*: policy.* es la CAUSA (cambia la politica, se
activa un kill switch, se invalida el capability set de un sujeto);
component.* es la CONSECUENCIA (cambia el lifecycle de una
ComponentInstance). Flujo canonico: se activa un kill switch -> se emite
policy.kill_switch_activated -> el supervisor lo consume -> si decide
aislar, emite component.quarantined con causation_id apuntando al
event_id del policy.*. Un componente que acaba en QUARANTINED por
politica NO convierte el kill switch en un component.*. Los payloads
viven en contracts/source (ADR-006) y estan registrados en el registro
canonico event_type -> payload.

Consecuencias:
Se gana: vocabulario limpio y autoexplicativo para la propagacion de
politica; los consumidores del gate se suscriben a policy.* y no a un
firehose de otra familia; extensibilidad sin tocar el nucleo (tipos
nuevos dentro de la familia se declaran en el manifest, ADR-004 nivel 1);
cadena causal explicita entre politica y lifecycle. Se acepta: una
familia mas en el vocabulario (ampliacion ADITIVA del enum cerrado, de
diez a once), que el check de compatibilidad de schemas (7.7) trata como
compatible (ampliar un enum lo es; reducirlo no), demostrado con prueba
negativa.

Alternativas consideradas:
B. Reutilizar una familia existente (component.* o user.*). Descartada:
fuerza la semantica (un kill switch global o de exchange no es un hecho
de usuario ni de componente), deja sin casa el kill switch por exchange,
market_scope y capability, y fragmenta el mecanismo entre dos familias.
Es deuda conceptual del tipo que causo la deriva de v4.
C. Propagar el kill switch FUERA del EventBus (polling a DB o canal
ad-hoc). Descartada: contradice ADR-012 (propagacion por evento) y
ADR-013 (todo proceso se comunica por el bus), y reintroduce el bus
informal de v4.

Referencias:
Ejercita la clausula de gobierno de ADR-004 (que queda VIGENTE e
intacto). Cruza ADR-012 (PolicyEvaluator y kill switch), ADR-003
(envelope, scope, causation_id), ADR-010 (QUARANTINED) y ADR-013
(transporte y outbox). Origen: CA-02, firmada por Alvaro. Construido en
la pieza P06.
