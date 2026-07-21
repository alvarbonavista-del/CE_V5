===================================================================
DOC_ROADMAP_V5.md
===================================================================
Plan de construccion de Crypto Engine V5.

Naturaleza: PRESCRIPTIVO / DE CONSTRUCCION. Traduce los 20 ADR de
DOC_ARQ_V5 y la estructura de DOC_ESTRUCTURA_V5 en piezas construibles,
su orden y su criterio de terminado. SUBORDINADO a ambos: no reabre ADRs
ni redefine carpetas. NO fija fechas ni presupuesto (eso es de Alvaro);
el plan es en esfuerzo relativo y dependencias.
Autoridad de decision: Alvaro (decisor unico). CSA consultivo.
Estado: APROBADO (CSA consultivo) y FIRMADO por Alvaro (2026-07-06).
Fecha: 2026-07-06.

===================================================================
0. METADATOS
===================================================================
- Version: 1.0.
- Documento hermano de DOC_ESTRUCTURA_V5 y DOC_ENTREGABLES_V5.
- Deriva de: DOC_ARQ_V5 (ADR-001 a ADR-020), DOC_ESTRUCTURA_V5,
  INFORME 0 (OBJ/CE/REST), INFORME 7 (despliegue).
- Autoridad: Alvaro; CSA consultivo.

0.1 PREMISAS DE ENTRADA
- DOC_ARQ_V5 cerrado y firmado (17 DAP, 20 ADR).
- DOC_ESTRUCTURA_V5 cerrado y firmado (monorepo, arbol, fronteras, CI).
- DOC_ENTREGABLES_V5 aun NO cerrado: este roadmap define piezas y orden;
  la politica fina de entregas/fixes/validaciones en caliente se cierra
  alli. Aqui se referencia, no se duplica.

===================================================================
1. PROPOSITO Y FRONTERAS
===================================================================
Define QUE se construye, en QUE orden, que DEPENDE de que, y COMO se sabe
que una pieza esta terminada. NO reabre ADRs (eso es DOC_ARQ_V5), NO
redefine carpetas (eso es DOC_ESTRUCTURA_V5), NO fija fechas ni coste
(eso es de Alvaro). El plan se expresa en esfuerzo relativo (S/M/L/XL) y
dependencias, no en calendario.

===================================================================
2. PRINCIPIOS DE SECUENCIACION
===================================================================
El orden lo mandan las dependencias de los ADR, no la visibilidad de las
features. Reglas duras:
- ESPINA DORSAL PRIMERO: contratos, envelope, tiempo/Clock y EventBus
  antes que cualquier logica de negocio (ADR-003/004/005/006/007/013).
- TRANSVERSAL ANTES QUE DEPENDIENTE: tenancy, policy/gate y lifecycle de
  componentes antes que las capacidades que los usan (ADR-010/011/012).
- GATE ANTES QUE CAPACIDAD GATEADA: el PolicyEvaluator y el execution
  gate existen antes de que haya ejecucion real (ADR-012 antes de 018).
- NADA DE UI ANTES DE SUS CONTRATOS: el cliente consume contratos ya
  definidos (ADR-006/019 despues de la espina dorsal).
- CI DESDE PIEZA 0: los guardarrailes de DOC_ESTRUCTURA sec.7 corren
  desde el primer commit; una pieza no esta hecha si su CI no pasa.
- FAIL-CLOSED de serie: lo sensible nace denegando (ADR-012/018).
ADVERTENCIA ANTI-DERIVA (leccion v4): no convertir el roadmap en una
lista de features visibles. El bus informal, el dashboard acoplado, el
Clock tardio y los engines cableados a mano fueron causas directas de la
deuda de v4. Si una pieza de UI o de negocio pide adelantarse a su
contrato, se detiene y se reordena.

===================================================================
3. INVENTARIO DE PIEZAS DE CONSTRUCCION
===================================================================
Cada pieza usa la tabla canonica. "hecho cuando" incluye siempre los
checks de CI que debe pasar (DOC_ESTRUCTURA sec.7). Esfuerzo relativo.

--- PIEZA P00: Esqueleto de repositorio + CI base ---
  id: P00
  objetivo: Monorepo vacio pero con estructura y guardarrailes vivos.
  ADR/REST/CE: DOC_ESTRUCTURA 2-7; ADR-002.
  carpeta destino: raiz, tools/, .github/workflows/
  dependencias: ninguna.
  desbloquea: todo.
  esfuerzo: S
  hecho cuando: arbol creado; pyproject + workspaces; CI verde con checks
    bloqueantes de Pieza 0 (imports/fronteras, generado-no-editable,
    lint/format/type-check).
  checks obligatorios: 7.1, 7.2, 7.4 (base), lint/format/type.
  validacion manual/caliente: ninguna.
  fuera de alcance: cualquier logica.

--- PIEZA P01: Contratos base y envelope ---
  id: P01
  objetivo: Envelope canonico y familias de evento como Pydantic fuente.
  ADR/REST/CE: ADR-003, ADR-004, ADR-005, ADR-006.
  carpeta destino: contracts/source/envelope, families; contracts/schemas
  dependencias: P00.
  desbloquea: P02, P02b, P03, y toda emision de eventos.
  esfuerzo: L
  hecho cuando: envelope con identidad fisica/logica, scope y linaje;
    familias dominio.accion; generacion source->JSON Schema->TS; check
    7.3/7.4 verde.
  checks obligatorios: 7.3, 7.4, 7.7.
  validacion manual/caliente: ninguna.
  fuera de alcance: logica que produce estos eventos.

--- PIEZA P02: Modelo temporal y Clock ---
  id: P02
  objetivo: 3 timestamps UTC, Clock inyectable, watermark/maturity.
  ADR/REST/CE: ADR-007.
  carpeta destino: contracts/source/time; backend core/clock
  dependencias: P01.
  desbloquea: todo lo que emite eventos con tiempo.
  esfuerzo: M
  hecho cuando: Clock inyectable en tests; event/ingestion/processing_time
    en el envelope; maturity_state por familia.
  checks obligatorios: 7.3, type-check.
  validacion manual/caliente: ninguna.
  fuera de alcance: watermark avanzado de replay (con P03).

--- PIEZA P02b: Persistencia base + migraciones + transactional outbox ---
  id: P02b
  objetivo: DB base SIN modelo tenant completo: conexion, migraciones,
    transacciones, tablas tecnicas para outbox/inbox y audit tecnico
    minimo. Separada de la tenancy (P05).
  ADR/REST/CE: ADR-013 (outbox/inbox), ADR-003 (identidad de evento).
  carpeta destino: backend/infra/db (base, sin RLS aun)
  dependencias: P00, P01, P02.
  desbloquea: P03 (el bus necesita persistencia transaccional para
    outbox/inbox).
  esfuerzo: M
  hecho cuando: conexion + migraciones; transacciones; tablas de outbox/
    inbox; audit tecnico minimo. SIN RLS ni modelo tenant (eso es P05).
  checks obligatorios: integration DB, type-check.
  validacion manual/caliente: una escritura transaccional con outbox.
  fuera de alcance: RLS, tenancy (P05).

--- PIEZA P03: Sustrato EventBus (abstraccion + adapter Redis) ---
  id: P03
  objetivo: Bus externo con at-least-once, DLQ, consumer groups,
    idempotencia real, outbox/inbox.
  ADR/REST/CE: ADR-013.
  carpeta destino: backend core/bus; infra/bus_redis
  dependencias: P01, P02, P02b.
  desbloquea: todo proceso que publica/consume; mata el _bus(ev) de v4.
  esfuerzo: XL
  hecho cuando: publish/consume idempotente; DLQ; equivalente local para
    tests; outbox/inbox transaccional sobre la DB de P02b; replay por
    offset.
  checks obligatorios: 7.1, integration del bus.
  validacion manual/caliente: reinicio de consumidor sin perder ni
    duplicar.
  fuera de alcance: particionado avanzado (con la escala).

--- PIEZA P04: Raiz Componente, manifest, discovery, lifecycle ---
  id: P04
  objetivo: Sustrato de Componentes: raiz neutral, manifest tipado,
    discovery por carpeta, lifecycle observable.
  ADR/REST/CE: ADR-001, ADR-008, ADR-009, ADR-010.
  carpeta destino: backend core/component, manifest, discovery
  dependencias: P01, P03.
  desbloquea: todo Componente real; "copiar carpeta + reiniciar".
  esfuerzo: L
  hecho cuando: discovery valida manifest antes de cargar codigo; estados
    de lifecycle; check 7.5/7.6 verde.
  checks obligatorios: 7.5, 7.6.
  validacion manual/caliente: alta de un Componente dummy por copia de
    carpeta + reinicio.
  fuera de alcance: hot-reload (no v5.0).

--- PIEZA P05: Tenancy shared-schema + RLS ---
  id: P05
  objetivo: tenancy shared-schema + RLS fail-closed SOBRE la persistencia
    de P02b.
  ADR/REST/CE: ADR-011.
  carpeta destino: backend core/tenancy; infra/db
  dependencias: P02b, P03.
  desbloquea: todo dato por-tenant; usuarios reales.
  esfuerzo: L
  hecho cuando: toda tabla declara alcance (public_market/tenant/user/
    system); RLS activo; tests de aislamiento; check 7.8 verde.
  checks obligatorios: 7.8.
  validacion manual/caliente: intento de fuga cross-tenant que debe fallar.
  fuera de alcance: sharding (no v5.0).

--- PIEZA P06: PolicyEvaluator central + kill switch (el gate) ---
  id: P06
  objetivo: Resolucion de capacidades por jurisdiccion/plan/rol,
    fail-closed, kill switch jerarquico, enforcement en API.
  ADR/REST/CE: ADR-012.
  carpeta destino: backend core/policy
  dependencias: P05.
  desbloquea: geo-blocking, premium, y el execution gate. EXISTE ANTES
    QUE LA EJECUCION.
  esfuerzo: L
  hecho cuando: ALLOW/DENY/NOT_APPLICABLE con reason_code+policy_version;
    DENY>ALLOW; fail-closed en sensibles; SensitiveActionAudit; kill
    switch propaga por evento.
  checks obligatorios: 7.8, audit.
  validacion manual/caliente: kill switch que corta una capability en
    caliente sin reinicio.
  fuera de alcance: catalogo comercial de jurisdicciones (de Alvaro).

--- PIEZA P06b: API/Auth/Realtime Gateway ---
  id: P06b
  objetivo: Entrypoint HTTP/WS que expone la plataforma por contratos,
    autentica usuarios, aplica el PolicyEvaluator en bordes API, sirve
    capabilities, registra dispositivos/push y publica/consume eventos
    SIN evaluar reglas ni ejecutar ordenes.
  ADR/REST/CE: ADR-002, ADR-006, ADR-011, ADR-012, ADR-013, ADR-019.
  carpeta destino: backend/entrypoints/api; backend/core/auth (si se crea)
  dependencias: P01, P03, P05, P06.
  desbloquea: P09b, P12a, P13, orden manual UI, realtime.
  esfuerzo: L
  hecho cuando: login/session/JWT o proveedor elegido; endpoints
    versionados basicos; WebSocket/realtime autenticado
    (RealtimeAuthContract); capabilities expuestas; geo/policy aplicado
    en API; API publica/consume eventos sin evaluar reglas ni ejecutar.
  checks obligatorios: 7.1, 7.3, 7.8, integration API/auth/realtime.
  validacion manual/caliente: login + suscripcion realtime autenticada.
  fuera de alcance: logica de reglas/ejecucion (nunca en la API).

--- PIEZA P07: Ingesta de market data (hibrida) ---
  id: P07
  objetivo: Streams publicos compartidos por MarketStreamKey y privados
    BYOC; demanda por SubscriptionIntent con ref-count.
  ADR/REST/CE: ADR-014.
  carpeta destino: backend platform/market; entrypoints/worker_ingestion
  dependencias: P03, P04, P05.
  desbloquea: datos para reglas y para la UI.
  esfuerzo: L
  hecho cuando: publicos sin tenant_id compartidos; privados con RLS/geo;
    ref-count reconstruible; primer market.* end-to-end con datasource
    FAKE o connector minimo elegido para construccion.
  checks obligatorios: 7.1, 7.8.
  validacion manual/caliente: alta/baja de interes que enciende/apaga un
    stream por ref-count.
  fuera de alcance: integracion completa de todos los exchanges (de Alvaro).

--- PIEZA P08: Motor de reglas (raiz Rule + evaluacion + proyeccion) ---
  id: P08
  objetivo: Raiz Rule neutral; AlertRule y TradingSignalRule; forma
    canonica; doble ciclo; proyeccion rule.* -> signal.*/alert.*.
  ADR/REST/CE: ADR-015, ADR-016, ADR-017.
  carpeta destino: backend/platform/rules; entrypoints/worker_rules
  dependencias: P03, P04, P05, P06, P06b, P07.
  desbloquea: senales y alertas; la "maquinaria unica, dos productos".
  esfuerzo: XL
  hecho cuando: una Rule tenant-scoped pasa a FIRING y proyecta
    signal.*/alert.* con causation_id; veto guardian; forma canonica
    con hash estable; Execution Plan derivado reconstruible; las
    capacidades necesarias se resuelven por PolicyEvaluator y los
    eventos quedan servibles por API/realtime.
  checks obligatorios: 7.1, 7.3, 7.8, integration de reglas.
  validacion manual/caliente: crear una regla como dato tenant-scoped
    y verla disparar sobre datos reales.
  fuera de alcance: dibujo/patrones (v5.1, seccion 8).

--- PIEZA P09a: Notification Router backend ---
  id: P09a
  objetivo: alert.* -> ruteo por politica, dedup/ACK; entrega por canal
    mock/Telegram/email/webhook segun disponibilidad. SIN push PWA.
  ADR/REST/CE: INFORME 4; ADR-004/012.
  carpeta destino: backend/platform/notification;
    entrypoints/worker_notifications
  dependencias: P06, P08, P06b.
  desbloquea: avisos backend; base de P09b.
  esfuerzo: M
  hecho cuando: consume alert.*, politica, dedup/ACK idempotente, entrega
    por al menos un canal no-PWA (o mock).
  checks obligatorios: 7.1.
  validacion manual/caliente: una alerta real llega a un canal no-PWA.
  fuera de alcance: push PWA (P09b).

--- PIEZA P09b: PWA Push integration ---
  id: P09b
  objetivo: DeviceInstallation/PushSubscription desde cliente; push en
    dispositivo real; ACK idempotente.
  ADR/REST/CE: ADR-019 (contratos de cliente); INFORME 4.
  carpeta destino: backend/platform/notification (registro); frontend
  dependencias: P09a, P12a, P06b.
  esfuerzo: M
  hecho cuando: registro de dispositivo desde cliente; push probado en
    dispositivo real; ACK idempotente.
  checks obligatorios: 7.1, 7.2, 7.8.
  validacion manual/caliente: push real a un movil.
  fuera de alcance: sonido N3 nativo (via nativa, ADR-019).

--- PIEZA P10a: Credential Manager BYOC + ExecutionProfile ---
  id: P10a
  objetivo: Gestion segura de credenciales de exchange y config de
    ejecucion por tenant.
  ADR/REST/CE: ADR-011, ADR-012, ADR-018.
  carpeta destino: backend/platform/execution (credenciales); infra/db
  dependencias: P05, P06, P06b.
  desbloquea: P10b.
  esfuerzo: M
  hecho cuando: alta/baja/rotacion de API key; envelope encryption;
    api_key_ref (nunca la key); verificacion de permisos al conectar;
    minimo privilegio; ExecutionProfile tenant-scoped; SensitiveActionAudit.
  checks obligatorios: 7.8, audit.
  validacion manual/caliente: alta de credencial con permisos verificados;
    key con retirada -> advertencia.
  fuera de alcance: DEX/wallets (v5.1).

--- PIEZA P10b: Cadena de ejecucion (gate -> risk -> order -> connector) ---
  id: P10b
  objetivo: ExecutionRequest neutral; gate fail-closed; risk; order
    manager con idempotencia/estados/reconciliacion; connector BYOC (CCXT).
  ADR/REST/CE: ADR-018 (usa ADR-012).
  carpeta destino: backend/platform/execution; infra/connectors;
    entrypoints/worker_execution, worker_reconciliation
  dependencias: P10a, P08, P03, P06.
  desbloquea: orden manual y autotrade BYOC. APARECE DESPUES del gate,
    las reglas y el cliente visual (criterio 4).
  esfuerzo: XL
  hecho cuando: ExecutionRequest (source_type signal|manual_ui) pasa gate
    y risk; order manager con client_order_id, UNKNOWN/RECONCILING y
    reconciliacion; execution.* con fills por streams privados;
    confirmacion manual sin bypass.
  checks obligatorios: 7.1, 7.8, integration de ejecucion.
  validacion manual/caliente: orden manual BYOC en sandbox; reconciliacion
    tras timeout simulado.
  fuera de alcance: DEX/wallets (v5.1).

--- PIEZA P11: Billing (Stripe) ---
  id: P11
  objetivo: Roles free/premium, integracion de pago desde la estructura.
  ADR/REST/CE: CE-10; ADR-012 (premium como capability).
  carpeta destino: backend platform/billing
  dependencias: P06.
  desbloquea: diferenciacion comercial. Se completa ANTES de cualquier
    ejecucion user-facing si una capability de ejecucion depende de plan
    premium.
  esfuerzo: M
  hecho cuando: plan resuelve capabilities por el PolicyEvaluator; alta/
    baja de plan.
  checks obligatorios: 7.8.
  validacion manual/caliente: upgrade de plan que habilita una capability.
  fuera de alcance: precios/planes comerciales (de Alvaro).

--- PIEZA P12a: Cliente shell (auth/realtime/i18n/offline) ---
  id: P12a
  objetivo: Shell PWA: capas ui-core/app-core/device-ports/device-web;
    auth (AuthSessionPort/AuthFlowPort); RealtimeClient con checkpoint;
    i18n/RTL/CJK; politica offline (no operar desde cache).
  ADR/REST/CE: ADR-019.
  carpeta destino: frontend/*
  dependencias: P01 (contratos), P06b (API/auth/realtime).
  desbloquea: P12b, P09b, P13.
  esfuerzo: L
  hecho cuando: PWA instalable; login; realtime autenticado sin inventar
    campos; offline sin operar desde cache; check 7.2 verde.
  checks obligatorios: 7.2, 7.4.
  validacion manual/caliente: spike Capacitor en dispositivo real.
  fuera de alcance: dashboard/charting (P12b/P13).

--- PIEZA P12b: app-core/ui-core inicial + dashboard shell ---
  id: P12b
  objetivo: Logica de cliente y shell de dashboard configurable
    (widgets), consumiendo capabilities y datos por contrato.
  ADR/REST/CE: ADR-019; INFORME 3.
  carpeta destino: frontend/app-core, ui-core
  dependencias: P12a, P06b, P08.
  esfuerzo: L
  hecho cuando: dashboard configurable con widgets; consume capabilities
    (premium oculta/muestra); sin logica de negocio en UI.
  checks obligatorios: 7.2.
  validacion manual/caliente: dashboard con un widget real.
  fuera de alcance: charting (P13).

--- PIEZA P13: Charting y overlays ---
  id: P13
  objetivo: ChartPort (UI adapter); KLineChart (financiero), ECharts
    (widgets); overlays de senal universales; dashboard configurable.
  ADR/REST/CE: ADR-020; INFORME 3.
  carpeta destino: frontend/ui-core
  dependencias: P12b, P08.
  esfuerzo: L
  hecho cuando: velas + overlays de signal.* por contrato; overlay
    universal en toda jurisdiccion; widgets con ECharts.
  checks obligatorios: 7.2.
  validacion manual/caliente: chart en PWA movil real (perfil movil de
    KLineChart).
  fuera de alcance: dibujo avanzado/patrones (v5.1, seccion 8).

===================================================================
4. FASES Y HITOS
===================================================================
Fase = bloque de trabajo. Hito = sistema funcionando verificable.

F0 - Base estructural: P00.
  M0: repo creado + CI de guardarrailes en verde.

F1 - Espina dorsal tecnica: P01, P02, P02b, P03.
  M1: un evento viaja de punta a punta con envelope, idempotencia y Clock
  sobre el bus externo, con outbox transaccional; reinicio sin perdida.

F2 - Sustrato plataforma: P04, P05, P06, P06b.
  M2: un Componente se descubre por carpeta, aislado por tenant/RLS, con
  capacidades por el gate fail-closed; API/auth/realtime autenticado en
  pie; kill switch corta en caliente.

F3 - Datos, reglas y notificacion backend: P07, P08, P09a.
  M3 (backend, SIN overlay y SIN ejecucion): una Rule dispara sobre datos
  reales y proyecta signal.*/alert.*; el router backend entrega por un
  canal no-PWA/mock.

F4 - Cliente visual SIN ejecucion: P12a, P12b, P13, P09b.
  M4: PWA instalable con dashboard, chart y overlays de signal.*
  universales en dispositivo movil real; push PWA; geo-blocking bloquea
  EJECUCION, no visualizacion. El producto YA VALE sin trading.

F5 - Ejecucion gateada y billing: P10a, P10b, P11.
  M5a: el gate BLOQUEA ejecucion en UE/EEA/UK a nivel API.
  M5b: orden MANUAL BYOC fuera de UE, confirmacion sin bypass.
  M5c: autotrade BYOC (signal.* -> ExecutionRequest).
  M5d: reconciliacion tras timeout/estado ambiguo.

Camino critico: P00->P01->P02b->P03->P04->(P05,P06,P06b)->P08->P10b.
P07 alimenta P08; el cliente (P12a/P12b/P13) cuelga de P06b+P08 y va en
F4, ANTES de la ejecucion (F5). La ejecucion es lo ultimo: aparece tras
reglas, gate, cliente visual y credenciales.

===================================================================
5. HITOS VERIFICABLES (mapa a CE / OBJ)
===================================================================
M0 -> disciplina anti-deuda operativa desde el commit 0 (R1-R4).
M1 -> espina dorsal (contratos/eventos/tiempo; mata L1 de v4).
M2 -> multiusuario + geo-gate + kill switch + API/auth/realtime (OBJ-1).
M3 -> BACKEND de plataforma: reglas/senales/alertas y notificacion
      backend, SIN overlay visual y SIN ejecucion.
M4 -> VALOR VISUAL de plataforma SIN trading: PWA movil con dashboard,
      chart y overlays de senal universales; geo-blocking corta
      ejecucion, no visualizacion (criterio 4 hecho visible).
M5 -> EJECUCION gateada al final (OBJ-9): bloqueo UE/EEA/UK, orden
      manual BYOC, autotrade BYOC, reconciliacion.
Nota: el orden hace CUMPLIR el criterio 4: el producto es valioso en M4
sin trading; la ejecucion llega despues como capacidad, no como eje.

===================================================================
6. TRABAJO DE VALIDACION EN CONSTRUCCION
===================================================================
Pendientes que la investigacion dejo para construccion, ubicados:
- Spike Capacitor en dispositivo real -> P12a (F4).
- Perfil movil de KLineChart (chart real en movil) -> P13 (F4).
- Redis Streams bajo carga / reinicio de consumidor -> P03 (F1).
- Reconciliacion de ordenes con timeout/estado ambiguo -> P10b (F5).
- Coste de RLS con volumen -> P05 (F2).
- Credenciales BYOC (envelope encryption, permisos) -> P10a (F5).
La politica de CUANDO y COMO se hacen estas validaciones (incluida la
validacion en caliente) se cierra en DOC_ENTREGABLES_V5.

===================================================================
7. RIESGOS DE CONSTRUCCION Y ORDEN DE MITIGACION
===================================================================
- EventBus/idempotencia (P03): cimiento; un fallo aqui contamina todo.
  Mitigar en F1 con equivalente local y pruebas de reinicio.
- Reconciliacion de ordenes (P10b): dinero real; sandbox antes de
  cualquier exchange real.
- Perfil movil del chart (P13): validar en dispositivo real antes de
  comprometer UI compleja.
- Coste de RLS (P05): medir con volumen antes de escalar usuarios.
- Credenciales BYOC (P10a): seguridad sensible; probar cifrado y minimo
  privilegio pronto.
- Deriva de features: el mayor riesgo de proceso; se mitiga con los
  principios de secuenciacion (sec.2) y CI desde P00.

===================================================================
8. FUERA DE ALCANCE / HERENCIA v5.1+
===================================================================
No se planifican aqui; se referencian (DOC_ARQ_V5 sec.9):
- Wallets frias (MetaMask) + DEX.
- Libreria de charting propia (via ChartPort).
- Dibujo avanzado sobre la API de overlays de KLineChart.
- Fork de KLineChart si una herramienta excede su API.
- Rol de administracion/compliance auditado.
Su lado legal/regulatorio/comercial es de Alvaro con asesoria. Entran al
roadmap solo cuando se abra la planificacion de v5.1+.

===================================================================
9. RELACION CON DOC_ENTREGABLES_V5
===================================================================
El "hecho cuando" y los "checks obligatorios" de cada pieza (sec.3) se
apoyan en la politica de entregables, fixes y validaciones en caliente
de DOC_ENTREGABLES_V5. Este roadmap define QUE se construye y en que
orden; aquel define QUE significa "entregado" y como se gestionan fixes
y validaciones. No se duplican: si hay conflicto, ENTREGABLES manda en
politica de entrega y ROADMAP manda en orden/dependencias.

FIN DOC_ROADMAP_V5 (v1.0, aprobado CSA + firmado Alvaro 2026-07-06).

=====================================================================
SECCION DE AMPLIACION A-1 (append-only) -- M3 AMPLIADO A PARIDAD
FUNCIONAL v4 (EXP-M3-01)
=====================================================================
Naturaleza: AMPLIACION del plan. Firmada por Alvaro: 2026-07-17 (la
expansion) y 2026-07-18 (reflejarla aqui). Doble revision Central + CSA.
NO reabre ningun ADR: cubre el hueco del catalogo concreto de DataSources
que ADR-014/008/015 ya preveian y que INFORME 6 (12.5/17) dejo
EXPLICITAMENTE a construccion.
El contenido ORIGINAL de este documento (v1.0) queda INTACTO y es
HISTORICO: era cierto cuando se escribio. Donde v1.0 diga que M3 son tres
piezas (P07, P08, P09a), MANDA ESTA SECCION: M3 son SIETE.
Decisiones asociadas: REGISTRO_DECISIONES seccion 21.
Informes que fundamentan estas fichas (en el knowledge): I-01
(indicadores/KLineChart), I-02 e I-02-V (feasibilidad de ingesta), I-03
(swings, pivotphase, divergencias), I-04 (orderflow y modelo de confianza).

A-1.1 M3 AMPLIADO: PIEZAS Y ORDEN
---------------------------------------------------------------------
  P07  Ingesta de market data (velas) ................ ENTREGADA
  T-03 Conectores OKX y Bybit (transversal) .......... ENTREGADA
  P07b Ingesta de trades + footprint ................. NUEVA
  P07c Ingesta de orderbook L2 con estado ............ NUEVA
  P08  Motor de reglas ............................... EN CURSO
  P08b DataSources candle-derived .................... NUEVA
  P08c DataSources footprint/L2-derived + pivotphase . NUEVA
  P09a Router de notificaciones backend .............. PENDIENTE
Orden: P07 -> T-03 -> P07b -> P07c -> P08 -> P08b -> P08c -> P09a.
Paralelismo admitido: P08 || P07b || P07c || P08b.
P08c espera a P07b + P07c. P09a va tras P08 (consume alert.*).
Inventario del proyecto: 19 -> 23 unidades de trabajo.
M3 no cambia de naturaleza (datos reales + reglas + notificacion backend);
crece su cobertura de datos hasta la paridad funcional con v4.

A-1.2 FICHA P07b -- INGESTA DE TRADES + FOOTPRINT
---------------------------------------------------------------------
OBJETIVO: ingerir el TRADE INDIVIDUAL de los tres exchanges y derivar el
FOOTPRINT (celda = nivel de precio x barra), base de orderflow, absorcion y
volume profile.
DEPENDE DE: P07. Paralela a P07c, P08 y P08b.
FAMILIA: MarketStreamKey data_family=trades (ADR-014, ya prevista).
ALCANCE (SI):
- Trades INDIVIDUALES (no aggTrade): mas fieles; no dependen de la
  agregacion propia del exchange.
- Lado AGRESOR por el flag publico del exchange (Binance `m`, Bybit `S`,
  OKX `side`): clasificacion EXACTA y determinista, NO estimada. Por eso el
  footprint sale reproducible BIT A BIT (ADR-007). La regla de tick queda
  solo como fallback degradado documentado.
- Footprint por celda: volumen agresor comprador/vendedor por nivel de
  precio y barra; delta por celda y por barra.
- Ingesta PUBLICA sin tenant (scope=public_market, compartida cross-tenant).
- Retencion/trimming por familia.
- Validacion contractual del dato de exchange como entrada NO CONFIABLE
  (ADR-006): nada de floats/strings opacos sin normalizar.
ALCANCE (NO): orderbook L2 (P07c); DataSources del catalogo (P08b/P08c);
reglas (P08).
RESTRICCIONES VINCULANTES:
- CE-14: la familia nueva entra por ConnectorRegistry SIN tocar el nucleo de
  ingesta. Si obliga a tocar nucleo, MarketStreamKey o fronteras, se PARA y
  se ELEVA (ese fue el sentido de T-03).
- Regla 5.20: escritura por rol de ingesta estrecho, guardias de arranque
  bidireccionales.
- Regla 5.15: barrido de linea base de seguridad de la familia nueva.
- Medicion EMPIRICA de volumen antes de dimensionar recursos.
- REPRODUCIBILIDAD DEL FOOTPRINT (marcado NO VERIFICADO por I-04 1.1; se
  cierra AQUI): fijar orden DETERMINISTA para trades con el mismo timestamp
  en ms, y declarar la regla de bucketing por vela (timestamp del trade;
  dimension timezone/offset en la cache_key).
- AHP (DEC-AHP-01) para cualquier detector estadistico que cuelgue.
DoD: footprint reproducible BIT A BIT desde los mismos trades; Actions verde
3/3; barrido 5.15 escrito control por control; cero skips (5.18).
VALIDACION EN CALIENTE (obligatoria): footprint construido desde los TRES
exchanges reales; demostrar reproducibilidad (mismo input -> mismo
footprint) y reconexion sin perder ni duplicar.
REFERENCIAS: I-02 (lado agresor por exchange), I-04 Parte 1.

A-1.3 FICHA P07c -- INGESTA DE ORDERBOOK L2 CON ESTADO
---------------------------------------------------------------------
OBJETIVO: mantener un LIBRO L2 LOCAL CON ESTADO a partir del stream de
deltas publico. Es PIEZA PROPIA, no "una familia mas" de P07b: introduce
estado, semilla, aplicacion de deltas, deteccion de huecos, resync y
semantica de correccion propia.
DEPENDE DE: P07. Paralela a P07b, P08 y P08b.
FAMILIA: MarketStreamKey data_family=orderbook (ADR-014).
FUENTE (VERIFICADA CONTRA DOC PRIMARIA, I-02-V 2026-07-17):
- Binance Spot: <symbol>@depth@100ms (deltas) + SEMILLA por REST
  /api/v3/depth. Integridad por U/u; si U > id_local+1 -> reiniciar.
- OKX: canal `books` (400 niveles, 100 ms), SEMILLA en el primer mensaje WS
  (action=snapshot, prevSeqId=-1). Integridad por seqId/prevSeqId; ante
  discontinuidad -> re-suscribir.
- Bybit v5 Spot: orderbook.{depth}.{symbol} (50=20ms / 200=100ms), SEMILLA
  en el primer mensaje WS (type=snapshot). Integridad por u/seq; u=1 ->
  reset.
PROHIBIDO:
- Usar como fuente continua las FOTOS PARCIALES periodicas (Binance
  @depth5/10/20; OKX books5).
- Usar canales TICK-BY-TICK CON LOGIN (OKX books-l2-tbt / books50-l2-tbt:
  exigen VIP4, error 64003).
- Depender de CHECKSUM: el de OKX esta DEPRECADO (valor fijo 0). La
  integridad es POR NUMEROS DE SECUENCIA en los tres.
EXCEPCIONES DOCUMENTADAS DE OKX QUE NO SON HUECO: keepalive con
seqId == prevSeqId (~60 s sin cambios) y reset de mantenimiento con
seqId < prevSeqId. No tratarlas como discontinuidad.
ALCANCE (SI): libro local reconstruible; re-snapshot OBSERVABLE con
metricas; fault isolation por MarketStreamKey; backpressure; reparto de
canales por limite de conexion; SEMANTICA DE CORRECCION DEL LIBRO PROPIA (no
copiar candle_corrected).
ALCANCE (NO): absorcion por refill/iceberg (es v5.1, DEC-ABSORCION-01);
DataSources del catalogo (P08c).
RESTRICCIONES VINCULANTES: CE-14; regla 5.20; regla 5.15 con barrido PROPIO
y mas exigente que P07b; medicion empirica de volumen.
DoD: tests de HUECO, RESET, DUPLICADO, DELTA FUERA DE ORDEN y SNAPSHOT
CORRUPTO; Actions verde 3/3; barrido 5.15; cero skips.
VALIDACION EN CALIENTE (obligatoria): libro vivo contra los TRES exchanges
reales; provocar un hueco y demostrar el re-sync; metricas de re-snapshot.
NOTA (DA-02-1, resuelta con doc primaria): OKX publico es el libro MAS
PROFUNDO de los tres (400 niveles); no hay asimetria de profundidad, y 100 ms
sobra porque los observables l2.* usan ventanas de 60 s y 300 s.
REFERENCIAS: I-02, I-02-V.

A-1.4 FICHA P08b -- DATASOURCES CANDLE-DERIVED
---------------------------------------------------------------------
OBJETIVO: catalogo de DataSources que solo necesitan VELAS.
DEPENDE DE: P07. Paralela a P08, P07b y P07c.
ALCANCE (SI):
- Indicadores firmados: RSI (Wilder), EMA (sembrada con el primer cierre),
  MACD (histograma x1), SMA real.
- candle.*, volume.*, vwap.*, fib.* (contexto fijo 1D segun lo firmado).
- swing.* GEOMETRICO: primitiva UNICA (mismo metodo y mismo N/R) para los
  pivotes de PRECIO, de RSI y de CVD (DA-I03-4). Ancla determinista =
  pivote por FUERZA SIMETRICA N=R (el fractal es el caso N=R=2); ZigZag/ATR
  solo para la vista y el encadenado, nunca como fuente determinista
  (DA-I03-1).
- divergence.* PRECIO-vs-RSI (paridad v4).
ALCANCE (NO):
- Divergencia de VOLUMEN: v5.1 (DEC-DIVERGENCIA-01; v4 no la diseno).
- swing.confidence: NO existe. La confianza de orderflow vive en
  pivotphase.confidence (DEC-PIVOTPHASE-01). swing.* se queda geometrico.
- Observables SUBSUMIDOS por las funciones canonicas (value_at, average,
  change, previous_value, is_active, elapsed_since): NO se implementan como
  DataSource propia; hacerlo seria codigo muerto.
- Cualquier cosa que necesite footprint o L2 (P08c).
RESTRICCIONES VINCULANTES:
- cache_key_schema COMPLETO desde el PRIMER commit: datasource_id, exchange,
  symbol, timeframe, price_source, bucket_offset, formula_version,
  parametros, ventana y as_of si aplica (ADR-008). Dejarlo para despues
  rompe caches, comparativas y reproducibilidad.
- snapshot+replay ante correccion (DEC-SNAPSHOT-REPLAY-01): exacto y O(1)
  para los indicadores recursivos; los derivados por ventana (swings,
  divergencias) recomputan su VENTANA acotada, sin snapshot propio.
- Naming en INGLES (ADR-016).
- El warm-up del RSI GATEA la divergencia (no emparejar con RSI inmaduro).
- UNA sola implementacion para backtest y produccion (I-01).
DoD: verificacion contra TradingView como DoD auxiliar; Actions verde 3/3;
cero skips.
VALIDACION EN CALIENTE (obligatoria): series calculadas sobre datos reales y
comparadas con TradingView.
REFERENCIAS: I-01, I-03 (Tandas 1-2).

A-1.5 FICHA P08c -- DATASOURCES FOOTPRINT/L2-DERIVED + PIVOTPHASE
---------------------------------------------------------------------
OBJETIVO: catalogo derivado de footprint y L2, y el motor compuesto
pivotphase.
DEPENDE DE: P07b (footprint) y P07c (l2). VA DESPUES DE AMBAS.
ARRANQUE OBLIGATORIO: volcado de SOLO LECTURA del codigo de v4 (estilo T-03
Paso 0) para replicar la logica con paridad literal. Ficheros recuperados y
ya verificados (GAP-P08c cerrado): models/pivot_state_machine.py,
engines/l1/pivot_phase_engine.py, engines/l1/volume_profile_engine.py.
NO se arrastra basura del repo de v4 (CE-8: codigo muerto = 0).
ALCANCE (SI): footprint.*, absorption.* (FOOTPRINT-BASED, tipos bid/ask +
exhaustion), orderflow.* (delta, delta_momentum), CVD (DataSource NUEVA en
v5.0), vp.* (POC; value area 70%; VAH/VAL; HVN > media*1.5; LVN < media*0.3;
ventana 100 -- paridad v4), climax.*, void.*, l2.*, notrade.*, y
pivotphase.* (FSM de fases 0-5 de v4).
ALCANCE (NO): absorcion por refill/iceberg (v5.1); divergencia de volumen
(v5.1); backtester (v5.1); observables subsumidos.
RESTRICCIONES VINCULANTES:
- DEC-PIVOTPHASE-01 (firmada; DERIVA RATIFICADA): se replica la ESTRUCTURA
  de la FSM 0-5 de v4 (IDLE / IMPULSE / ENCOUNTER / ABSORPTION / EXHAUSTION
  / FLIP, con sus invalidaciones estructurales y sus parametros
  inyectables), pero la CONFIANZA la produce el modelo rico de I-04 (F1-F7
  normalizado y calibrado), SUSTITUYENDO la formula simple de v4
  (50 + zone_strength/2). El proxy de v4 "notrade como exhaustion" (Fase 4)
  puede sustituirse por un exhaustion de delta real.
- DA-I04-1 (anti-doble-conteo INTERNO): pivotphase consume cada insumo UNA
  sola vez via su fase y NO re-gradua evidencia ya incorporada.
- Regla semantica 5 (nivel Rule): pivotphase.* declara consume_internamente
  {orderflow, vp, absorption, notrade} y el validador RECHAZA combinarlo con
  sus insumos en la misma regla (doble conteo).
- DA-I03-9: pivotphase es ORDERFLOW-DRIVEN (el pivote emerge de la secuencia
  de fases); NO se unifica con la primitiva geometrica swing.*. Son dos
  nociones distintas de pivote.
- l2.* no_combinable si su estado no es algebraicamente combinable.
- CVD: reset_policy es PARAMETRO declarado (session-UTC | rolling) y entra en
  la cache_key. El CVD es un INTEGRADOR: no olvida, asi que una correccion en
  la barra k desplaza TODO el CVD posterior. Se snapshotea el acumulador POR
  VENTANA DE RESET y se replaya dentro de la ventana afectada. La divergencia
  de CVD exige CVD CONTINUO entre los dos swings comparados.
- DA-I03-5: la divergencia de CVD (orderflow, v5.0) NO es la divergencia de
  volumen (v5.1). No confundirlas.
- AHP OBLIGATORIO (DEC-AHP-01 = ANALISIS HISTORICO PREVIO, no el metodo de
  Saaty) para cada detector estadistico: absorcion, scores de orderflow,
  climax, void, notrade y la confianza de pivotphase. Ningun umbral se fija
  de memoria. Anti-fuga temporal en la validacion: walk-forward con purga y
  embargo.
- cache_key_schema completo; naming en INGLES (ADR-016); una sola
  implementacion backtest+produccion.
DoD: un AHP escrito por cada detector estadistico; Actions verde 3/3; cero
skips.
VALIDACION EN CALIENTE (obligatoria): pivotphase sobre datos reales;
demostrar el ciclo provisional -> confirmado / retractado y la
reproducibilidad tras una correccion.
REFERENCIAS: I-02, I-03 (completo + addendum de cierre de GAP-P08c), I-04
(completo).
FIN DE LA SECCION DE AMPLIACION A-1.
