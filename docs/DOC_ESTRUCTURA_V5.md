===================================================================
DOC_ESTRUCTURA_V5.md
===================================================================
Estructura de proyecto de Crypto Engine V5.

Naturaleza: PRESCRIPTIVO / DE IMPLEMENTACION. Derivado de los 20 ADR de
DOC_ARQ_V5 y de convenciones verificadas (web_search 2026). Fija la
estructura de repositorio, carpetas, fronteras y guardarrailes ANTES de
la primera linea de codigo. SUBORDINADO a DOC_ARQ_V5: no reabre DAPs ni
ADRs.
Autoridad de decision: Alvaro (decisor unico). CSA consultivo.
Estado: APROBADO (CSA consultivo) y FIRMADO por Alvaro (2026-07-06).
Fecha: 2026-07-06.

===================================================================
0. METADATOS
===================================================================
- Version: 1.0.
- Documento hermano de DOC_ROADMAP_V5 y DOC_ENTREGABLES_V5.
- Deriva de: DOC_ARQ_V5 (ADR-001 a ADR-020), INFORME 0 (OBJ/REST/CE),
  LECCIONES_V4 (R1-R4), convenciones 2026 verificadas.
- Autoridad: Alvaro; CSA consultivo.

===================================================================
1. PROPOSITO
===================================================================
Fijar la estructura del proyecto antes de construir, de modo que la
disciplina que evita la deuda de v4 este MATERIALIZADA en el arbol y en
guardarrailes automaticos, no confiada a la memoria del equipo. R3/R4
de v4 (codigo muerto, modulos huerfanos) fueron, en el fondo, problemas
de estructura sin enforcement. Este documento deriva de los ADR (no
inventa arquitectura) y hace ejecutables sus fronteras. Todo lo que
aqui se decide es organizacion/implementacion subordinada a DOC_ARQ_V5.

===================================================================
2. DECISIONES DE ESTRUCTURA
===================================================================

2.0 FRONTERA DE LAS DECISIONES DE ESTRUCTURA
Estas mini-decisiones son de implementacion/organizacion, SUBORDINADAS
a DOC_ARQ_V5. No reabren DAPs ni ADRs cerrados. Si una opcion contradice
o amplia una decision arquitectonica, se DETIENE la redaccion y se eleva
a Alvaro como decision arquitectonica explicita antes de construir
(evita deriva R2).

2.1 MONOREPO vs MULTI-REPO
Opciones: (A) Monorepo unico. (B) Multi-repo.
PROPUESTA: A, monorepo. CE v5 comparte tipos back<->front por la cadena
Pydantic -> JSON Schema -> TS (ADR-006); el monorepo permite que contrato
y consumidores se validen en el MISMO PR/commit y habilita el patron de
imports jerarquico no circular que rompe el acoplamiento de v4 (ADR-002).
Multi-repo obligaria a publicar los contratos como paquete externo antes
de consumirlos: sobrecarga para equipo de dos en v5.0. Trade-off
aceptado: el repo crece; se mitiga con fronteras de imports (sec.6) y
guardarrailes (sec.7).

2.2 HERRAMIENTA DE GESTION DEL MONOREPO
Opciones: (A) Workspaces nativos (pnpm/npm) + scripts + runner ligero,
sin framework. (B) Nx/Turborepo. (C) Bazel/Pants.
PROPUESTA: A para v5.0 (C3, simplicidad/coste para equipo de dos). Nx/
Bazel aportan estructura pero tambien complejidad y sobrecarga; resuelven
escala de EQUIPO que CE v5 no tiene aun. La estructura NO se ata a la
herramienta: adoptar Turborepo despues es aditivo (se anade config, no se
reorganizan carpetas). Reversible; no toca arquitectura.

2.3 LAYOUT DEL BACKEND (Python)
PROPUESTA: src-layout + pyproject.toml como config unica. NO es una
decision cerrada por REST ni por ADR; es una mini-decision de estructura
subordinada a DOC_ARQ_V5. Se adopta porque es coherente con REST-7
(backend Python), REST-11 (tests desde dia uno), REST-15 (migrabilidad
sin refactor) y ADR-002 (monolito modular con fronteras claras). El
src-layout fuerza que los tests corran contra el paquete INSTALADO, no
contra el working dir (evita errores de import y "funciona en mi maquina"
de v4). pyproject.toml (PEP 621) centraliza build, dependencias y config
de herramientas (Ruff/mypy/pytest) en un unico punto, frente a la
dispersion setup.py/cfg/requirements/MANIFEST de v4.

2.4 LAYOUT DEL FRONTEND (mapa de ADR-019)
No es decision abierta: ADR-019 fijo ui-core/app-core/device-ports/
shared-contracts. Aqui solo se mapean a paquetes con frontera propia (no
carpetas sueltas): ui-core (presentacion; incluye el ChartPort de ADR-020
como adapter de presentacion, NO device-port), app-core (logica de
cliente), device-ports (interfaces) + device-web (adapter web v5.0),
shared-contracts (tipos generados, consumidos, nunca editados). Fronteras
ejecutables en sec.6 y 7.

2.5 UBICACION Y FLUJO DE shared-contracts
No reabre ADR-005/006; decide DONDE viven las tres zonas y su direccion.
PROPUESTA, direccion unica:
  contracts/source/        fuente Pydantic v2 (UNICA fuente de verdad)
  contracts/schemas/       JSON Schema generado (artefacto)
  frontend/.../generated/  tipos TS generados (artefacto)
Regla dura: los artefactos NO se editan a mano; se regeneran desde
source. La herramienta concreta (datamodel-code-generator o
pydantic-to-typescript) se elige en construccion; la estructura reserva
las tres zonas y el flujo. ADR-006 fija la cadena como frontera unica y
fuente unica de verdad.

===================================================================
3. ARBOL DE CARPETAS
===================================================================
Arbol raiz del monorepo. Cada entrada lleva su ancla (ADR/CE/REST).
Nombres ASCII, sin acentos. El detalle interno de cada componente se
genera con la plantilla de la seccion 5.

ce_v5/
- pyproject.toml            Config raiz backend (PEP 621)        [2.3]
- package.json              Workspaces del lado TS               [2.2]
- README.md
- .gitignore
- .env.example              Plantilla de entorno; nunca secretos [CE-13]
- docs/                     Documentacion viva del proyecto      [R1]
  - adr/                    Snapshot sincronizado de ADRS_PROPUESTOS
  - DOC_ARQ_V5.md
  - DOC_ESTRUCTURA_V5.md
  - DOC_ROADMAP_V5.md
  - DOC_ENTREGABLES_V5.md
- contracts/                Espina dorsal de contratos      [ADR-005/006]
  - source/                 Pydantic v2 = FUENTE DE VERDAD   [ADR-006]
    - envelope/             Envelope canonico               [ADR-003]
    - families/             Familias dominio.accion         [ADR-004]
      (market datasource rule signal alert execution notification
       user component billing)
    - time/                 Modelo temporal (3 ts, Clock)   [ADR-007]
  - schemas/                JSON Schema GENERADO (no editar) [ADR-006]
  - VERSIONING.md           Reglas de evolucion dual        [ADR-005]
- backend/
  - src/                    src-layout                          [2.3]
    - ce_v5/
      - core/               Nucleo neutral de plataforma
        - component/        Raiz Componente, lifecycle  [ADR-001/010]
        - manifest/         Modelo y validacion manifest [ADR-008]
        - discovery/        Discovery por carpeta        [ADR-009]
        - bus/              Abstraccion EventBus         [ADR-013]
        - clock/            Clock inyectable             [ADR-007]
        - policy/           PolicyEvaluator central      [ADR-012]
        - tenancy/          Contexto tenant/RLS          [ADR-011]
      - components/         TODOS los Componentes reales [ADR-001/009]
        - <nombre>/         (plantilla en seccion 5)
      - platform/           Servicios transversales
        - rules/            Motor de reglas              [ADR-015/017]
        - execution/        Cadena de ejecucion          [ADR-018]
        - market/           Streams de market data       [ADR-014]
        - notification/     Router de notificaciones     [INFORME 4]
        - billing/          Integracion billing          [CE-10]
      - entrypoints/        Procesos runtime (seccion 4) [ADR-002]
        - api/
        - worker_ingestion/   worker_rules/
        - worker_notifications/  worker_execution/
        - worker_reconciliation/
      - infra/              Adapters de infraestructura
        - db/               Persistencia, RLS            [ADR-011]
        - bus_redis/        Adapter Redis Streams        [ADR-013]
        - connectors/       Adapters de exchange (CCXT)  [ADR-018]
- frontend/
  - package.json
  - src/
    - shared-contracts/     Tipos consumidos             [ADR-006]
      - generated/          TS GENERADO (no editar)      [ADR-006]
    - app-core/             Logica de cliente            [ADR-019]
    - ui-core/              Presentacion (incl. ChartPort) [ADR-019/020]
    - device-ports/         Interfaces de puerto         [ADR-019]
    - device-web/           Adapter web v5.0             [ADR-019]
- tests/                    Tests transversales (ver nota)
  - unit/  integration/  e2e/
- tools/                    Scripts de generacion y checks (sec.7)
  - gen_schemas.py  gen_ts_types  check_imports
  - check_manifests  check_orphans
- infra/                    Docker, compose, CI          [INFORME 7]
  - docker/  compose/  ci/
- .github/workflows/        Guardarrailes CI (seccion 7)

Nota workers: el set de workers de entrypoints/ es CONCEPTUAL;
DOC_ROADMAP_V5 decide cuales existen desde el inicio y si alguno comparte
proceso fisico (ver 4).
Nota tests: los tests de un Componente viven junto a el en
components/<nombre>/tests/; en tests/ (raiz) viven solo los tests
TRANSVERSALES (integration/e2e y unit de core/platform). No se duplican.

===================================================================
4. PROCESOS Y ENTRYPOINTS RUNTIME
===================================================================
CE v5 es monolito modular MULTIPROCESO (ADR-002): un unico codebase
desplegable, con shared-contracts como frontera unica, pero NO un unico
proceso. Regla dura: NO existe un main.py gigante que arranque todo;
cada proceso tiene su entrypoint en entrypoints/<nombre>/.

Entrypoints CONCEPTUALES (ADR-002, INFORME 7):
  api                    Peticiones HTTP/WS; no evalua reglas.
  worker_ingestion       Ingesta de market data          [ADR-014]
  worker_rules           Evaluacion del motor de reglas  [ADR-015/017]
  worker_notifications   Router de notificaciones        [INFORME 4]
  worker_execution       Cadena de ejecucion             [ADR-018]
  worker_reconciliation  Reconciliacion de ordenes       [ADR-018]

Worker CONCEPTUAL vs proceso FISICO: DOC_ROADMAP_V5 puede agrupar
temporalmente varios workers conceptuales en un mismo proceso fisico si
no rompe ADR-002 ni las fronteras de eventos (evita explosion inicial de
procesos para equipo de dos). Cada worker conceptual conserva su modulo
y su entrypoint aunque comparta proceso; separarlos despues es aditivo,
no refactor.

Todos los procesos se comunican por el EventBus externo (ADR-013), nunca
por imports cruzados ni memoria compartida. La API no evalua reglas ni
ejecuta ordenes: publica/consume eventos.

===================================================================
5. CONVENCION DE UN COMPONENTE (ALTA Y BAJA)
===================================================================
5.1 ALTA (materializa CE-14 y ADR-008/009: "copiar carpeta + reiniciar")
Un Componente vive integro en components/<nombre>/:

  components/<nombre>/
  - manifest.(json|yaml)  Declaracion tipada, versionada  [ADR-008]
  - __init__.py           Entrypoint declarado en manifest [ADR-009]
  - <logica>.py           Implementacion del Componente
  - tests/                Tests del Componente
  - README.md             Proposito declarado (anti-R1)

Los contratos publicos que el Componente PRODUCE o CONSUME viven en
contracts/source/ y se referencian desde el manifest mediante schema_ref
(ADR-008). El Componente NO mantiene schemas publicos propios: la fuente
unica de verdad de contratos es contracts/source/ (2.5, ADR-006). Si un
Componente necesitara validaciones internas NO publicas, irian en un
internal_schemas/ local claramente marcado como no-compartido; no se
incluye de inicio para evitar confusion.

Discovery (ADR-009): al arranque se escanea components/, se lee y valida
el manifest (ADR-008), se registra el Componente y solo despues se carga
el entrypoint declarado. Nunca se importa codigo para saber que existe.
Sin hot-reload en v5.0 (ADR-009): el descubrimiento es al arranque.

5.2 BAJA (plantilla de ELIMINACION; ataca R3/R4)
Eliminar un Componente exige retirar EN EL MISMO CAMBIO: carpeta,
manifest, tests, schemas propios, referencias en docs y snapshots/
artefactos generados. Queda PROHIBIDO dejar placeholders, codigo
comentado o carpetas "disabled por si acaso".
Regla de escalado: eliminar un Componente ordinario NO requiere ADR si
no altera contratos publicos. Si la eliminacion rompe o depreca un
contrato, una familia de evento, una capability transversal o una
frontera de capa, se tramita como cambio ARQUITECTONICO explicito
(elevado a Alvaro), no como limpieza (protege ADR-005/006 y evita R2).

===================================================================
6. FRONTERAS ENTRE CAPAS (reglas de import)
===================================================================
Materializa ADR-002 (modulos por contratos/eventos, sin imports cruzados
directos) y ADR-019 (capas de cliente). El patron es JERARQUICO: las
dependencias apuntan hacia los contratos y el nucleo, nunca al reves ni
en cruz. Los Componentes NO se importan entre si: se comunican por
eventos.

MATRIZ (permitido / prohibido):
  backend/components/*  ->  PUEDE importar shared-contracts y core ports/
                            interfaces. NO importa adapters concretos de
                            infra (bus_redis, DB concreta, CCXT...) salvo
                            que el propio Componente SEA, por definicion,
                            un adapter/connector y lo declare en su
                            manifest.
  backend/components/*  ->  NO importa otro components/*  (usan eventos).
  backend/components/*  ->  NO importa frontend.
  backend/platform/*    ->  PUEDE importar core y contracts; NO importa
                            components concretos por nombre.
  backend/core/*        ->  NO importa components ni platform (raiz
                            neutral; nadie por encima).
  frontend/app-core     ->  PUEDE importar shared-contracts y device-ports
                            (interfaces); NO importa device-web.
  frontend/ui-core      ->  NO llama a la API directamente (eso es de
                            app-core); consume via app-core.
  frontend/device-web   ->  IMPLEMENTA device-ports; NO define casos de
                            uso ni logica de negocio.
  cualquier capa        ->  NO edita contracts/schemas ni */generated.

REGLA DE CABLEADO (composition root): los adapters concretos de
infraestructura (bus_redis, DB, connectors CCXT) se cablean en
entrypoints/ (composition root). Los Componentes dependen de puertos/
contratos, no de implementaciones concretas, salvo Componentes-adapter
declarados como tales en su manifest (ADR-013).

Direccion del contrato (2.5): contracts/source -> contracts/schemas ->
frontend/generated. Unidireccional; nunca a la inversa.

===================================================================
7. GUARDARRAILES AUTOMATICOS Y CHECKS DE ESTRUCTURA
===================================================================
Las reglas anteriores NO son consejos: se hacen cumplir en CI. Sin
enforcement automatico, las convenciones se erosionan y vuelve la deuda
de v4. (Coherente con ADR-005 -checks de compatibilidad de schema en CI-,
ADR-006 -validacion en bordes/CI-, ADR-008/009 -validacion de manifests
y discovery observable-, ADR-011 -tests de aislamiento RLS-.)

7.0 POLITICA DE MADUREZ DE CHECKS
Los checks son obligatorios como CATEGORIA desde el primer commit, pero
crecen por fases para no exigir un arsenal de CI antes de tener codigo:
- BLOQUEANTES desde Pieza 0: imports/fronteras (7.1/7.2), artefactos
  generados no editados (7.4), lint/format/type-check.
- Se ACTIVAN cuando exista su objeto: 7.3 al primer contrato; 7.5/7.6 al
  primer Componente real; 7.7 al primer schema versionado; 7.8 a la
  primera tabla tenant/user; 7.9 desde el primer Componente.

7.1 Checks de imports entre capas (backend) -> falla si se viola la
    matriz de la seccion 6 (ADR-002).
7.2 Checks de boundaries (frontend) -> ui-core no llama API; device-web
    no define casos de uso (ADR-019).
7.3 Checks de shared-contracts -> regenerar desde source y comparar; si
    schemas/ o generated/ no coinciden con source, FALLA el build
    (ADR-006).
7.4 Prohibicion de editar artefactos generados -> check que detecta
    ediciones manuales en contracts/schemas y */generated (ADR-006).
7.5 Validacion de manifests -> cada manifest es tipado, versionado y
    valido; si no, el build falla (ADR-008).
7.6 Check de huerfanos -> carpeta en components/ sin manifest, o manifest
    sin entrypoint, o entrypoint inexistente -> falla (ADR-009; R3/R4).
7.7 Checks de schema y compatibilidad -> cambios de contrato respetan las
    reglas de evolucion; incompatibles sin bump -> falla (ADR-005).
7.8 Checks de tenancy/RLS -> toda tabla declara alcance (public_market/
    tenant/user/system); tests de aislamiento; sin RLS donde toca ->
    falla (ADR-011).
7.9 Documentacion minima -> cada carpeta de Componente tiene README con
    proposito declarado; su ausencia -> falla (anti-R1).

Los detalles de "que significa pieza entregada" y la relacion checks<->
entrega viven en DOC_ENTREGABLES_V5; aqui se define QUE se comprueba, no
la politica de entrega.

===================================================================
8. TRAZABILIDAD (carpeta/regla/check -> ancla)
===================================================================
contracts/source, schemas ....... ADR-003/004/005/006
contracts/time .................. ADR-007
core/component, lifecycle ....... ADR-001, ADR-010
core/manifest, discovery ........ ADR-008, ADR-009
core/bus, infra/bus_redis ....... ADR-013
core/policy ..................... ADR-012
core/tenancy, infra/db (RLS) .... ADR-011
components/<nombre>/ ............. ADR-001/008/009, CE-14
platform/rules .................. ADR-015, ADR-017
platform/execution, connectors .. ADR-018
platform/market ................. ADR-014
entrypoints/* (multiproceso) .... ADR-002, INFORME 7
frontend capas .................. ADR-019
ui-core/ChartPort ............... ADR-020
matriz de imports (sec.6) ....... ADR-002, ADR-019
guardarrailes (sec.7) ........... ADR-005/006/008/009/011, R1/R3/R4
plantilla de eliminacion ........ R3/R4 (y ADR-005/006 si toca contrato)

FIN DOC_ESTRUCTURA_V5 (v1.0, aprobado CSA + firmado Alvaro 2026-07-06).
