# DOC_ARQ_V5.md

Documento de Arquitectura de Crypto Engine V5.

Naturaleza: PRESCRIPTIVO. A diferencia de los INFORMES (descriptivos),
este documento recorre las 17 DAPs, elige UNA opcion para cada una con
justificacion contra los cuatro criterios, la formaliza como ADR en
ADRS_PROPUESTOS.md y deja la DAP cerrada.

Autoridad de decision: Alvaro (decisor unico). El CSA es consultivo.
Estado: NUCLEO CERRADO. Las 17 DAPs estan cerradas (ADR-001 a ADR-020); esqueleto v1 aprobado 2026-07-06. Cierre editorial 2026-07-06.
Fecha de creacion: 2026-07-06.

===================================================================
0. METADATOS
===================================================================
- Version: 1.0 (nucleo de decisiones cerrado).
- Estado: 17 DAPs cerradas (DAP-1 a DAP-17); 20 ADR formalizados
  (ADR-001 a ADR-020).
- Fecha de cierre del nucleo: 2026-07-06.
- Autoridad de decision: Alvaro (decisor unico); CSA consultivo.

===================================================================
RESUMEN EJECUTIVO
===================================================================
DOC_ARQ_V5 es la sintesis prescriptiva que cierra la fase de
investigacion de Crypto Engine V5. Recorre las 17 Decisiones
Arquitectonicas Pendientes (DAP-1 a DAP-17), elige una opcion para
cada una justificada contra cuatro criterios (consistencia,
escalabilidad, simplicidad/coste, vision de plataforma) y la cristaliza
como ADR. Resultado: 17 DAPs cerradas, 20 ADR formalizados (ADR-001 a
ADR-020). Este documento es el norte inamovible que consumira el
proyecto de construccion.

QUE ES CE v5. Una plataforma comercial multiusuario de analisis
cuantitativo y automatizacion sobre mercados de criptomonedas,
accesible como web y PWA instalable. NO es un bot de trading: el
trading es UNA capacidad gateada -opcional, solo BYOC y solo donde la
regulacion lo permite-, no el eje. Todo en el sistema es un Componente
del mismo tipo raiz (engines, workers, connectors, plugins de UI,
notification providers, exporters), que declara sus capacidades, se
descubre por convencion y tiene un lifecycle observable.

LAS DECISIONES, EN UNA FRASE CADA UNA.
- Forma (ADR-001/002): plataforma de Componentes en monolito modular
  multiproceso sobre EventBus externo, con costuras de extraccion.
- Espina dorsal (ADR-003 a 007): envelope canonico unico, versionado,
  con familias de evento gobernadas y tiempo explicito (Clock
  inyectado, UTC).
- Sustrato (ADR-008 a 010): manifest tipado, discovery por carpeta,
  lifecycle con estados y quarantine.
- Multiusuario (ADR-011/012): tenancy shared-schema + RLS y un
  PolicyEvaluator central fail-closed que resuelve jurisdiccion, plan
  y permisos a nivel API.
- Transporte y datos (ADR-013/014): EventBus con idempotencia, DLQ y
  outbox/inbox; market data hibrido con publicos compartidos y privados
  BYOC.
- Motor de reglas (ADR-015 a 017): una raiz Rule neutral con dos
  productos (alertas y senales de trading), canon localizable, y
  compilacion a plan derivado reconstruible.
- Ejecucion (ADR-018): ExecutionRequest neutral -> gate fail-closed ->
  risk manager -> order manager -> connector; sin exactly-once externo;
  BYOC no-custodial.
- Cliente y UI (ADR-019/020): PWA-first portable migrable a nativa;
  charting en dos categorias (KLineChart, ECharts) tras un adapter.

POR QUE ASI, Y NO COMO v4. v4 murio no por mal diseno abstracto, sino
por codificar antes de cerrar contratos, capas y disciplina, acumulando
deuda (bus informal, sin versionado, Clock tardio, codigo muerto,
decisiones sin registro del motivo). CE v5 invierte el orden: la base
esta cerrada, contratada y documentada -cada decision con su motivo-
ANTES de la primera linea de codigo. El sistema escala de decenas a
miles de usuarios anadiendo capacidad, no reescribiendo.

FRONTERA DE ALCANCE. Este documento decide arquitectura tecnica. Las
decisiones legales, fiscales, regulatorias (MiCA, KYC/AML de negocio,
licencias), de estructura societaria y de politica comercial son de
Alvaro con asesoria externa; el diseno provee el MECANISMO para hacer
cumplir esas politicas, no las define.

COMO LEERLO. La seccion 3 fija los principios rectores; las secciones
6-7 contienen las 17 decisiones con su plantilla y trazabilidad; la
seccion 8 da la vista consolidada (capas y flujo de punta a punta); la
seccion 9 registra lo diferido a construccion y las ideas de herencia
para v5.1+. El razonamiento largo vive aqui; su cristalizacion, en
ADRS_PROPUESTOS.md.

===================================================================
1. PROPOSITO Y NATURALEZA
===================================================================
DOC_ARQ_V5 es la sintesis prescriptiva de la fase de investigacion.
Cierra formalmente las 17 DAPs y fija la arquitectura que el Central
de la fase de construccion consumira como norte inamovible. Cada
decision se razona aqui y se cristaliza como ADR en ADRS_PROPUESTOS.md.

===================================================================
2. CONVENCIONES Y COMO LEER
===================================================================
- Texto ASCII-safe.
- Nomenclatura OBJ/REST/CE/INFORME N de INFORME 0.
- Relacion con ADRS_PROPUESTOS.md: este documento RAZONA (bloque por
  DAP con la plantilla completa); el ADR es la version CRISTALIZADA.
  Se referencian mutuamente; no se duplica el razonamiento largo.
- Numeracion de ADR: secuencial desde ADR-001 por ORDEN DE CIERRE (no
  por numero de DAP). Una DAP puede generar mas de un ADR si tiene
  sub-decisiones separables.

===================================================================
3. PRINCIPIOS ARQUITECTONICOS RECTORES
===================================================================
Los principios de INFORME 0 que gobiernan TODA decision de CE v5. No
son aspiraciones: cada uno se materializo en ADRs concretos. Esta
seccion los enuncia y apunta donde se cumplen, para que el Central de
construccion los reconozca como restricciones duras, no preferencias.

-------------------------------------------------------------------
3.1 PLATAFORMA, NO BOT (criterio 4 elevado a principio)
-------------------------------------------------------------------
CE v5 es una plataforma extensible de Componentes; el trading es UNA
capacidad, no el eje. Ninguna decision de infraestructura (naming,
schema de eventos, modelo de componentes) puede acoplar el nucleo a
"trading". Se materializa en: raiz Componente neutral (ADR-001); raiz
Rule sin campos de mercado, con el mercado en las hojas (ADR-015);
familias de evento donde signal.* es hija de rule.*, no el centro
(ADR-004); autotrade como un source_type entre varios en
ExecutionRequest, no camino privilegiado (ADR-018); ChartPort como
adapter de presentacion, con el chart financiero como un widget mas
(ADR-020). Cada capacidad futura (dibujo, patrones, prediccion) entra
como DataSource/Componente sin tocar el nucleo.

-------------------------------------------------------------------
3.2 MULTI-TENANT DESDE EL DIA 1
-------------------------------------------------------------------
El aislamiento por usuario/tenant es base desde el primer commit, no
un extra posterior. Se materializa en: tenancy shared-schema + RLS con
fallo cerrado (ADR-011); envelope con scope y tenant_id/user_id
condicionales (ADR-003); reglas por-tenant (ADR-015), ExecutionProfile
y credenciales tenant-scoped con RLS (ADR-011/018); streams privados
por-usuario con RLS (ADR-014). Nada que pertenezca a un tenant se
comparte entre tenants por accidente. Las excepciones no-tenant son
explicitas y clasificadas: datos publicos de mercado (scope
public_market, sin tenant_id) y datos/catalogos de plataforma o
infraestructura (scope system o definiciones globales, segun contrato).
Toda tabla/evento debe declarar su alcance: public_market, tenant, user
o system.

-------------------------------------------------------------------
3.3 GEO-BLOCKING A NIVEL API
-------------------------------------------------------------------
La diferenciacion por jurisdiccion se aplica en el backend, no solo en
la UI: ocultar un boton no basta. Se materializa en: PolicyEvaluator
central que resuelve capacidades por jurisdiccion (IP+KYC), plan y rol,
con enforcement en API y fail-closed en lo sensible (ADR-012); el
execution gate que cierra la ruta de ejecucion (automatica y manual) a
nivel API para UE/EEA/UK (ADR-018). Principio asociado: el geo-blocking
corta la EJECUCION, nunca la VISUALIZACION -las senales y overlays se
ven en TODAS las jurisdicciones (ADR-020).

-------------------------------------------------------------------
3.4 i18n + RTL + CJK-READY DESDE EL PRIMER COMMIT
-------------------------------------------------------------------
La arquitectura de v5.0 debe permitir anadir arabe (v5.1, RTL) y chino
(v5.2, CJK) como trabajo de traduccion y activacion, sin refactor de
codigo, sin CSS fisico, sin texto hardcodeado. Se materializa en: canon
del lenguaje de reglas en ingles como identificadores internos con
localizacion en la capa de renderizado (ADR-016); cliente con i18n/RTL/
CJK y CSS logico desde el primer commit (ADR-019); errores, warnings,
diagnostics y reason_codes del chatbot/validador emitidos como
code+params y renderizados por i18n, nunca texto hardcodeado (ADR-016).

-------------------------------------------------------------------
3.5 API-FIRST DESACOPLADO
-------------------------------------------------------------------
Cliente y backend se comunican solo por contratos versionados; el
cliente nunca accede a memoria interna del sistema. Se materializa en:
contratos Pydantic -> JSON Schema -> TS como frontera unica (ADR-005/
006); cliente portable que consume API/WebSocket y tipos de shared-
contracts, sin logica de negocio en la UI (ADR-019); el RealtimeClient
consume el envelope sin inventar campos, con el checkpoint como estado
de cliente (ADR-019). La abstraccion permite cambiar implementaciones
internas de backend, transporte operativo o librerias de cliente sin
romper consumidores siempre que se respeten los contratos versionados.
Esta propiedad se apoya en ADR-005/006 para schemas y tipos, ADR-013
para transporte operativo, ADR-019 para cliente portable y ADR-020 para
charting aislado por adapter.

-------------------------------------------------------------------
3.6 CONTRATOS, EVENTOS Y TIEMPO COMO ESPINA DORSAL
-------------------------------------------------------------------
Los datos son el contrato entre componentes. CE v5 no comunica
subsistemas mediante objetos internos, callbacks informales ni acceso
directo a stores, sino mediante contratos versionados, eventos con
envelope canonico y modelo temporal explicito. Es la respuesta directa
a la deuda de v4 (bus informal _bus(ev), sin versionado, Clock anadido
tarde). Se materializa en: envelope canonico unico con identidad fisica,
identidad logica, scope, temporalidad y linaje (ADR-003); taxonomia de
eventos dominio.accion con familias gobernadas (ADR-004); versionado
dual envelope/payload y reglas de evolucion compatibles (ADR-005);
Pydantic -> JSON Schema -> TypeScript como shared-contracts (ADR-006);
modelo temporal con event_time, ingestion_time, processing_time, Clock
inyectado, UTC y politica de tardios/correcciones (ADR-007); sustrato
EventBus/colas/workers con at-least-once, consumer groups, DLQ,
outbox/inbox e idempotencia real (ADR-013). Regla dura: ningun
componente puede saltarse estos contratos ni recrear un "_bus(ev)"
informal.

-------------------------------------------------------------------
3.7 SIN REFACTOR DE DECENAS A MILES
-------------------------------------------------------------------
El sistema escala anadiendo capacidad, no reescribiendo. Se materializa
en: monolito modular con costuras de extraccion (ADR-002); EventBus con
consumer groups y particionado (ADR-013); market data compartido por
MarketStreamKey que rompe la explosion de conexiones (ADR-014); motor
de reglas con forma canonica, shared_evaluation y Execution Plan
compilado (ADR-015/017); ejecucion resiliente con idempotencia y
reconciliacion (ADR-018). El objetivo de v5.0 (decenas de usuarios) y
la meta (miles) comparten arquitectura.

-------------------------------------------------------------------
3.8 ANTI-DEUDA (las cuatro causas de muerte de v4)
-------------------------------------------------------------------
v4 no murio por mal diseno abstracto, sino por empezar a codificar
antes de cerrar contratos, capas y disciplina. Las cuatro razones
estructurales (R1 documentacion tardia; R2 decisiones cambiadas sin
registro del motivo; R3 codigo muerto y modulos huerfanos; R4
funcionalidades descartadas cuyo codigo persiste) se combaten POR
DISENO:
- R1: la arquitectura se cierra ANTES del codigo (este documento); cada
  decision es un ADR con contexto y consecuencias.
- R2: todo cambio de opinion queda con su motivo escrito (el veto de
  Lightweight Charts documentado en ADR-020 es el patron: nunca un "esta
  descartado" sin porque).
- R3/R4: plugin discovery por convencion y manifest declarativo
  (ADR-008/009) evita cableado invisible; lifecycle observable con
  FAILED/QUARANTINED (ADR-010) hace visibles componentes rotos o no
  operables; y la disciplina de CI, trazabilidad de ADRs, eliminacion de
  placeholders y prohibicion de modulos "por si acaso" combate el codigo
  muerto. QUARANTINED no sustituye la disciplina anti-codigo-muerto:
  solo hace observable el fallo de componentes registrados.
Este principio es el motivo de existir de la fase de investigacion: la
base esta cerrada antes de la primera linea de codigo.

-------------------------------------------------------------------
3.9 PATRONES TRANSVERSALES QUE EMERGEN
-------------------------------------------------------------------
De aplicar los principios a las 17 DAPs surgen patrones que se repiten y
conviene reconocer como firma arquitectonica de CE v5:
- DERIVADO RECONSTRUIBLE: caches y materiales derivados (capability set,
  ref-count de suscripcion, Execution Plan y su PlanFingerprint como
  identidad de invalidacion) nunca son fuente de verdad; se reconstruyen
  desde el canon (ADR-012/014/017).
- FAIL-CLOSED EN LO SENSIBLE: ante duda, denegar; capacidades sensibles
  y ejecucion tienen default seguro (ADR-012/018).
- IDEMPOTENCIA Y RECONCILIACION: at-least-once + efectos idempotentes +
  reconciliacion, sin exactly-once magico (ADR-013/018).
- PROYECCION CON LINAJE: los productos derivados (signal.*, alert.*) se
  proyectan de una fuente neutral (rule.*) con causation_id, sin motores
  paralelos (ADR-015).
- NEUTRALIDAD DE RAIZ Y DE PUERTOS: las raices de dominio/plataforma
  (Componente, Rule, ExecutionRequest) y los puertos/adapters de
  frontera (ChartPort, device-ports, connectors) son neutrales; la
  especializacion vive en hojas, adapters o implementaciones concretas.
Estos patrones son la forma concreta en que CE v5 evita repetir v4.

===================================================================
4. LOS CUATRO CRITERIOS DE DECISION
===================================================================
C1 Consistencia arquitectonica con INFORME 0 y decisiones previas.
C2 Escalabilidad de decenas a miles sin refactor estructural.
C3 Simplicidad y coste de mantenimiento para equipo pequeno.
C4 Vision de plataforma (no reducir CE v5 a bot de trading).

===================================================================
5. CAPACIDADES FUTURAS PREVISTAS (v5.1+)  [SECCION-TEST]
===================================================================
Objetivo de extensibilidad, usado como TEST al cerrar ciertas DAPs:
- Dibujo manual sobre el chart (trendlines, figuras, anotaciones).
- Deteccion automatica de patrones.
- Alertas configurables SOBRE esos dibujos y patrones.

Modelado previsto: cada dibujo/detector = una DataSource nueva + un
Componente nuevo (plugin discovery); las alertas sobre ellos = el
mismo motor de reglas. En v5.0 el "precio" ya es una DataSource y una
alerta de precio es una regla que la observa; v5.1 solo anade
DataSources y Componentes, sin refactor.

Nota anclada: el anclaje temporal de los dibujos (puntos tiempo/precio)
frente a candle_corrected y cambios de timeframe es un detalle de
diseno de la DataSource de dibujo (no de arquitectura); se anota para
v5.1.

Alcance del test v5.1+ por DAP:
- CORE (test explicito): DAP-8, DAP-3, DAP-7, DAP-2, DAP-13, DAP-14,
  DAP-15, DAP-9, DAP-12.
- MARGINAL (comprobacion ligera): DAP-4, DAP-6, DAP-11, DAP-17.
- N/A: DAP-1, DAP-5, DAP-10, DAP-16.

===================================================================
6. DECISIONES ARQUITECTONICAS
===================================================================
Las 17 DAPs se resuelven en 8 bloques por orden de dependencias.
Cada DAP usa la plantilla: 1 Enunciado, 2 Contexto, 3 Opciones,
4 Propuesta de Central, 5 Decision de Alvaro, 6 Criterios,
7 Test v5.1+, 8 Frontera, 9 Trade-offs, 10 Cruces, 11 ADR.

-------------------------------------------------------------------
BLOQUE A - Fundacional
-------------------------------------------------------------------

DAP-8 - Modelo de Componentes vs vocabulario "Engine"   [CERRADA]

1. ENUNCIADO. Como se estructura el modelo raiz de Componente del que
   cuelgan engines, workers, connectors, plugins UI, auth/notification
   providers, exporters y tipos futuros: herencia de clase base unica,
   frameworks paralelos, o contratos compartidos sin herencia forzada.
2. CONTEXTO. El vocabulario ya estaba fijado (Componente raiz, Engine
   subtipo). Lo abierto era el MECANISMO. En v4 no habia modelo raiz:
   cada engine se instanciaba y cableaba a mano en main.py (16 engines
   = 16 modificaciones; DIFICULTADES sec.3, L4); hubo engines
   desconectados. INFORME 8 ya trata a todos los tipos de forma
   uniforme en lo transversal (familia component.*, manifest, Clock).
   L19 advierte lo contrario: no forzar simetria entre componentes de
   naturaleza distinta.
3. OPCIONES. A unificado (clase base unica). B paralelos (frameworks
   por tipo sin clase comun). C hibrido (contratos comunes Lifecycle/
   Capabilities implementados por cada tipo, sin herencia profunda;
   mixins opcionales para boilerplate).
4. PROPUESTA DE CENTRAL. Opcion C. "Componente" es un ROL definido por
   contratos, no una clase base a heredar. Tres contratos transversales
   neutrales que todo componente satisface: Lifecycle (component.*,
   DAP-6), Capabilities/Manifest (produce/consume, requires Clock,
   permisos, config_schema; DAP-7), integracion con EventBus. Los tipos
   (Engine, Connector, NotificationProvider, ...) cumplen los contratos
   e implementan su comportamiento propio. Mixins/base-helpers
   OPCIONALES para el boilerplate (p.ej. maquina de estados de
   lifecycle). En Python: Protocol/ABC para el contrato, composicion
   sobre herencia para la implementacion.
5. DECISION DE ALVARO. ACEPTADA la opcion C (2026-07-06). Firmada como
   ADR-001.
6. CRITERIOS. C1 encaja con INFORME 8 y DAP-6/7/3; respeta L19. C2 la
   uniformidad de lifecycle+manifest elimina el cuello de v4 (main.py);
   B no escala. C3 evita la clase-Dios (fragile base class) y reduce
   duplicacion; idiomatico en Python. C4 superficie compartida NEUTRAL;
   la trading-ness vive solo en tipos concretos, nunca en la raiz.
7. TEST v5.1+ (CORE). Un TIPO nuevo (dibujo, detector de patrones) se
   anade implementando los mismos contratos y declarando su manifest,
   sin tocar raiz ni discovery. PASA de forma aditiva.
8. FRONTERA. Ninguna; decision puramente tecnica.
9. TRADE-OFFS. Se gana neutralidad, aislamiento entre tipos y
   extensibilidad. Se acepta: algo de boilerplate (mitigado con mixins
   opcionales); los contratos deben definirse pronto (DAP-6 y DAP-7,
   justo despues); se pierde el "unico sitio donde mirar" de una clase
   base (mitigado: el manifest es ese punto de declaracion).
10. CRUCES. DAP-6 (lifecycle), DAP-7 (manifest), DAP-3 (discovery),
    DAP-1 (par de bloque). Raiz que heredan DAP-16 y los notification
    providers de INFORME 4. INFORMES 8/2/5/6; L4, L19; DIFICULTADES 1-3.
11. ADR. ADR-001. Estado: Aceptado (2026-07-06).

DAP-1 - Monolito modular vs Microservicios   [CERRADA]

1. ENUNCIADO. Forma macro de despliegue del backend: monolito modular
   (un codebase desplegable) frente a microservicios (backend
   fragmentado en N servicios desde v5.0).
2. CONTEXTO. Par del Bloque A (con DAP-8). Equipo de dos, sin DevOps;
   decenas en v5.0 escalable a miles (OBJ-2). Leccion de v4: el
   problema no fue "ser monolito" sino acoplamiento sin contratos
   (INFORME 2 sec.8). Ya cerrado condiciona: REST-2 (UI fuera del
   proceso del motor), INFORME 7 (API y workers procesos separados,
   Docker sin K8s) y DAP-17 (EventBus externo). "Un solo proceso" ya
   estaba descartado; se decide monolito modular multiproceso vs
   microservicios.
3. OPCIONES. A monolito modular (un backend por modulos; Componentes
   de DAP-8 como modulos; runtime API + worker(s) sobre EventBus
   externo). B microservicios (servicios autonomos desde v5.0).
4. PROPUESTA DE CENTRAL. Opcion A "monolito modular MULTIPROCESO con
   costuras de extraccion": un codebase desplegable + shared-contracts
   como frontera unica (no un solo proceso); runtime API + worker(s)
   separados (REST-2, INFORME 7) sobre el bus externo (DAP-17); modulos
   comunicados por contratos y eventos, sin imports cruzados directos,
   de modo que un modulo caliente se pueda extraer a servicio sin
   reescritura; microservicios no en v5.0, posibles como evolucion por
   modulo.
5. DECISION DE ALVARO. ACEPTADA la opcion A (2026-07-06). Firmada como
   ADR-002.
6. CRITERIOS. C1 coherente con REST-2/3/4, INFORME 2 (sec.8 punto
   intermedio), INFORME 7 y DAP-8; microservicios dia uno contra
   REST-14. C2 el cuello (workers) escala por consumer groups sobre el
   bus (DAP-17); INFORME 7 llega a Etapa 3 sin fragmentar; extraccion
   por la costura si Etapa 4. C3 un codebase, un pipeline, un sitio
   donde depurar: decisivo para equipo de dos; microservicios son
   anti-patron del CSA a esta escala. C4 la extensibilidad la da el
   modelo de Componentes + plugin discovery (DAP-8/3), no la
   fragmentacion; costura neutral respecto a trading.
7. TEST v5.1+ (N/A). La forma de despliegue no condiciona anadir
   dibujo/patrones/alertas (lo garantizan DAP-8/3/7/13).
8. FRONTERA. Ninguna; decision tecnica.
9. TRADE-OFFS. Se gana simplicidad, un despliegue y costura de
   extraccion. Se acepta: mantener disciplina de modulos (sin imports
   cruzados; comunicacion por contratos/eventos), o el monolito degrada
   como en v4 (mitigado DAP-3/7 + CI); un deploy unico afecta a todo
   (mitigado gates CI + expand-and-contract de INFORME 7); extraer un
   modulo es trabajo real (mitigado: no es refactor estructural).
10. CRUCES. DAP-8, DAP-17, DAP-6/7, DAP-3. INFORMES 2 (sec.8) y 7;
    REST-2/3/4/14/15; L1, L2.
11. ADR. ADR-002. Estado: Aceptado (2026-07-06).

-------------------------------------------------------------------
BLOQUE B - Espina dorsal (eventos y tiempo)
-------------------------------------------------------------------
DAP-2 - Contratos formales de eventos   [CERRADA]

Frontera con DAP-4: DAP-2 ratifica las RANURAS temporales del envelope
(event_time, ingestion_time, processing_time, time_anchor_ref); su
semantica es DAP-4. La proyeccion rule/signal/alert la soporta el
envelope (causation_id/correlation_id) y se cierra en DAP-13.

--- ADR-003: Envelope canonico unico ---
Enunciado: un envelope unico compartido por todos los eventos, con
payload tipado por tipo.
Opciones: A envelope unico 4 bloques + payload; B envelope minimo; C
sin envelope comun.
Decision de Alvaro: ACEPTADA opcion A (2026-07-06). Campos: identidad y
tipo (event_id UUID v4, event_type, envelope_version,
event_schema_version, source); identidad LOGICA (idempotency_key
REQUIRED, stream_key REQUIRED, source_sequence, source_event_id) para
dedup/replay; alcance (scope public_market|tenant|user|system, tenant_id
condicional, user_id si scope=user); temporalidad (ranuras; semantica en
DAP-4); linaje (correlation_id REQUIRED, causation_id); payload tipado.
Criterios: C1 cierra el bus informal de v4; C2 idempotency_key+stream_key
+source_sequence dan resiliencia a reconexion/replay; C3 un envelope, no
N; C4 scope separa publico de mercado sin acoplar a trading.
Test v5.1+ (CORE): dibujo/detector emite con el mismo envelope sin
campos nuevos. PASA.
Trade-offs: mas metadatos por evento (compensado por payloads minimos);
formula de idempotency_key por familia se concreta en DOC_ARQ.
Cruces: DAP-4, DAP-10, DAP-13, DAP-17; INFORME 8 sec.2; L1.
ADR-003. Estado: Aceptado (2026-07-06).

--- ADR-004: Taxonomia de tipos de evento ---
Enunciado: naming y gobernanza de los tipos (cerrada vs extensible).
Opciones: A dominio.accion, familias cerradas + tipos extensibles; B
todo cerrado; C todo abierto.
Decision de Alvaro: ACEPTADA opcion A (2026-07-06). Naming dominio.accion,
tipos especificos. Familias base CERRADAS: market.*, datasource.*,
rule.*, signal.*, alert.*, execution.*, notification.*, user.*,
component.*, billing.*. Gobernanza: tipos nuevos dentro de familia se
declaran en el manifest del componente (DAP-3/7) y REFERENCIAN su schema,
que vive en shared-contracts (ADR-006); el manifest no sustituye al
schema. Familia nueva: solo por ADR o decision explicita de arquitectura.
Notas: execution.* (INFORME 9: order_submitted, order_filled,
risk_blocked, confirmation_required...) es la familia de la capa de
ejecucion; DAP-16 depende de ella. datasource.* generaliza el
FeatureEvent de v4. signal.* es hija de rule.*, no el eje.
Criterios: C1 naming consistente, alineado con DAPS.md/DAP-16; C2 anadir
capacidades no coordina despliegues; C3 orden sin burocracia; C4
taxonomia neutral.
Test v5.1+ (CORE): dibujo -> datasource.drawing_updated; deteccion ->
datasource.pattern_detected; alertas -> rule.*/alert.*; por manifest,
sin familia nueva ni cambio de envelope. Coherente con INFORME 6. PASA.
Trade-offs: crear FAMILIA nueva es via deliberada de arquitectura.
Cruces: DAP-3/7, DAP-13, DAP-16, DAP-17; INFORME 9/4/6.
ADR-004. Estado: Aceptado (2026-07-06).

--- ADR-005: Versionado dual y evolucion ---
Enunciado: versionar y evolucionar envelope y payloads sin romper
consumidores (REST-20; cierra L10).
Opciones: A versionado dual independiente + reglas + CI; B version unica
global; C sin versionado explicito.
Decision de Alvaro: ACEPTADA opcion A (2026-07-06). envelope_version y
event_schema_version independientes. Reglas (envelope, payloads,
entidades): nunca renombrar ni retipar; anadir nuevo + deprecar viejo
(expand-and-contract / tolerant reader); campos nuevos con default;
compatibilidad FULL por defecto; schemas como CODIGO en git, PR, con
CHECK de CI bloqueante; entidades con schema_version + migradores.
Criterios: C1 disciplina uniforme; C2 evolucionar sin coordinar
despliegues; C3 git+CI en vez de registry pesado; C4 neutral.
Test v5.1+ (marginal): anadir tipos/campos de dibujo/patrones es cambio
aditivo compatible. OK.
Trade-offs: coste de mantener el CI de compatibilidad y la deprecacion.
Cruces: ADR-006; INFORME 7 (version skew), INFORME 3 (entidades).
ADR-005. Estado: Aceptado (2026-07-06).

--- ADR-006: Tecnologia de contrato y validacion ---
Enunciado: con que se definen/serializan/validan los contratos y cuando
se valida en runtime.
Opciones: A Pydantic v2 + JSON Schema derivado + tipos TS, validacion en
bordes; B JSON Schema como fuente; C Protobuf/Avro + registry.
Decision de Alvaro: ACEPTADA opcion A (2026-07-06). Autoria: modelos
Pydantic v2 en backend, que exportan JSON Schema automaticamente; el JSON
Schema vive en shared-contracts como artefacto interoperable; de el se
generan los tipos TypeScript del frontend. Validacion: siempre en bordes
externos (API, WebSocket ingress, webhooks, conectores), siempre antes de
publicar eventos criticos al bus, siempre en CI/tests; en runtime interno,
completa en dev/test y selectiva/critica en produccion; tolerant reader
al consumir. No en v5.0: Avro/Protobuf como contrato principal ni schema
registry obligatorio (puerta abierta al escalar).
Nota de implementacion (CSA): los modelos Pydantic que actuan como
contrato NO viven como detalle privado de un modulo de backend; viven en
la capa/paquete shared-contracts o se generan desde ahi (REST-4). Es
cautela de construccion, no cambia la decision.
Criterios: C1 encaja con REST-4/7/20 y ADR-005; C2 JSON Schema+git+CI
escala a Etapa 3; C3 fuente unica (Pydantic) genera contrato y tipos TS;
C4 neutral.
Test v5.1+ (CORE por tecnologia): tipo nuevo de dibujo/patron = modelo
Pydantic nuevo -> JSON Schema nuevo -> tipo TS generado, validado en
bordes, sin tocar los existentes. PASA aditivo.
Trade-offs: JSON mas verboso que binario (irrelevante a esta escala);
binario/registry queda como via futura.
Cruces: ADR-005; INFORME 2 (stack), INFORME 7 (transporte); REST-4/7/20.
ADR-006. Estado: Aceptado (2026-07-06).
DAP-4 - Modelo temporal granular   [CERRADA]

Frontera con DAP-2: DAP-2 (ADR-003) ratifico las RANURAS TEMPORALES del
envelope (event_time, ingestion_time, processing_time, time_anchor_ref).
DAP-4 decide su semantica operativa y define, FUERA del envelope
universal, la politica de madurez/correccion temporal de eventos
(provisional|closed|correction|reemission) en los schemas de las
familias afectadas (market.*, datasource.*), no como ranura global.

--- ADR-007: Modelo temporal operativo ---
Enunciado: semantica del modelo temporal (asignacion, inmutabilidad,
herencia, formato, Clock, madurez/correcciones).
Opciones: A contrato temporal operativo completo; B tres timestamps sin
contrato; C un solo timestamp.
Decision de Alvaro: ACEPTADA opcion A (2026-07-06).
- Asignacion: event_time lo fija el origen del hecho (heredado en
  derivados), nunca lo inventa el que procesa; ingestion_time una vez en
  el connector de borde, no se sobreescribe; processing_time por cada
  emision.
- Inmutabilidad: event_time e ingestion_time inmutables; processing_time
  propio de cada emision.
- Herencia: los derivados declaran su ancla via time_anchor_ref
  (datasource.value_updated = event_time base o window_end; rule.firing
  = event_time del trigger; notification.dispatched = instante del
  intento, no hereda).
- Formato canonico (REST-19): UTC epoch milliseconds (int64) en cable;
  resolucion al milisegundo; prohibidos timestamps naive/locales; ISO
  8601 UTC solo para display/logs; conversion a zona del usuario en
  cliente.
- Clock: todo componente que cree/transforme eventos, procese ventanas,
  evalue reglas, notifique o calcule expiraciones recibe Clock/
  TimeProvider inyectado y lo DECLARA en su manifest (DAP-7); prohibido
  time.time()/datetime.now() dispersos. Habilita SimulatedClock para
  backtesting (REST-12) sin tocar la logica.
- Madurez y correcciones: maturity_state (provisional|closed|correction|
  reemission) modelado en el SCHEMA de las familias que lo necesitan
  (market.*, datasource.*), no como campo universal del envelope
  (respeta ADR-003). Watermark por stream_key; late_event_policy y
  out_of_order_policy por stream/consumidor; una correction no muta el
  original (append-only), emite evento nuevo que referencia el
  idempotency_key corregido y dispara recomputo. Velas: candle_updated
  (provisional) / candle_closed (closed, trigger canonico) /
  candle_corrected (correction).
Criterios: C1 cierra la ambiguedad temporal de v4 (L3), encaja con
REST-19 e INFORME 6/8; C2 watermark + source_sequence + maturity_state
dan resiliencia a reconexion/replay/correcciones; C3 Clock inyectable
simplifica test y backtesting, append-only evita event sourcing pleno;
C4 modelo neutral, no acopla el tiempo a trading.
Test v5.1+ (marginal): punto de dibujo anclado por event_time y asociado
a precio/timeframe, reacciona a candle_corrected; detector emite
datasource.pattern_detected y se recomputa ante correction/reemission;
alertas rule.*/alert.* sin cambiar envelope. Anclaje fino de dibujos
anotado como diseno de la DataSource de dibujo para v5.1. OK.
Frontera: ninguna (tecnica).
Trade-offs: todo componente temporal debe recibir y declarar Clock
(verificado DAP-7/CI); maturity_state y correcciones anaden recomputo
aguas abajo (necesario); estrategia de watermark se concreta en INFORME
7.
Cruces: DAP-2 (ranuras; maturity_state va por schema de familia, no
ranura global), DAP-7 (Clock en manifest), DAP-6 (lifecycle de
Componentes, distinto de maturity_state de datos), DAP-13 (reglas sobre
event_time/candle_closed), DAP-17 (watermark), DAP-5 (streams). INFORME
8 sec.6-7, INFORME 6, INFORME 7; L3; REST-19/12.
ADR-007. Estado: Aceptado (2026-07-06).

-------------------------------------------------------------------
BLOQUE C - Sustrato de componentes
-------------------------------------------------------------------
DAP-7 - Capacidades declarativas por componente   [CERRADA]

--- ADR-008: Manifest de componente tipado y versionado ---
Enunciado: como declara cada componente sus capacidades (formato,
campos, validacion, versionado, obligatorio vs opcional).
Opciones: A manifest tipado Pydantic (versionado, serializable a JSON/
YAML); B YAML/JSON suelto a mano; C introspeccion/decoradores sin
manifest explicito.
Decision de Alvaro: ACEPTADA opcion A (2026-07-06).
- El manifest es un modelo Pydantic ComponentManifest tipado y validado,
  con manifest_schema_version propio (evoluciona bajo ADR-005). Pydantic
  v2 es la FUENTE de autoria/validacion y exporta JSON Schema (ADR-006);
  se serializa a artefacto JSON/YAML para que DAP-3 decida el mecanismo
  fisico de discovery. NO obliga a importar codigo arbitrario para
  descubrir componentes.
- Campos: identity (id, version, manifest_schema_version, type -enum
  ABIERTO, entendido como vocabulario controlado y validado por schema,
  extensible via manifest_schema_version, no string libre-);
  produces/consumes (referencian schemas en shared-contracts, ADR-004);
  requires (Clock/TimeProvider -ADR-007-, DB, EventBus, servicios,
  componentes o capacidades); capabilities (BLOQUE GENERICO extensible:
  datasources, notification_channels, connector_capabilities,
  ui_capabilities, exporter_capabilities, auth_capabilities,
  execution_capabilities, custom_capabilities kind+schema_ref+version);
  capabilities.datasources (declaracion de DataSources de INFORME 6
  sec.12.2: id canonico, tipo de dato, evaluation_contexts, unidades de
  historia, params, servibilidad, reglas semanticas, shared_evaluation,
  sharing_scope, cache_key_schema; es una capability especializada, no la
  unica); ui (panel, widget, config_screen, supported_surfaces);
  policy_requirements (permissions_required, feature_flags_required,
  entitlements_required, sensitive_capabilities -connect_broker,
  execute_order, activate_autotrade-; DAP-7 solo DECLARA, DAP-11 decide);
  config_schema (JSON Schema del config).
- Validacion en dos capas: ESTATICA en discovery (estructura, campos
  requeridos, schemas referenciados existen, capabilities bien formadas)
  y SEMANTICA en registro/runtime (dependencias resolubles, Clock si es
  temporal, permisos/flags declarados validos, servibilidad coherente).
  Minimo obligatorio: id, version, manifest_schema_version, type; resto
  obligatorio-si-aplica.
Criterios: C1 encaja con ADR-001 (manifest = contrato transversal, no
solo DataSources), ADR-004/006/007, INFORME 5 (tres capas) y 6; habilita
CE-14. C2 grafo de dependencias y validacion de cableado automaticos;
escala sin registro manual (cierra L4). C3 un modelo Pydantic tipado sin
deriva codigo/YAML. C4 capabilities generico + type abierto sirven a
connectors, notificaciones, UI, exporters, auth y ejecucion; neutral.
Test v5.1+ (CORE): (a) dibujo/detector via capabilities.datasources
(datasource.drawing_updated / datasource.pattern_detected) sin cambiar
formato; (b) componente NO-DataSource (canal de notificacion, exporter,
connector nuevo) via capabilities/custom_capabilities, tampoco cambia el
formato. PASA aditivo.
Frontera: ninguna; declara permisos/flags/entitlements pero NO decide su
politica (DAP-11/10).
Trade-offs: disciplina de mantener el manifest fiel (mitigado: es la
declaracion tipada del propio codigo); versionado propio del formato
(gobierno pequeno); el como se descubre/carga es DAP-3.
Cruces: DAP-3 (discovery), DAP-6 (lifecycle), DAP-8 (rol Componente),
DAP-2/ADR-004 (schemas), DAP-4/ADR-007 (Clock), DAP-13/15 (catalogo
DataSources), DAP-11 (policy_requirements), DAP-9 (ui), DAP-16
(execution_capabilities). INFORME 2/5/6/8.
ADR-008. Estado: Aceptado (2026-07-06).
DAP-3 - Plugin discovery   [CERRADA]

--- ADR-009: Plugin discovery por convencion de carpetas + manifest ---
Enunciado: mecanismo fisico de descubrir, leer el manifest y registrar
componentes; seguridad de carga, testing, (no) hot-reload.
Opciones: A entry points en pyproject.toml (reinstalar paquete); B
decoradores + auto-import (import con efectos); C convencion de carpetas
+ manifest declarativo escaneado al arranque; D combinacion.
Decision de Alvaro: ACEPTADA opcion C como mecanismo principal, con D
(entry points de terceros) como extension declarada (2026-07-06).
- Cada componente vive en su carpeta bajo una raiz de componentes
  (components/<nombre>/manifest.json + component.py); el discovery
  ESCANEA al arranque y lee el manifest (artefacto JSON/YAML que ADR-008
  hace serializable), sin importar codigo arbitrario.
- Secuencia: leer manifest -> validar (capa estatica de ADR-008) ->
  registrar componente -> publicar sus DataSources al catalogo (INFORME
  6 sec.12.3) -> SOLO despues cargar explicitamente el entrypoint
  declarado en el manifest.
- Cumple CE-14 (copiar carpeta + reiniciar) sin reinstalar paquete
  (evita el punto flojo de A) ni auto-import con efectos (evita el de B).
- Seguridad: solo carpetas bajo la raiz confiable; manifest validado
  antes de tocar codigo. Sin hot-reload en v5.0 (discovery al arranque/
  reinicio); el runtime en caliente -activar/pausar sin reiniciar- es
  DAP-6.
- Extension D: componentes de terceros empaquetados (wheels) se admiten
  ADEMAS por entry points; ambos caminos terminan en el mismo manifest
  validado. No obligatorio en v5.0.
Reglas de implementacion (precision del CSA, no cambian la decision):
manifest invalido -> no se importa codigo; id/version duplicados ->
error de discovery; en CI/dev manifest invalido -> fallo fuerte; en
produccion, fail-fast vs quarantine se coordina con DAP-6; el resultado
del discovery debe ser observable (descubiertos, registrados,
rechazados y motivo).
Criterios: C1 encaja con ADR-008 (lee manifest como artefacto), ADR-002
(dentro del proceso modular), INFORME 2 sec.9 e INFORME 6 sec.12.3;
cierra L4. C2 escanear carpetas escala a decenas/cientos de componentes
sin registro manual. C3 el mecanismo mas simple de entender/testear/
operar para equipo de dos. C4 neutral: descubre cualquier tipo de
componente por igual.
Test v5.1+ (CORE): un Componente nuevo (dibujo, detector) se anade
copiando su carpeta con su manifest; discovery lo escanea, valida,
registra y publica sus DataSources, sin tocar el core ni el mecanismo.
PASA aditivo.
Frontera: ninguna (tecnica).
Trade-offs: sin hot-reload en v5.0 (anadir/quitar componente pide
reinicio del worker afectado; runtime en caliente es DAP-6); convencion
de carpetas exige disciplina (mitigada por validacion de manifest);
soporte de terceros (D) queda como via declarada.
Cruces: DAP-7/ADR-008 (manifest), DAP-6 (REGISTER es la salida del
discovery; caliente es suyo), DAP-8 (rol Componente), DAP-13/15
(catalogo DataSources), DAP-11 (policy_requirements del manifest).
INFORME 2 sec.9, INFORME 6 sec.12.3; CE-14; L4.
ADR-009. Estado: Aceptado (2026-07-06).
DAP-6 - Lifecycle de componentes   [CERRADA]

--- ADR-010: Lifecycle de ComponentInstance ---
Enunciado: maquina de estados de lifecycle comun a toda
ComponentInstance (estados, ambito, emision al bus, PAUSE,
dependencias, salud/readiness, failure modes).
Distincion base: ComponentDefinition (lo que DAP-3 descubre desde
carpeta+manifest; global; component_id/version/type/capabilities) vs
ComponentInstance (lo que DAP-6 arranca/pausa/detiene/falla;
component_instance_id; lifecycle_scope global|tenant|user;
tenant_id/user_id si aplica). Un binance_connector:v1 (Definition)
tiene N Instances (una por tenant/user BYOC).
Opciones: A maquina explicita unica con supervisor; B lifecycle ad-hoc
por tipo; C sin lifecycle en runtime (v4).
Decision de Alvaro: ACEPTADA opcion A (2026-07-06).
- El lifecycle se aplica a ComponentInstance. DAP-3 registra
  Definitions; el supervisor de DAP-6 registra Instances creadas desde
  ellas. Precision: el estado REGISTERED de la maquina de DAP-6 se
  refiere a la Instance registrada en el supervisor, NO al mero
  descubrimiento de la Definition (que hace DAP-3).
- lifecycle_state (maquina principal, pequena): REGISTERED ->
  INITIALIZING -> INITIALIZED -> STARTING -> RUNNING <-> PAUSED ->
  STOPPING -> STOPPED -> UNLOADED, mas FAILED y QUARANTINED.
- health_status (healthy|degraded|unhealthy) y readiness_status
  (ready|not_ready) SEPARADOS del lifecycle. DEGRADED no es estado de
  lifecycle: es health_status de una instancia RUNNING/PAUSED cuando
  una dependencia opcional cae o una capability queda parcialmente no
  servible.
- Supervisor/registry central posee la maquina (no la reimplementa
  cada componente; mixin opcional de ADR-001) y expone estados a la UI
  de admin.
- Emision al EventBus: cada transicion emite component.* (ADR-004) con
  envelope (ADR-003) y Clock (ADR-007), identificando la instancia:
  component_id, component_version, component_instance_id,
  lifecycle_scope, tenant_id/user_id si aplica, previous_state,
  new_state, health_status, readiness_status, reason/error_code.
- Ambito: mismo contrato para instancias globales y por-usuario/tenant
  (connector BYOC, gestor de API keys), que se instancian/paran por
  (tenant,user) al anadir/retirar su key; el gate geo/plan (DAP-11)
  actua ANTES de INITIALIZE. El ambito lo declara el manifest (DAP-7).
- PAUSE: detiene consumo, conserva registro y posicion en el stream
  (offset/stream_key) sin buffer ilimitado; el hueco se recupera por
  replay desde el offset (ADR-003/007), no por buffer en memoria. El
  manifest puede declarar drain vs descarte.
- Dependencias (grafo de requires del manifest): arranque topologico;
  dependencia obligatoria caida -> PAUSED o FAILED segun politica
  por-arista (esperar|degradar|fallar); dependencia opcional caida ->
  RUNNING con health_status=degraded y capability afectada not_ready.
- Failure modes: fallo en INITIALIZE -> no pasa a RUNNING; rollback de
  registros parciales; pasa a FAILED. En CI/dev fail-fast; en
  produccion QUARANTINE por defecto (aislada, observable, sin tumbar el
  resto) con reintentos backoff acotado; instancia critica puede
  declararse fail-fast en su manifest. Todo failure emite component.*.
Criterios: C1 cierra el contrato Lifecycle de ADR-001; usa component.*
(ADR-004), envelope (ADR-003), Clock (ADR-007), grafo del manifest
(ADR-008), REGISTER del discovery (ADR-009); recoge ambito por-usuario
de INFORME 5; Definition/Instance y health separado eliminan las dos
ambiguedades; cierra L4. C2 instance_id + scope hacen observable/
controlable el runtime a miles de instancias; replay desde offset (no
buffer) evita fuga de memoria. C3 una maquina principal pequena en un
supervisor unico; mas simple en monolito modular (INFORME 2 sec.9). C4
lifecycle neutral e identico para todo tipo; el trading entra solo por
gate de policy (DAP-11), no en la maquina.
Test v5.1+ (marginal): Componente de dibujo/detector -> Definition
drawing_datasource:v1; Instance global|tenant|user; lifecycle
REGISTERED->INITIALIZING->STARTING->RUNNING sin estados nuevos; si su
fuente cae, politica de dependencia existente (degraded/PAUSED/FAILED/
QUARANTINED). OK.
Frontera: ninguna; el gate geo/plan previo a INITIALIZE se declara aqui
como punto de aplicacion, su politica es DAP-11 y la regulacion es de
Alvaro.
Trade-offs: supervisor/registry es infraestructura nueva (corazon del
sustrato); replay exige consumidores idempotentes (garantizado por
idempotency_key de ADR-003); quarantine+backoff+health/readiness anaden
supervision (proporcional a always-on de INFORME 7).
Cruces: DAP-7/ADR-008, DAP-3/ADR-009, DAP-8/ADR-001, DAP-2 (ADR-003/
004), DAP-4/ADR-007, DAP-11 (gate), DAP-17 (offset/replay), DAP-16
(BYOC). INFORME 2 sec.9, INFORME 5 sec.9, INFORME 7, INFORME 8.
ADR-010. Estado: Aceptado (2026-07-06).

-------------------------------------------------------------------
BLOQUE D - Plataforma multiusuario
-------------------------------------------------------------------
DAP-10 - Modelo de tenancy   [CERRADA]

Frontera de alcance: DAP-10 decide el MECANISMO de aislamiento
(tecnico). Que jurisdicciones/planes existen y su obligatoriedad
regulatoria es politica de Alvaro.

--- ADR-011: Modelo de tenancy multiusuario ---
Enunciado: mecanismo de aislamiento de datos por tenant, disciplina
que arrastra, resolucion server-side del tenant y clasificacion
obligatoria de tablas.
Opciones: A shared-schema + RLS; B schema-per-tenant; C database-per-
tenant; D hibrido.
Decision de Alvaro: ACEPTADA opcion A como modelo base, con D declarada
(no construida en v5.0) (2026-07-06).
- Shared-schema + RLS con tenant_id en toda entidad por-tenant. tenant
  es ABSTRACCION: hoy 1:1 con usuario, pero la pertenencia
  (usuario -> tenant) es una capa aparte, de modo que introducir
  organizaciones/equipos despues sea ANADIR pertenencia, no reescribir
  el aislamiento.
- Resolucion de tenant (TenantContextResolver): el tenant efectivo de
  cada request/job lo resuelve EXCLUSIVAMENTE el backend a partir de la
  sesion autenticada y de la tabla user_tenant_membership; valida la
  pertenencia activa user -> tenant (y estado de cuenta/tenant si
  aplica) y solo entonces fija app.current_tenant_id con SET LOCAL
  dentro de la transaccion. El cliente puede SOLICITAR un tenant activo
  pero NUNCA imponerlo; sin pertenencia valida, la operacion FALLA
  CERRADA.
- Clasificacion obligatoria de tablas: toda tabla persistida declara
  isolation_scope: public_market | tenant | user | system (mismo
  vocabulario que el scope del envelope, ADR-003).
- Disciplina RLS obligatoria: identidad por transaccion (SET LOCAL /
  set_config('app.current_tenant_id', <id>, true)); rol de app sin
  BYPASSRLS ni SUPERUSER; el rol de migraciones nunca corre en runtime;
  checks de CI que FALLAN si una tabla tenant/user no tiene tenant_id,
  si una tabla user no tiene user_id/owner_user_id cuando aplique, si
  una tabla tenant/user no tiene RLS, si una tabla sin tenant_id no esta
  allowlisted como public_market/system, o si una policy RLS no usa el
  tenant context transaccional; tests de aislamiento en CI en cada build;
  claves de cache con tenant_id; caches derivadas de rol/premium/
  jurisdiccion/KYC se invalidan al cambiar esas senales (mecanismo en
  DAP-11).
- Defensa en profundidad: ademas de RLS, filtrado por tenant en la capa
  de aplicacion (scope global del ORM/repositorio) como segunda barrera;
  no sustituye a RLS, se suma.
- Nota de implementacion (CSA): en v5.0, user_tenant_membership puede
  inicializarse automaticamente con una pertenencia unica por usuario;
  no implica soportar organizaciones en producto, solo deja preparada la
  costura.
Criterios: C1 encaja con ADR-003 (mismo scope en DB), INFORME 5
(auth/authz/flags) y gate de ADR-010; el resolver materializa "el
cliente no impone tenant". C2 shared-schema+RLS es el modelo denso
eficiente para decenas->miles sin refactor; membership evita reescribir
el aislamiento al anadir organizaciones. C3 menor peso operativo para
equipo de dos; los ajustes son disciplina, no arquitectura pesada. C4
neutral; sirve a cualquier entidad por-tenant, no solo trading.
Test v5.1+ (N/A): una DataSource de dibujo por-usuario sera una entidad
isolation_scope=user con tenant_id/user_id, RLS y cache tenant-aware,
como cualquier otra. Sin objecion.
Frontera: el mecanismo es tecnico; jurisdicciones/planes, obligatoriedad
de KYC y seleccion de proveedores (auth, KYC, KMS) son de Alvaro.
Trade-offs: RLS + resolver + clasificacion arrastran disciplina estricta
(obligatoria: una RLS mal puesta o tabla sin clasificar es fuga);
db-per-tenant queda como via D declarada; RLS puede complicar debugging
de politicas ricas (mitigado con filtrado de app).
Cruces: DAP-11 (flags tenant-scoped; invalidacion de caches), DAP-2/
ADR-003 (scope, tenant_id), DAP-6/ADR-010 (gate de policy; BYOC por-
usuario), DAP-16 (ApiKey/ExecutionProfile tenant-scoped), DAP-5 (streams
privados). INFORME 5 sec.1-2, INFORME 7; L8, REST-5, OBJ-2.
ADR-011. Estado: Aceptado (2026-07-06).
DAP-11 - Modelo de feature flags de plataforma   [CERRADA]

Frontera de alcance: DAP-11 decide el MECANISMO (evaluador, entradas,
precedencia, kill switches, cache, invalidacion, enforcement,
auditoria). QUE jurisdicciones bloquean que, que da premium y la
obligatoriedad de KYC son POLITICA de Alvaro.

--- ADR-012: Feature flags como PolicyEvaluator central ---
Enunciado: modelo unico de feature flags transversal (evaluacion,
entradas/salida, precedencia DENY>ALLOW, kill switch, jerarquia de
confianza, cache/TTL, enforcement, auditoria).
Opciones: A PolicyEvaluator central propio; B flags dispersos por
modulo; C servicio de terceros como motor principal.
Decision de Alvaro: ACEPTADA opcion A, con C como backend futuro no
requerido en v5.0 (2026-07-06).
- Evaluacion: un PolicyEvaluator central resuelve, por sujeto
  (tenant/usuario), un capability set. API y UI consultan el MISMO
  resultado: UI oculta/deshabilita (cortesia), el endpoint aplica
  (seguridad). El capability set que consume la UI es INFORMATIVO; la
  decision autoritativa es siempre la reevaluacion/validacion backend
  en el endpoint sensible.
- Entradas -> salida: jurisdiccion (IP+KYC con precedencia), IP/VPN,
  rol/plan, entitlements, overrides, kill switches, config -> decisiones
  POR CAPABILITY ALLOW|DENY|NOT_APPLICABLE, cada una con reason_code y
  policy_version.
- Resolucion y precedencia: NO es suma de flags positivos. Para
  capacidades SENSIBLES (connect_broker, execute_order,
  activate_autotrade, manual_order, manage_api_key), cualquier DENY
  activo (kill switch, jurisdiccion, KYC no valido, plan insuficiente,
  entitlement ausente, policy no disponible o cache stale) PREVALECE
  sobre cualquier ALLOW inferior. Los overrides tenant/user solo
  conceden dentro del perimetro permitido; nunca saltan un DENY
  superior/de sistema/estado desconocido.
- Kill switch / DENY de sistema (INFORME 7): entrada de PRIMERA CLASE.
  Scopes: global, exchange, connector, tenant/user, market_scope,
  capability. Decision efectiva = union de bloqueos activos; dentro del
  mismo nivel DENY gana; un scope mas amplio bloquea inferiores. Los
  cambios invalidan caches y se propagan por evento SIN reiniciar
  procesos; toda activacion/desactivacion se audita.
- Jerarquia de confianza (INFORME 5 sec.5; el "que" es de Alvaro):
  tabla de politica CONFIGURABLE de senales (kyc_country, ip_country,
  vpn/proxy, kyc_status) y su precedencia. La arquitectura fija el
  mecanismo y que sea configurable; los valores por jurisdiccion los
  pone Alvaro.
- Persistencia/herencia: defaults por PLAN -> overrides tenant ->
  overrides usuario (tenant-scoped, RLS de ADR-011); reglas/overrides
  son datos versionados (ADR-005), no codigo.
- Cache e invalidacion (cierra lo que ADR-011 remitio): el capability
  set cacheado incluye tenant_id, user_id, policy_version,
  input_versions y evaluated_at. Invalidacion POR EVENTO como
  mecanismo principal; ademas max_staleness/TTL acotado como red de
  seguridad. En endpoints SENSIBLES, capability set expirado, stale, de
  policy_version no vigente o no recomputable => DENY (fail-closed). En
  capacidades NO sensibles se puede degradar con cache stale si la
  politica lo declara, NUNCA para ejecucion, API keys, autotrade ni
  acciones financieras.
- Enforcement: backend a nivel API en todo endpoint sensible (403 si no
  esta ALLOW); UI consume el mismo set.
- Auditoria: evaluaciones y bloqueos sensibles (bloqueos de trading,
  kill switches, cambios de jurisdiccion, concesiones premium) como
  eventos auditables (SensitiveActionAudit).
Criterios: C1 evaluador unico consumido por API y UI evita divergencia
entre INFORME 3/4/7/9; integra kill switch (INFORME 7), jerarquia
(INFORME 5) y fail-closed (INFORME 9); encaja con ADR-011/010/008/004/
005 y REST-16. C2 capability set cacheado por tenant/user +
policy_version + invalidacion por evento + TTL escala sin permisos
obsoletos. C3 evaluador propio sin SaaS externo, proporcional a equipo
de dos; tabla de politica configurable evita tocar codigo. C4 generico
(capabilities, no "trading flags"); trading es un subconjunto sensible.
Test v5.1+ (marginal): advanced_drawing, pattern_detection,
pattern_alerts, premium_overlay_pack entran como capabilities en plan/
tenant/user overrides, sin modelo nuevo. OK.
Frontera: el motor se decide aqui; jurisdicciones/premium/KYC y
seleccion de proveedores (KYC, geolocalizacion/VPN) son de Alvaro.
Trade-offs: construir el evaluador + cache/invalidacion + kill switch es
infraestructura transversal (evita divergencia); fail-closed puede
denegar de mas ante fallo/staleness (correcto en sensibles); la
jerarquia depende de la calidad del proveedor VPN/KYC (Alvaro).
Cruces: DAP-10/ADR-011 (tenant-scoped, invalidacion), DAP-6/ADR-010
(gate previo a INITIALIZE), DAP-7/ADR-008 (feature_flags_required),
DAP-2 (eventos de invalidacion; ADR-004/005), DAP-17 (kill switch por
el bus; Execution Gate), DAP-16 (enforcement BYOC/ejecucion), DAP-9/12
(UI consume el set). INFORME 5 sec.2/5/7, INFORME 3 (G2/G3), INFORME 7,
INFORME 9; REST-16, OBJ-1/9.
ADR-012. Estado: Aceptado (2026-07-06).

-------------------------------------------------------------------
BLOQUE E - Transporte operativo
-------------------------------------------------------------------
DAP-17 - Sustrato de EventBus, colas y workers   [CERRADA]

DAP-2 fijo los CONTRATOS (la carga); DAP-17 fija el TRANSPORTE (el
cable). El broker implementa el contrato, no lo sustituye.

--- ADR-013: Sustrato operativo de EventBus, colas y workers ---
Enunciado: transporte de mensajeria entre componentes, capacidades
operativas obligatorias, abstraccion, tecnologia v5.0 y garantias
end-to-end (retencion/replay, idempotencia de consumidor, outbox/inbox).
Opciones: A transporte EXTERNO con capacidades operativas obligatorias
(tecnologia abierta); B bus in-process (rechazada: deuda de v4, no
soporta workers separados).
Decision de Alvaro: ACEPTADA opcion A (2026-07-06).
- Capacidades operativas OBLIGATORIAS (contrato del sustrato,
  independiente del broker): at-least-once; acks; retries con backoff;
  DLQ observable; backpressure; consumer groups; ordering por stream_key;
  particionado (por stream_key o tenant); equivalente local en docker-
  compose (mismo transporte local y prod); metricas (lag, cola,
  reintentos, DLQ).
- Abstraccion (REST-15): productores/consumidores hablan con una interfaz
  PROPIA EventBus/Queue (publish/subscribe/ack por contrato), no con la
  API nativa del broker; cambiar de backend es cambiar el adaptador. El
  broker es una capability/Componente con manifest y lifecycle (DAP-6/7).
- Tecnologia v5.0 (con INFORME 2): Redis Streams como transporte inicial
  (consumer groups nativos, ordering por stream, latencia sub-ms, ya
  presente por la cache de ADR-011/012, peso operativo bajo). Kafka
  DESCARTADO (sobredimensionado; E6). NATS/JetStream ANOTADO con cautela
  como candidato de escalado (gobernanza CNCF/BSL abril 2025; hallazgos
  Jepsen; fsync no inmediato por defecto). PG LISTEN/NOTIFY no basta.
- Retencion y replay: Redis Streams es transporte operativo de corto/
  medio plazo, NO historico canonico indefinido. Cada familia/stream
  declara politica (max_age, max_len aprox, prioridad, destino historico).
  Trimming seguro solo por ventana suficiente para PAUSE/replay, o tras
  avance de watermark/ack de los consumer groups, o aceptando que la
  recuperacion venga de fuente persistida externa. Offset ya eliminado ->
  reconstruye desde fuente canonica/historica o entra en FAILED/
  QUARANTINED observable (ADR-010); nunca avanza en silencio. El historico
  canonico persistente vive en la DB append-only, no en el broker.
- Idempotencia de consumidores: idempotency_key es la identidad logica
  del hecho, NO la garantia automatica de idempotencia. Todo consumidor
  con efectos persistentes registra su procesamiento por consumer_group/
  handler/idempotency_key (ledger) o usa unique constraints/upserts/
  compare-and-set; ACK SOLO tras persistir el efecto (reintento tras
  efecto y antes de ACK no duplica). Critico para execution.*,
  notification.*, billing.*, alert.*, signal.*, component.*.
- Outbox/inbox DB-bus: los eventos que nacen de una transaccion de DB se
  escriben PRIMERO en una outbox transaccional en la misma DB (mismo
  commit que el cambio de estado); un publisher worker publica la outbox y
  marca enviado de forma idempotente; los consumidores con efectos aplican
  inbox/dedup o constraints antes del ACK. Garantiza at-least-once end-to-
  end DB<->broker (cierra "commit OK + publish falla" y "publish OK +
  rollback"), sin Kafka ni event sourcing.
- DLQ: eventos que agotan reintentos van a DLQ observable, monitorizada y
  reprocesable. La entrada de DLQ incluye owner operativo, reason_code,
  numero de intentos, first_seen_at, last_seen_at y procedimiento de
  reproceso (manual/automatico).
Criterios: C1 canal del contrato de INFORME 8 (REST-3/4); soporta
idempotency_key/stream_key (ADR-003), replay/watermark (ADR-007), replay
en PAUSE (ADR-010), kill switch (ADR-012); retencion completa el replay
por offset y outbox/inbox cierra la consistencia DB-evento; cierra el bus
informal de v4 (L1). C2 consumer groups + particionado + backpressure
escalan los workers always-on; la abstraccion permite migrar de broker;
la retencion evita memoria sin control y trimming de eventos aun
necesarios. C3 capacidades fijas pero broker ligero (Redis Streams, ya
presente); outbox/idempotencia/retencion son disciplina minima; sin
divergencia dev/prod. C4 transporte NEUTRAL para todas las familias;
execution.* es una familia mas.
Test v5.1+ (marginal): transporta datasource.drawing_updated,
datasource.pattern_detected, alert.pattern_firing sin cambio de
transporte; si un detector se pausa y reanuda por offset, la politica de
retencion de su stream determina si hay replay o reconstruccion desde
historico. PASA.
Frontera: ninguna; el proveedor gestionado de Redis lo decide Alvaro con
INFORME 7.
Trade-offs: transporte externo + outbox + ledgers de idempotencia es
infraestructura a operar (frente al bus in-process "gratis" que era la
deuda de v4); Redis Streams memory-bound (mitigado: retencion + historico
en DB + abstraccion para migrar); at-least-once obliga a consumidores
idempotentes (disciplina obligatoria, apoyada en idempotency_key ADR-003).
Cruces: DAP-2/ADR-003, DAP-4/ADR-007, DAP-5, DAP-6/ADR-010, DAP-7/ADR-008,
DAP-10/ADR-011 (outbox en DB tenant-scoped; historico canonico), DAP-11/
ADR-012 (kill switch por el bus), DAP-16 (execution.* y colas). INFORME
7/2/8; REST-3/4/15, L1.
ADR-013. Estado: Aceptado (2026-07-06).
DAP-5 - Estrategia de streams de market data   [CERRADA]

Corre sobre el sustrato de ADR-013 y bajo el modelo tenant de ADR-011.

--- ADR-014: Streams de market data hibridos ---
Enunciado: estrategia de ingesta/distribucion de market data, frontera
publico/privado, fuente de la demanda de suscripcion y clave del stream.
Opciones: A compartidos (insuficiente para privados); B por-usuario
(explosion N usuarios x M pares, descartada); C hibrido.
Decision de Alvaro: ACEPTADA opcion C (2026-07-06).
- Publico (market.*, scope=public_market, sin tenant_id): un stream por
  flujo publico compartido cross-tenant; se ingiere una vez y se
  multiplexa a los interesados.
- Privado (execution.*/fills/balance, scope=user, tenant_id+user_id,
  RLS de ADR-011): por-usuario, solo BYOC en jurisdiccion habilitada;
  el geo-gate (ADR-012) los reduce a la fraccion no-UE; connector BYOC
  como Componente por-usuario (ADR-010). Coste O(pares_unicos) +
  O(usuarios con broker activo), no el producto usuario x par.
- Fuente de demanda - MarketInterestRegistry/SubscriptionIntent: la
  demanda NO viene solo de watchlists, sino de la union de
  SubscriptionIntents de watchlists, widgets/layouts, AlertRules,
  TradingSignalRules, ExecutionPlans, DataSources, backfill/replay y
  detectores v5.1. Caso critico (INFORME 4): una AlertRule/
  TradingSignalRule activa mantiene vivo su stream 24/7 aunque la PWA no
  este abierta. El ref-count es estado operativo RECONSTRUIBLE, no
  fuente de verdad; tras reinicio se reconstruye desde entidades
  persistidas y reglas activas.
  Nota de implementacion (CSA): cada SubscriptionIntent deberia incluir
  source_type, source_ref, MarketStreamKey, priority, created_at/
  updated_at y, opcionalmente, lease_ttl para intereses efimeros de UI
  (los persistentes de reglas/alertas no dependen de TTL; los efimeros
  de widgets abiertos pueden caducar para evitar suscripciones zombis).
- Clave del stream - MarketStreamKey: el stream compartido se identifica
  por MarketStreamKey = exchange + instrument/symbol + data_family +
  granularidad aplicable (timeframe para candles, depth/channel para
  orderbook, tipo para trades/ticker), no solo por (exchange, par). El
  subscription manager deduplica por MarketStreamKey y el stream_key del
  envelope (ADR-003) se DERIVA de ella de forma determinista (ancla
  ordering y watermark).
- Ingesta como Componente (ADR-001/008/010): ingestor publico y
  connector privado declaran produces market.*/execution.*.
- Subscription manager: ref-count por MarketStreamKey; suscribe al
  primer intent, desuscribe con histeresis anti-flapping; respeta
  limites del exchange (fair-use operativo, INFORME 7).
- Reconexion: bootstrap REST + replay/retencion del sustrato (ADR-013);
  velas corregidas -> candle_corrected (ADR-007); historico canonico en
  DB. Fault isolation por stream (FAILED/QUARANTINED de ADR-010).
Criterios: C1 encaja con ADR-003 (scope; MarketStreamKey alineado con
stream_key), ADR-007, ADR-010, ADR-011, ADR-012, ADR-013; reconoce que
reglas/alertas generan demanda, no solo watchlists. C2 compartir
publicos rompe la explosion de conexiones; el registry evita infra-
suscribir datos de evaluadores backend. C3 subscription manager con
ref-count e histeresis reconstruible es pieza razonable; el ajuste solo
cambia la fuente de demanda. C4 market data publica compartida +
ejecucion privada opcional; cualquier consumidor futuro pide datos via
SubscriptionIntent sin acoplarse al dashboard ni a trading.
Test v5.1+ (N/A con matiz): un detector futuro genera demanda
(pattern_detector -> SubscriptionIntent(BTCUSDT, candles:1m) -> reusa o
suscribe), sin modelo nuevo. OK.
Frontera: mecanismo tecnico; que exchanges y en que jurisdicciones se
habilita lo privado es politica de Alvaro.
Trade-offs: MarketInterestRegistry + subscription manager con
reconstruccion tras reinicio es pieza a construir (evita infra-
suscripcion y reventar limites); los publicos compartidos son punto de
agregacion (mitigado con reconexion + replay + fault isolation por
MarketStreamKey); fair-use por exchange se dimensiona en INFORME 7.
Cruces: DAP-17/ADR-013, DAP-10/ADR-011, DAP-11/ADR-012, DAP-2/ADR-003
(stream_key derivado de MarketStreamKey), DAP-4/ADR-007, DAP-6/ADR-010,
DAP-13 (reglas/alertas generan SubscriptionIntent), DAP-16 (fills
privados), DAP-9 (charting/dashboard consume market.* y genera
SubscriptionIntent por widgets/layouts). INFORME 5 sec.9, INFORME 4,
INFORME 7, INFORME 9.
ADR-014. Estado: Aceptado (2026-07-06).

-------------------------------------------------------------------
BLOQUE F - Motor de reglas
-------------------------------------------------------------------
DAP-13 - Arquitectura unificada de reglas + lenguaje   [CERRADA]

Primera del Bloque F. Fija el CANON del motor; DAP-14/15 dependen de
el. Guardian del criterio 4: signal.* es hija de rule.*, no el eje.

--- ADR-015: Motor de reglas unificado (raiz Rule neutral) ---
Enunciado: arquitectura del motor de reglas (raiz + especializaciones,
estructura del lenguaje, proyeccion rule/signal/alert, veto, trigger,
transicion de estado, doble ciclo, funciones canonicas, complexity
budget).
Opciones: A una maquinaria (Rule neutral) + dos productos v5.0; B dos
motores separados; C estructura v4 fija de 3 grupos (descartada por A2).
Decision de Alvaro: ACEPTADA opcion A (2026-07-06).
- Raiz Rule NEUTRAL (sin symbol/exchange): rule_id, tenant_id (RLS),
  name, target_binding (neutral, declarativo/serializable),
  trigger_policy, groups 1..N, veto opcional, combine por niveles,
  schema_version, enabled. grupo = evaluation_context + etiqueta de
  dominio opcional (metadato) + 1..M features; feature = 1..K
  condiciones, max 3 fuentes distintas. La raiz se llama Rule; hojas
  AlertRule y TradingSignalRule; "evaluation" nombra el ciclo, no la
  clase.
- Especializaciones v5.0 (mercado en la hoja): TradingSignalRule (Rule
  + market_scope {exchange,symbol}; overlay universal + autotrade
  gateado por ADR-012/DAP-16); AlertRule (Rule + market_scope +
  notification_policy_ref + attention lifecycle con ACK +
  attention_termination_policy). AlertRule de INFORME 4 se mapea 1:1
  (confirm_on_close -> parametro de trigger_policy candle_close).
- Proyeccion rule.* -> signal.*/alert.* (DAP-2 la dejo aqui): rule.* es
  la FUENTE DE VERDAD NEUTRAL del evaluation lifecycle. La evaluacion
  produce primero rule.* (rule.evaluation_completed, rule.firing,
  rule.resolved). signal.* y alert.* son PROYECCIONES DERIVADAS con
  causation_id hacia el rule.* que las origina: TradingSignalRule
  proyecta rule.firing/resolved -> signal.*; AlertRule -> alert.*.
  alert.acknowledged pertenece SOLO al attention/delivery lifecycle,
  nunca al evaluation. Historial y deduplicacion se anclan en rule.*
  (evita doble conteo). Una hoja nunca emite saltandose rule.*.
- Veto: bloque guardian OPCIONAL con semantica OR por defecto: cualquier
  condicion de veto activa bloquea la transicion a FIRING. No dispara
  por si mismo; solo impide o resuelve el estado activo. Si bloquea, el
  EvaluationResult conserva veto_matched=true, veto_reason/ref y nodos
  responsables. Mientras el veto este activo, NO se proyectan signal.*
  ni alert.*.
- trigger_policy: candle_close | event_arrival | schedule | manual |
  mixed (candle_close dominante v5.0).
- Combinacion por niveles con FORMA CANONICA de alcance declarado
  (condiciones atomicas, arbol booleano normalizado, modos explicitos,
  orden estable, ids estables); equivalencia dentro de las
  transformaciones soportadas, NO semantica arbitraria (el normalizador
  no es demostrador logico); catalogo explicito + hash sobre la forma
  canonica se especifican con INFORME 8; el pipeline es DAP-15.
- Emision por transicion de estado (rule.firing/rule.resolved, ADR-003/
  007). Doble ciclo: evaluation lifecycle universal + attention
  lifecycle (ACK) con attention_termination_policy por producto/canal.
- Funciones canonicas neutrales (naming textual con DAP-14): value_at/
  previous_value, average, change, is_active, elapsed_since, con unidad
  de historia por evaluation_context o tipo de DataSource (bars/events/
  time/ticks). Funciones v4 = referencia, no canon.
- Limites y complexity budget (no se delega a DAP-15): limites por plan
  Y maximo absoluto de plataforma. Base v5.0: grupos hard cap N<=5,
  features/grupo hard cap M<=3, condiciones/feature hard cap K<=5,
  fuentes/feature max 3, nodos booleanos totales max por plan,
  SubscriptionIntents derivados max por plan. La validacion RECHAZA
  reglas que excedan el budget ANTES de persistir o compilar.
- Reglas como datos: Rule y especializaciones son JSON versionado por-
  tenant (ADR-005/011), no-Turing-complete, sandbox; superficies
  textuales (DSL) derivadas (DAP-14); plantillas curadas (la "tripleta
  clasica" como plantilla). DataSource como Componente declarativo
  (ADR-008): la Rule no conoce observables directos, sino DataSources.
- Nota de implementacion (CSA): los nombres exactos de event_type se
  cierran en shared-contracts con consistencia gramatical (p.ej.
  rule.firing/rule.resolved o rule.fired/rule.resolved), sin mezclar
  estado y accion de forma ambigua.
Criterios: C1 cierra la proyeccion rule.*->signal.*/alert.* que DAP-2
dejo aqui; encaja con ADR-003/004/007/008/011/012/014. C2 forma canonica
+ shared_evaluation + budget evitan reglas caras de operar. C3 una
maquinaria (no dos motores); veto y proyeccion escritos evitan
divergencia futura entre Alert Engine, Signal Engine, Notification
Router y ejecucion. C4 raiz Rule neutral; mercado y trading en hojas/
proyecciones, no en la raiz (nucleo del criterio 4).
Test v5.1+ (CORE): datasource.pattern_detected/drawing_updated entran
como DataSources nuevas observadas por la MISMA Rule; alert.* sobre
ellas se proyecta desde rule.* con el mismo attention lifecycle; sin
gramatica ad hoc ni cambio de raiz. PASA aditivo.
Frontera: tecnica; la calificacion regulatoria de "senal" y las
jurisdicciones son de Alvaro (gate en ADR-012); el autotrade real es
DAP-16.
Trade-offs: forma canonica + normalizador con catalogo explicito es
diseno acotado a proposito (no demostrador logico); una gramatica
canonica obliga a DAP-14/15; los hard caps son restricciones de
producto conscientes, ajustables por plan bajo el maximo de plataforma.
Cruces: DAP-14 (localizacion/naming), DAP-15 (pipeline; recibe budget
validado), DAP-2/ADR-003-004, DAP-4/ADR-007, DAP-7/ADR-008, DAP-10/11/
ADR-011-012, DAP-16 (TradingSignalRule -> autotrade gateado), DAP-5/
ADR-014 (reglas -> SubscriptionIntent), DAP-9 (overlay). INFORME 6
(sec.10-11), INFORME 4, INFORME 8.
ADR-015. Estado: Aceptado (2026-07-06).
DAP-14 - Localizacion del lenguaje   [CERRADA]

Segunda del Bloque F. Se apoya en ADR-015 (funciones canonicas
neutrales, reglas como datos, DSL derivado). Guardian de REST-13.

--- ADR-016: Localizacion del lenguaje de reglas por canonico unico ---
Enunciado: en que idioma vive el canon del lenguaje de reglas y como se
localiza la superficie que ve el usuario.
Opciones: A canonico unico (ingles) + chatbot multiidioma + renderizado
localizado; B lexico localizable multi-idioma sobre canonico; C solo
espanol (descartada por A3/REST-13).
Decision de Alvaro: ACEPTADA opcion A (2026-07-06).
- Canon en INGLES y NEUTRAL: keywords estructurales (RULE, GROUP,
  CONDITION, AND, OR, NOT, VETO...) y las funciones canonicas de ADR-015
  (value_at/previous_value, average, change, is_active, elapsed_since)
  se fijan en ingles como IDENTIFICADORES canonicos, NO como texto de
  UI. Es la unica gramatica objetivo del chatbot, validador,
  normalizador y compilador (DAP-15).
- La superficie principal NO es el DSL: el usuario crea via chatbot
  describiendo en su idioma (INFORME 6 sec.14); el canon nunca se le
  impone como texto a escribir. Las explicaciones (por que es asi, por
  que disparo) se RENDERIZAN en el idioma del usuario desde la forma
  canonica, deterministamente. El DSL textual en ingles sobrevive como
  representacion derivada (experto, exportacion, documentacion), fuera
  del camino critico.
- Localizacion = RENDERIZADO, no N gramaticas: la i18n vive en la capa
  de renderizado (obligatoria por REST-13) con catalogos de traduccion
  (keys i18n), no en parsers por idioma. Esto hace el sistema RTL-ready
  (AR v5.1) y CJK-ready (ZH v5.2) sin refactor: anadir idioma = anadir
  catalogo + activar, sin tocar el canon ni el motor.
- Identificadores de usuario en Unicode con normalizacion anti-colision
  (p.ej. NFC + defensa anti-homoglifos/confusables): los nombres de
  reglas/grupos son datos, nunca keywords del canon.
- Display-names de fuentes via catalogo (ADR-008/DAP-7): los ids de
  DataSources son canonicos y estables; su nombre mostrado se traduce
  por catalogo/i18n keys del manifest, no se hardcodea en el canon.
- Nota de implementacion (CSA): los errores de validacion, warnings,
  diagnostics y reason_codes del chatbot/validador se emiten como
  code + params, nunca como texto hardcodeado; la UI los renderiza por
  i18n igual que las explicaciones.
- Migrabilidad: A -> B (lexico nativo por idioma) NO rompe reglas
  guardadas (el canonico persiste igual); B seria capa de superficie
  adicional, no cambio de fuente de verdad. Via declarada, no v5.0.
Criterios: C1 encaja con ADR-015 (funciones neutrales, DSL derivado),
ADR-008 (display-names por catalogo), ADR-005 (schema versionado) y
REST-13; el chatbot tiene UNA gramatica objetivo. C2 una gramatica y un
renderizador escalan a N idiomas por traduccion, no por N parsers;
anadir AR/ZH no toca el motor. C3 N gramaticas localizadas serian caras
y ambiguas (RTL/CJK en keywords); un canon + catalogos i18n es lo mas
barato y da mas fiabilidad al chatbot. C4 canon neutral en ingles no
acopla el lenguaje a un idioma ni a trading; DataSource futura = id
canonico + display traducido.
Test v5.1+ (CORE): una funcion/observable nuevo (is_active sobre
datasource.pattern_detected, o una funcion de geometria de dibujo) se
anade como identificador canonico en ingles + display-name traducido
por catalogo; el renderizado por idioma sale de la traduccion, sin tocar
la gramatica. PASA aditivo.
Frontera: tecnica; que idiomas se lanzan y su calendario es producto de
Alvaro (ES/EN/FR v5.0, AR v5.1, ZH v5.2); la calidad de las traducciones
es trabajo de localizacion.
Trade-offs: el usuario experto que quiera DSL lo vera en ingles
(mitigado: superficie principal es el chatbot en su idioma +
explicaciones renderizadas; el DSL es opcional); el renderizado
localizado y los catalogos i18n son trabajo continuo (ya obligatorio por
REST-13); la opcion B queda como via declarada no construida.
Cruces: DAP-13/ADR-015 (canon: funciones neutrales, DSL derivado, forma
canonica que el chatbot produce), DAP-15 (el pipeline compila el mismo
canon, agnostico al idioma), DAP-7/ADR-008 (display-names por catalogo),
DAP-8 (chatbot como Componente), DAP-11/ADR-012 (limites del chatbot por
plan; coste LLM), DAP-12/DAP-9 (UI de creacion/explicacion, i18n/RTL).
INFORME 6 sec.14-15, REST-13, decision A3.
ADR-016. Estado: Aceptado (2026-07-06).
DAP-15 - Pipeline de compilacion de Rules   [CERRADA]

Tercera del Bloque F, lo cierra. DAP-13 fijo el canon (ADR-015),
DAP-14 la localizacion (ADR-016); DAP-15 lleva el canon a runtime.

--- ADR-017: Pipeline de compilacion de Rules ---
Enunciado: como se transforma una Rule canonica en algo ejecutable
(interpretar el AST o compilar a Execution Plan), como se comparte
computo a escala, y como se identifica/invalida el plan.
Opciones: A forma canonica -> AST -> runtime interpretado (util como
implementacion interna minima, NO como arquitectura final: repite v4);
B forma canonica -> AST -> Execution Plan -> runtime.
Decision de Alvaro: ACEPTADA opcion B, con implementacion minima en v5.0
(2026-07-06).
- Execution Plan como CACHE DERIVADA: se compila desde la forma canonica
  (fuente de verdad, ADR-015) y desde los catalogos/manifests
  versionados; nunca es fuente de verdad y siempre se reconstruye.
  Analogo al ref-count de ADR-014 y al capability set de ADR-012.
- Indexado por trigger: los planes se agrupan en LOTES por clave de
  trigger (candle_close por evaluation_context, event_arrival por
  DataSource, schedule, manual, mixed); al llegar un trigger solo se
  evaluan las reglas de su lote. Materializa el trigger_policy de
  ADR-015.
- Shared evaluation por declaracion: la evaluacion compartida de
  subexpresiones la dirige la declaracion de cada DataSource
  (shared_evaluation/sharing_scope/cache_key_schema, ADR-008), sobre
  formas canonicas IDENTICAS (hash de ADR-015). Subexpresiones
  equivalentes solo por transformaciones no soportadas por el
  normalizador no se detectan como comunes (coste marginal, no afecta
  correccion). El motor no conoce la semantica de la clave: la aplica.
- Ordenacion por coste: dentro de un lote, evaluar primero lo barato/
  mas selectivo (veto, descartes rapidos) para cortar pronto.
- Identidad e invalidacion del plan: el Execution Plan se identifica por
  un PlanFingerprint/PlanCacheKey derivado de TODOS sus inputs
  contractuales: rule_id, canonical_rule_hash, rule_schema_version,
  compiler_version, function_catalog_version, datasource_manifest_
  versions, datasource_capability_schema_versions, cache_key_schema
  versions, trigger_index_version y plan_policy_version. Cualquier
  cambio en uno de esos inputs invalida el plan y fuerza recompilacion.
  Si no puede recomputarse, la Rule queda DISABLED/FAILED/QUARANTINED
  observable (ADR-010); nunca se ejecuta con un plan obsoleto en
  silencio. El PlanFingerprint se persiste junto al ExecutionPlan y
  aparece en metricas/logs de compilacion (para depurar recompilaciones
  y quarantines).
- Implementacion minima v5.0: el plan puede ser trivial al principio
  (AST anotado + indexado por trigger; solo candle_close activo), con
  shared_evaluation y ordenacion por coste como optimizacion progresiva.
  Se decide B DESDE EL DISENO para no refactorizar al escalar, pero sin
  construir el compilador completo el dia uno; el PlanFingerprint si se
  disena bien desde el principio.
- El plan no relaja el budget: DAP-15 compila solo Rules que ya pasaron
  el complexity budget de ADR-015; el compilador no acepta canon
  inadmisible.
Criterios: C1 consume forma canonica y hash de ADR-015, materializa
trigger_policy en lotes, usa shared_evaluation/cache_key_schema de
ADR-008; el plan es cache derivada con fingerprint que incluye las
versiones de las declaraciones; el fallo de recompilacion cae al
lifecycle de ADR-010. C2 indexar por trigger + compartir subexpresiones
evita el AST interpretado en cada evaluacion (deuda de v4), de decenas a
miles de reglas, y es SEGURO porque el plan sabe cuando esta obsoleto.
C3 B desde el diseno con implementacion minima; el ajuste no exige
compilador complejo el dia uno, solo un PlanFingerprint bien disenado.
C4 el compilador opera sobre canon + declaraciones de DataSource, no
sobre trading; cualquier DataSource nueva entra por su declaracion.
Test v5.1+ (marginal): una DataSource nueva (dibujo, patron) declara su
shared_evaluation/sharing_scope/cache_key_schema/trigger/unidades de
historia; el compilador la indexa, comparte computo por su cache_key e
incluye sus versiones en el PlanFingerprint (una actualizacion invalida
los planes que la usan). Sin caso especial. PASA aditivo.
Frontera: ninguna; el dimensionado de lotes y el coste operativo del
compilador se concretan en construccion/operacion (INFORME 7).
Trade-offs: el Execution Plan es pieza a construir (mitigado:
implementacion minima en v5.0); la deteccion de subexpresiones comunes
se limita a formas canonicas identicas (coste marginal); el
PlanFingerprint debe cubrir todos los inputs contractuales y mantenerse
al anadir nuevos (barato si se disena desde el principio).
Cruces: DAP-13/ADR-015 (forma canonica, hash, trigger_policy, budget ya
validado; DAP-15 solo compila), DAP-14/ADR-016 (pipeline agnostico al
idioma), DAP-7/ADR-008 (shared_evaluation/sharing_scope/cache_key_schema;
sus versiones en el fingerprint), DAP-6/ADR-010 (plan no recomputable ->
DISABLED/FAILED/QUARANTINED), DAP-17/ADR-013 (triggers por el sustrato;
lotes), DAP-5/ADR-014 (datos por MarketStreamKey; plan como cache
derivada), DAP-11/ADR-012 (limites por plan; plan_policy_version),
DAP-16 (TradingSignalRule compilada -> senal -> gate). INFORME 6 sec.13,
INFORME 7.
ADR-017. Estado: Aceptado (2026-07-06).

-------------------------------------------------------------------
BLOQUE G - Ejecucion
-------------------------------------------------------------------
DAP-16 - Arquitectura de ejecucion multi-broker   [CERRADA]

Bloque G. Consume signal.* (ADR-015) tras el gate (ADR-012),
connectors BYOC (ADR-010), streams privados (ADR-014), execution.*
con idempotencia (ADR-013). Guardian del criterio 4: ejecutar es una
capacidad gateada, no el eje.

--- ADR-018: Arquitectura de ejecucion multi-broker ---
Enunciado: order manager, risk manager, execution gate, connector,
idempotencia realista, ciclo de vida de ordenes, reconciliacion,
confirmacion manual, ruta manual y automatica unica, familia
execution.*.
Opciones: A capa de ejecucion unica con ExecutionRequest neutral; B
dos pipelines separados (rechazada: duplica gate/risk/order manager);
C ejecucion acoplada al motor de reglas (rechazada: viola ADR-015).
Decision de Alvaro: ACEPTADA opcion A (2026-07-06).
- ExecutionRequest neutral (request_id, source_type signal|manual_ui|
  future_workflow, source_ref -si signal, event_id de la senal como
  causation-, tenant_id/user_id, connector_id, market_scope, intent
  {side, order_type, quantity|sizing_ref, price}, execution_profile_ref,
  requested_at). source_type se conserva en toda la cadena y en
  execution.*. El order manager NO depende de signal.* ni de la UI.
- Dos vetos ordenados y distintos: (i) EXECUTION GATE (policy, ADR-012)
  sobre la ExecutionRequest antes de construir la orden; capacidades
  connect_broker/execute_order/activate_autotrade; FAIL-CLOSED (default
  denegar); manual y automatica pasan por execute_order;
  activate_autotrade gatea el consumo de signal.*; si cierra ->
  SensitiveActionAudit, no se ejecuta. (ii) RISK MANAGER (seguridad,
  independiente): RiskDecision allow|block|reduce_size|require_manual_
  confirmation, entradas declaradas sin estado implicito, emite
  execution.risk_*.
- Semantica de ejecucion e idempotencia: CE v5 NO promete exactly-once
  externo frente al broker. Contrato: at-least-once en transporte +
  efectos internos idempotentes + client_order_id determinista cuando el
  exchange lo soporte + reconciliacion obligatoria. Si el exchange no
  soporta client_order_id fiable para una capacidad, el connector lo
  DECLARA (ADR-008) y la capacidad puede bloquearse FAIL-CLOSED. Ante
  timeout/estado ambiguo -> UNKNOWN/RECONCILING y se consulta al exchange
  antes de reintentar; nunca retry a ciegas de estado desconocido.
- Maquina minima de estados de orden (normalizada, comun): REQUESTED,
  GATE_BLOCKED, RISK_BLOCKED, CONFIRMATION_REQUIRED, CONFIRMATION_
  EXPIRED, READY_TO_SUBMIT, SUBMITTING, SUBMITTED, ACKNOWLEDGED,
  PARTIALLY_FILLED, FILLED, CANCEL_REQUESTED, CANCELED, REJECTED,
  EXPIRED, UNKNOWN, RECONCILING, FAILED_TERMINAL. RECONCILING reconstruye
  verdad desde fetch_order_status/stream_order_updates/fills/balance/
  positions. Transiciones append-only en ExecutionHistory; ningun estado
  externo ambiguo pasa a FILLED/CANCELED/REJECTED sin evidencia del
  exchange o reconciliacion verificable.
- Flujo de confirmacion manual: si RiskDecision=require_manual_
  confirmation, el order manager NO envia; persiste PendingExecution
  Confirmation (request_id, tenant_id/user_id, final_intent, risk_reason,
  expires_at, market_snapshot_ref, policy_version, risk_policy_version) y
  emite execution.confirmation_required. La confirmacion del usuario es
  idempotente (request_id + confirmation_id); al confirmar se REEVALUAN
  gate + risk justo antes de enviar; una confirmacion caducada/stale o
  con kill switch/policy cambiada NO ejecuta; la confirmacion NO puede
  saltarse el gate.
- Connector como Componente (ADR-001/008/010): declara capacidades
  (exchange, tipos de orden, client_order_id fiable, WebSocket de fills,
  Clock); interfaz interna estable (place_order/cancel_order/fetch_order_
  status/fetch_balance/fetch_positions/stream_order_updates) que traduce
  a CCXT o SDK. CCXT base v5.0 (100+ exchanges, MIT), hibrido SDK donde
  se justifique; lista de exchanges de Alvaro. CEX-BYOC v5.0; DEX (firma
  on-chain, otra primitiva de auth) como extension futura registrada
  (enlaza con idea futura: wallets frias/MetaMask + DEX para v5.1/v5.2).
- BYOC sin custodia: el usuario aporta credenciales; CE v5 NO custodia
  fondos. Envelope encryption (INFORME 5): en eventos api_key_ref, nunca
  la key. Verificacion de permisos AL CONECTAR; minimo privilegio
  (detectar/advertir permiso de retirada). Estados de credencial (aporte
  a ADR-010): connected/degraded/permission_error/revoked.
- ExecutionProfile como config de ejecucion del usuario, versionada
  tenant-scoped (ADR-011), FUERA de la TradingSignalRule. Autotrade NO
  privilegiado: es un source_type mas.
- Familia execution.* (ADR-003/004; proyeccion con causation_id):
  payloads minimos; timestamps de ejecucion; Clock inyectado;
  idempotencia/outbox del sustrato (ADR-013); fills por streams privados
  (ADR-014).
- Nota de auditoria (CSA): SensitiveActionAudit registra tambien
  confirmation_required, confirmation_confirmed, confirmation_expired,
  risk reduce_size, cambios de ExecutionProfile y cambios de permisos del
  connector, no solo bloqueos del gate.
Criterios: C1 ExecutionRequest unifica auto y manual respetando ADR-012/
015/003/004/007/013; at-least-once + idempotencia interna +
reconciliacion es coherente con ADR-013 (no exactly-once magico); gate y
risk como vetos distintos. C2 idempotencia + reconciliacion + UNKNOWN/
RECONCILING + fail-closed dan resiliencia en produccion; fills por
streams privados; CCXT reduce coste de exchanges; ExecutionRequest admite
origenes futuros. C3 UNA maquinaria; CCXT base; BYOC no-custodial; gate
reutiliza ADR-012; ajustes = disciplina minima. C4 autotrade es un
source_type, no el eje; connector como Componente; la misma senal sirve
al overlay universal y a la ejecucion selectiva.
Test v5.1+ (marginal): la maquinaria admite un source_type nuevo (future_
workflow de rebalanceo, o accion derivada de un patron v5.1) sin tocarla:
ExecutionRequest con ese source_type pasa el mismo gate/risk/order
manager/estados. PASA aditivo.
Frontera: el mecanismo es tecnico; jurisdicciones/licencia, exigencia de
keys sin withdrawal, custodia, disclaimers, exchanges soportados y
reporte regulatorio son de Alvaro. El diseno provee el mecanismo, no las
politicas.
Trade-offs: CCXT introduce capa intermedia (mitigado con hibrido SDK);
sin exactly-once externo, estados ambiguos pasan por RECONCILING (es lo
correcto); el flujo de confirmacion + reevaluacion anade pasos
(necesario: no es bypass); exchanges y regulacion quedan a Alvaro.
Cruces: DAP-2/ADR-003-004, DAP-4/ADR-007, DAP-5/ADR-014, DAP-6/ADR-010,
DAP-7/ADR-008, DAP-8/ADR-001, DAP-10/ADR-011, DAP-11/ADR-012, DAP-13/
ADR-015, DAP-17/ADR-013. INFORME 9, INFORME 5, INFORME 8.
ADR-018. Estado: Aceptado (2026-07-06).

-------------------------------------------------------------------
BLOQUE H - Cliente y UI
-------------------------------------------------------------------
DAP-12 - Portabilidad de cliente (PWA-first, ruta a nativa) [CERRADA]

Primera del Bloque H. INFORME 10 (ultimo INFORME, 4 revisiones CSA)
amplio esta DAP con los contratos de cliente; aqui se cierran.

--- ADR-019: Cliente PWA-first migrable a nativa ---
Enunciado: arquitectura del cliente, device-ports, ruta de empaquetado
y que capas sobreviven por ruta, service worker/offline, contratos de
cliente.
Opciones: A cliente portable con capas desacopladas + device-ports +
Capacitor preferente + contratos de INFORME 10 + alcance de migracion
explicito por ruta; B PWA cerrada monolitica (descartada, no migrable).
Decision de Alvaro: ACEPTADA opcion A (2026-07-06).
- La PWA es una IMPLEMENTACION de cliente, no la arquitectura del
  cliente. Capas: ui-core (presentacion), app-core (logica de cliente,
  no de negocio), device-ports (puertos de capacidad), shared-contracts
  (tipos generados, ADR-006).
- Alcance de migracion por ruta: device-web es el adapter de v5.0. En la
  ruta CAPACITOR, device-capacitor sustituye/adapta los adapters de
  dispositivo manteniendo la UI web y conservando ui-core web como
  codigo. En rutas RN/Flutter/nativo puro, se conservan app-core,
  shared-contracts, contratos de backend, interfaces de device-ports y
  modelo auth/realtime/permisos, pero la presentacion/ui-core PUEDE
  reimplementarse. La arquitectura NO promete reutilizacion total de UI
  fuera de Capacitor; promete no reescribir dominio, contratos ni
  capacidades.
- device-ports: PushPort, StoragePort/SecureStoragePort,
  NotificationPort, AudioPort, FilePort, ClipboardPort,
  NetworkStatusPort, BiometricPort, DeepLinkPort, AuthSessionPort,
  AuthFlowPort/AuthRedirectPort, ApiClientPort/NativeHttpPort,
  RealtimeConnectionPort. APIs web experimentales solo con fallback.
- Ruta de empaquetado: Capacitor preferente (mantiene UI web, adapta
  device-ports, no reescribe UI); RN/Flutter/nativo puro como evolucion
  (reescriben presentacion, mismo backend/dominio/contratos);
  reversible; sin atar framework. Trade-off packaged assets vs remote
  URL. Requisito: spike Capacitor en dispositivo real en construccion.
- Service worker SIN logica de negocio; PWAUpdatePolicy (skipWaiting/
  prompt explicito, no reload en accion sensible, rollback, telemetria,
  sin tokens en logs); offline limitado y explicito por matriz de cache/
  offline por tipo de dato, con regla dura de NO operar (ordenes,
  cambios sensibles) desde cache.
- Contratos de cliente (INFORME 10): AuthSessionPort/SecureStoragePort
  (tokens en cookie HTTP-only/memoria en web, Keychain/Keystore en
  nativo; nunca localStorage/IndexedDB); AuthFlowPort/AuthRedirectPort
  (external user-agent para nativo -RFC 8252-, PKCE, callback por
  universal/app links, logout global con revocacion); ApiClientPort/
  NativeHttpPort (puente seguro WebView<->almacen; refresh token nunca
  al JS; access token corto en memoria); DeviceInstallation/
  PushSubscription (entidad backend para el Notification Router:
  web_push/apns/fcm + fallback); RealtimeConnectionPort +
  RealtimeCheckpoint (estado de cliente por stream_key;
  last_seen_event_id no es campo del envelope) + RealtimeAuthContract
  (token efimero no en query string, renovacion, re-suscripcion
  revalidada, invalidacion por cambio de plan/KYC/jurisdiccion/
  permisos; sin acciones sensibles si falla auth realtime);
  DeepLinkContract (universal/app links preferentes, custom scheme
  fallback, validacion backend antes de pantalla sensible);
  ServiceWorkerLifecycle/PWAUpdatePolicy; versionado de cliente
  (min_supported_client_version, forced update en nativo, feature flags
  por version).
- i18n/RTL/CJK desde el primer commit (REST-13): CSS logico, sin texto
  hardcodeado, coherente con ADR-016.
- Niveles de sonido (cierra el gancho N3 de INFORME 4): N1 in-app (PWA);
  N2 push del sistema (PWA, sin sonido personalizado garantizado); N3
  nativo (iOS Critical Alerts condicionado al entitlement de Apple;
  Android canal de alta importancia con bypass DND no universal). No es
  base contractual.
Criterios: C1 consume shared-contracts (ADR-006), no evalua permisos
(ADR-012), RealtimeClient respeta el envelope (ADR-003/013) sin inventar
campos, DeviceInstallation alimenta el Notification Router (INFORME 4),
i18n coherente con ADR-016, no operar desde cache en sensibles (ADR-018).
C2 cliente desacoplado del backend versionado escala por adapter o por
reimplementacion de presentacion, sin reescribir dominio/contratos/auth.
C3 PWA-first + Capacitor es proporcional para equipo de dos con movil
como requisito; device-ports contenido; reversible. C4 el cliente es
superficie portable de la plataforma, no app de trading acoplada; las
capacidades futuras entran por contratos/widgets/DataSources/device-ports.
Test v5.1+ (CORE cliente): la UI de dibujo/overlays de patrones/alertas
vive en ui-core y consume DataSources/eventos por los mismos contratos;
si exigiera una capacidad nueva (p.ej. haptics) entraria por un device-
port nuevo sin tocar app-core. El charting concreto es DAP-9. PASA
aditivo.
Frontera: arquitectura/empaquetado/capacidades/service worker/contratos
son tecnicos y se deciden aqui; la restriccion regulatoria de PWA en UE
(Apple/DMA), publicar en stores, el entitlement de Critical Alerts y los
terminos de tiendas son de Alvaro.
Trade-offs: device-ports es abstraccion extra (justificada por
migrabilidad); iOS PWA tiene limites reales (push solo instalada,
storage evictable, sin background fiable) mitigados por la via nativa;
fuera de Capacitor, la presentacion/ui-core puede requerir
reimplementacion (limite honesto: se conserva dominio/contratos/
capacidades); spike Capacitor y testing en dispositivo real en
construccion.
Cruces: DAP-9 (charting en ui-core; par), DAP-11/ADR-012 (consume
capabilities, no las evalua), DAP-2/ADR-003/DAP-17/ADR-013 (envelope/
checkpoint), DAP-5/ADR-014 (realtime), DAP-14/ADR-016 (i18n/RTL),
DAP-16/ADR-018 (UI de ejecucion, no operar desde cache). INFORME 10, 4,
5, 2; REST-6/OBJ-3, REST-13.
ADR-019. Estado: Aceptado (2026-07-06).

DAP-9 - Estrategia de charting responsive/PWA   [CERRADA]

Ultima DAP. Segunda del Bloque H. El chart es UI en ui-core (ADR-019);
la abstraccion ChartPort es UI adapter de presentacion, no device-port.

--- ADR-020: Estrategia de charting responsive/PWA ---
Enunciado: libreria(s) de chart (financiero principal + widgets) y
como se aisla el chart para no acoplar el cliente a una libreria.
Opciones: (A) financiero: KLineChart (elegida), Lightweight Charts
(descartada, motivo v4), Advanced Charts (descartada, licencia); (B)
widgets: ECharts (preferente), Chart.js/Recharts (simples), ApexCharts.
Decision de Alvaro: ACEPTADA la estrategia propuesta (2026-07-06).
- Abstraccion ChartPort como UI ADAPTER / presentation port dentro de
  ui-core (NO device-port; los device-ports de ADR-019 son capacidades
  de dispositivo): el cliente habla con un contrato propio de
  presentacion (render de velas, overlays con metadatos, series,
  interaccion tactil, resize, seleccion, hit-testing, eventos). Cambiar
  la libreria toca SOLO el adapter y piezas visuales de ui-core, nunca
  app-core/shared-contracts/contratos backend/reglas/DataSources/
  execution. El chart recibe "marcas con metadatos" por contrato
  (INFORME 3/6): no conoce dominio ni trading.
- Chart financiero principal: KLineChart (Apache-2.0), continuidad de la
  decision de v4 (sin friccion de licencia ni atribucion; movil
  explicito; zero-dep; representa indicadores como RSI y herramientas de
  dibujo). Lightweight Charts DESCARTADA (motivo de v4: no representaba
  indicadores como RSI, faltaban herramientas de dibujo, y su diseno
  empuja a pagar Advanced Charts). TradingView Advanced Charts DESCARTADA
  (licencia restrictiva, solo empresas en proyectos publicos; riesgo alto
  para producto de pago). Fijacion final condicionada a validacion en PWA
  movil real (fuera de Dash, frontend TS responsive) en construccion.
- Charts de widgets: ECharts (Apache-2.0, sin tope, movil) preferente;
  Chart.js/Recharts para widgets simples.
- Criterio de aceptacion duro: soporte ADITIVO de overlays/series/dibujos
  custom, marcas con metadatos por contrato, hit-testing/interaccion
  tactil y resize, sin que el chart conozca dominio (es el eje en que
  Lightweight fallaba en v4; KLineChart lo cumple).
- Canvas por perfil movil; pinch/pan/resize/orientacion como criterios de
  la validacion movil. Overlay de senales universal en toda jurisdiccion
  (el gate es de ejecucion, ADR-012/018, no de visualizacion). Anclaje
  temporal de overlays por event_time (ADR-007).
Criterios: C1 el chart vive en ui-core como UI adapter (no device-port),
recibe marcas por contrato (ADR-015/INFORME 3) sin conocer dominio,
consume datos por ADR-006/014; mantiene la decision de v4 con motivo
documentado. C2 canvas + streams por MarketStreamKey escalan a movil; el
UI adapter permite cambiar de libreria sin refactor. C3 KLineChart y
ECharts Apache-2.0 sin friccion ni tope ni atribucion; decision ya
validada en v4 reduce riesgo. C4 ChartPort como adapter de presentacion
protege la frontera de ADR-019; el chart financiero es un widget entre
varios, no el eje.
Test v5.1+ (CORE): dibujo manual (lineas, fibonacci, zonas) y overlays de
datasource.pattern_detected sobre velas, anclados por event_time, sin
cambiar el contrato ChartPort ni la arquitectura del cliente. KLineChart
admite indicadores/overlays/dibujo; ECharts series/brush para widgets. El
anclaje fino frente a candle_corrected/timeframe es diseno de esa
DataSource en v5.1. PASA aditivo (validacion movil como red).
Frontera: la interpretacion legal de licencias y la lista comercial son
de Alvaro/asesoria; el diseno provee los criterios.
Trade-offs: dos librerias (financiera + widgets) en vez de una
(justificado); KLineChart no re-confirmada 100% hasta el spike movil en
construccion (honestidad tecnica); ChartPort anade una capa de
abstraccion (justificada por criterio 4 y migrabilidad).
Cruces: DAP-12/ADR-019 (chart como UI en ui-core; ChartPort UI adapter,
no device-port), DAP-13/ADR-015 (overlay de signal.*; marcas por
contrato), DAP-5/ADR-014 (datos por MarketStreamKey), DAP-2/ADR-006
(shared-contracts; contrato de marcas), DAP-4/ADR-007 (anclaje temporal;
candle_corrected), DAP-11/ADR-012 (overlay universal; gate de ejecucion),
DAP-14/ADR-016 (i18n/RTL). INFORME 2 sec.4, INFORME 3, INFORME 6 sec.16;
LECCIONES_V4 (veto de Lightweight, motivo).
ADR-020. Estado: Aceptado (2026-07-06).

===================================================================
7. MAPA DE TRAZABILIDAD
===================================================================
DAP    | ADR      | INFORME origen        | Criterios | Estado
-------|----------|-----------------------|-----------|--------
DAP-8  | ADR-001  | 8 (2/5/6 transversal) | C1-C4     | CERRADA
DAP-1  | ADR-002  | 2 (sec.8) + 7         | C1-C4     | CERRADA
DAP-2  | ADR-003  | 8 (envelope)          | C1-C4     | CERRADA
DAP-2  | ADR-004  | 8 + 9 (execution.*)   | C1-C4     | CERRADA
DAP-2  | ADR-005  | 8 (versionado)        | C1-C4     | CERRADA
DAP-2  | ADR-006  | 8 + 2 (tecnologia)    | C1-C4     | CERRADA
DAP-4  | ADR-007  | 8 (sec.6-7 temporal)  | C1-C4     | CERRADA
DAP-7  | ADR-008  | 2 + 6 + 8 (manifest)  | C1-C4     | CERRADA
DAP-3  | ADR-009  | 2 + 6 (discovery)     | C1-C4     | CERRADA
DAP-6  | ADR-010  | 2 + 5 (lifecycle)     | C1-C4     | CERRADA
DAP-10 | ADR-011  | 5 (tenancy)           | C1-C4     | CERRADA
DAP-11 | ADR-012  | 5 + 7 + 9 (flags)     | C1-C4     | CERRADA
DAP-17 | ADR-013  | 7 + 2 (transporte)    | C1-C4     | CERRADA
DAP-5  | ADR-014  | 5 + 7 (streams)       | C1-C4     | CERRADA
DAP-13 | ADR-015  | 6 + 4 + 8 (motor)     | C1-C4     | CERRADA
DAP-14 | ADR-016  | 6 (localizacion)      | C1-C4     | CERRADA
DAP-15 | ADR-017  | 6 (compilacion)       | C1-C4     | CERRADA
DAP-16 | ADR-018  | 9 (ejecucion)         | C1-C4     | CERRADA
DAP-12 | ADR-019  | 10 + 2 (cliente)      | C1-C4     | CERRADA
DAP-9  | ADR-020  | 2 + 3 (charting)      | C1-C4     | CERRADA

(Se rellena una fila por DAP conforme se cierra.)

===================================================================
8. ARQUITECTURA RESULTANTE CONSOLIDADA
===================================================================
Vista integrada de las 17 DAPs cerradas (ADR-001 a ADR-020). No
reabre decisiones: las une en un plano unico de capas y flujo. Es el
mapa que el Central de construccion usa como norte.

-------------------------------------------------------------------
8.1 PRINCIPIO DE FORMA
-------------------------------------------------------------------
CE v5 es UNA plataforma de Componentes (ADR-001) desplegada como
monolito modular multiproceso sobre un EventBus externo (ADR-002),
con costuras de extraccion pero sin microservicios en v5.0. Todo lo
que hace el sistema -ingerir mercado, evaluar reglas, alertar,
ejecutar, notificar- es un Componente del mismo tipo raiz, que
declara sus capacidades por manifest (ADR-008), se descubre por
convencion de carpetas (ADR-009) y tiene un lifecycle observable
(ADR-010). El trading NO es el eje: es una capacidad gateada mas.

-------------------------------------------------------------------
8.2 CAPAS (de dentro hacia fuera)
-------------------------------------------------------------------
(1) ESPINA DORSAL - contrato y tiempo.
    Todo evento viaja en un envelope canonico unico (ADR-003) con
    identidad logica (idempotency_key, stream_key), alcance (scope
    public_market|tenant|user|system) y linaje (correlation_id,
    causation_id). Los tipos se nombran dominio.accion en familias
    cerradas market/datasource/rule/signal/alert/execution/
    notification/user/component/billing (ADR-004); signal.* y alert.*
    son proyecciones de rule.*. El tiempo es explicito: 3 timestamps
    en UTC epoch ms, Clock inyectado, watermark y maturity_state por
    familia (ADR-007). Contratos versionados Pydantic -> JSON Schema
    -> TS (ADR-005/006).

(2) SUSTRATO DE COMPONENTES - declaracion, discovery, vida.
    Manifest tipado con capabilities genericas (DataSources como
    especializacion) y policy_requirements (ADR-008); discovery por
    carpeta al arranque, sin hot-reload en v5.0 (ADR-009); lifecycle
    Definition vs Instance con estados REGISTERED..RUNNING<->PAUSED..
    UNLOADED + FAILED/QUARANTINED, health y readiness separados
    (ADR-010).

(3) PLATAFORMA MULTIUSUARIO - aislamiento y capacidades.
    Tenancy shared-schema + RLS, tenant 1:1 usuario hoy, fallo cerrado
    (ADR-011). Capacidades resueltas por un PolicyEvaluator central:
    jurisdiccion (IP+KYC), rol, plan, entitlements, overrides y kill
    switches -> ALLOW|DENY|NOT_APPLICABLE con reason_code y
    policy_version, DENY>ALLOW en lo sensible, fail-closed, enforcement
    en API no solo UI (ADR-012).

(4) TRANSPORTE OPERATIVO - el cable y los datos.
    EventBus con transporte externo y capacidades obligatorias
    (at-least-once, acks, retries, DLQ, backpressure, consumer groups,
    ordering por stream_key, particionado, equivalente local, metricas)
    tras una abstraccion propia; Redis Streams en v5.0; retencion/
    replay con historico canonico en DB; idempotencia real de
    consumidor; outbox/inbox DB-bus (ADR-013). Market data hibrido:
    publicos compartidos por MarketStreamKey sin tenant_id, privados
    por-usuario BYOC con RLS/geo-gate; demanda agregada por
    MarketInterestRegistry/SubscriptionIntent con ref-count
    reconstruible (ADR-014).

(5) MOTOR DE REGLAS - una maquinaria, dos productos.
    Raiz Rule neutral sin mercado (target_binding); AlertRule y
    TradingSignalRule como hojas con market_scope; veto guardian OR;
    forma canonica de alcance declarado; doble ciclo evaluation/
    attention; rule.* fuente de verdad y signal.*/alert.* derivados con
    causation_id; complexity budget con hard caps (ADR-015). Canon en
    ingles como identificadores internos, chatbot multiidioma y
    renderizado localizado RTL/CJK-ready (ADR-016). Compilacion a
    Execution Plan derivado reconstruible, lotes por trigger,
    shared_evaluation por declaracion, PlanFingerprint con invalidacion
    observable (ADR-017).

(6) EJECUCION - capacidad gateada, no eje.
    ExecutionRequest neutral (source_type signal|manual_ui|future_
    workflow) -> execution gate fail-closed -> risk manager
    (RiskDecision) -> order manager -> connector. Sin exactly-once
    externo: at-least-once + idempotencia interna + client_order_id +
    reconciliacion; maquina de estados con UNKNOWN/RECONCILING;
    confirmacion manual sin bypass; connectors como Componentes (CCXT
    base), BYOC no-custodial; execution.* con fills por streams
    privados (ADR-018).

(7) CLIENTE Y UI - superficie portable.
    Cliente PWA-first con capas ui-core/app-core/device-ports/shared-
    contracts; Capacitor preferente reversible; contratos de cliente
    (auth, realtime+checkpoint, push, deep links, service worker);
    offline explicito sin operar desde cache; i18n/RTL/CJK (ADR-019).
    Charting en dos categorias tras ChartPort (UI adapter de ui-core):
    KLineChart en el chart financiero, ECharts en widgets; overlays de
    senal universales en toda jurisdiccion (ADR-020).

-------------------------------------------------------------------
8.3 FLUJO DE PUNTA A PUNTA (ejemplo: senal -> alerta + ejecucion)
-------------------------------------------------------------------
1. Un ingestor de mercado (Componente) publica market.* por
   MarketStreamKey al EventBus; la demanda la mantiene viva un
   SubscriptionIntent de una regla activa (ADR-014), aunque el usuario
   no tenga la PWA abierta.
2. El motor evalua las Rules cuyo lote de trigger corresponde (ADR-017)
   sobre esos datos; una Rule pasa a FIRING y emite rule.firing
   (ADR-015), salvo veto activo.
3. De rule.firing se proyectan, con causation_id: alert.* (si hay
   AlertRule) y signal.* (si hay TradingSignalRule). El overlay de la
   senal se pinta en el chart de CUALQUIER jurisdiccion (ADR-020); el
   geo-blocking no corta la visualizacion.
4. alert.* entra en el Notification Router -> canales (PWA push,
   Telegram, email...) segun politica; el attention lifecycle gobierna
   ACK/mute (ADR-015).
5. Si el usuario tiene autotrade habilitado Y pasa el gate (jurisdiccion
   BYOC, plan, kill switch; ADR-012), signal.* origina una
   ExecutionRequest (source_type signal). Gate fail-closed -> risk
   manager -> order manager con idempotencia y client_order_id ->
   connector BYOC (ADR-018).
6. Los fills vuelven como execution.* por streams privados por-usuario
   (ADR-014); la maquina de estados reconcilia; SensitiveActionAudit
   registra la traza.
7. Todo evento de la cadena viajo con el mismo envelope (ADR-003),
   idempotente (ADR-013), en UTC con Clock inyectado (ADR-007), aislado
   por tenant/RLS (ADR-011).

-------------------------------------------------------------------
8.4 POR QUE ESTO NO ES v4
-------------------------------------------------------------------
El bus informal en proceso, la ausencia de contratos, el acoplamiento
a "engine/trading" y la deuda sin registro (R1-R4) quedan cerrados: bus
externo con idempotencia y DLQ; envelope y versionado formales; raiz
Componente y raiz Rule neutrales; y cada decision escrita como ADR con
su motivo. El sistema escala de decenas a miles de usuarios anadiendo
consumidores y particiones, no reescribiendo.

===================================================================
9. DIFERIDO A CONSTRUCCION O A v5.1+
===================================================================
Lo que NO se cierra en DOC_ARQ_V5 y por que. Dos grupos: pendientes de
construccion, e ideas de herencia para versiones futuras.

9.1 PENDIENTES DE CONSTRUCCION / VALIDACION
- Validaciones en dispositivo real (spike Capacitor, chart movil), como
  requisito antes de fijar librerias criticas de UI (ADR-019/020).
- Listas comerciales de exchanges soportados (politica de Alvaro).
- Detalle de la DataSource de dibujo y su anclaje temporal frente a
  candle_corrected y cambios de timeframe (v5.1; ADR-015).

9.2 IDEAS DE HERENCIA PARA VERSIONES FUTURAS (v5.1+)
Registradas para no perderlas; NO son decisiones cerradas. Su lado
tecnico es de Central-construccion; su lado legal/comercial es de
Alvaro con asesoria.
- Wallets frias (tipo MetaMask) y operacion en exchanges
  descentralizados (DEX), para v5.1/v5.2. Habilitado por la nota "DEX
  como extension futura" de ADR-018 (la wallet on-chain es otra
  primitiva de auth distinta de las API keys BYOC de CEX). La custodia
  y el encuadre regulatorio son de Alvaro.
- Libreria de charting propia, para v5.2+, habilitada por el ChartPort
  como UI adapter de ADR-020: entraria como un adapter nuevo sin tocar
  app-core/contratos. Requiere un proyecto dedicado (motor de render,
  interaccion, dibujo con anclaje temporal, indicadores); evaluar solo
  si el rendimiento o una necesidad especifica lo justifican.
- Herramientas de dibujo avanzadas (v5.1): construibles sobre la API de
  overlays personalizados de KLineChart (registerOverlay: multi-paso,
  magnetismo, eventos, anclaje por timestamp+value) detras del
  ChartPort, con la logica en la DataSource de dibujo (ADR-015).
- Fork de KLineChart (Apache-2.0) como via limpia para extender el
  motor de chart por dentro si una herramienta excede su API publica
  (alternativa a copiar codigo de terceros; la interpretacion de
  licencias es de Alvaro/asesoria).
- Rol de administracion/compliance auditado para supervision e
  investigacion regulatoria: a disenar como capacidad EXPLICITA dentro
  del PolicyEvaluator (ADR-012), con traza obligatoria en
  SensitiveActionAudit (ADR-018); NUNCA como acceso oculto/backdoor ni
  como excepcion que salte el gate. El alcance (lectura vs
  intervencion), el acceso a datos y el respeto a la no-custodia de
  credenciales BYOC (ADR-018) son decision de Alvaro con asesoria legal.

(Se amplia conforme surjan nuevas ideas o pendientes.)

===================================================================
10. REFERENCIAS
===================================================================
INFORME 0 (OBJ/REST/CE), INFORMES 1-10, LECCIONES_V4,
DIFICULTADES_Y_REFACTORS_V4, DAPS.md, ADRS_PROPUESTOS.md.

FIN DOC_ARQ_V5 (nucleo cerrado: 17 DAPs, 20 ADR; 2026-07-06).
