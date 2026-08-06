# CONTEXTO PARA EL CSA (ChatGPT) - CONSTRUCCION Crypto Engine V5

Proposito: dar al CSA (revisor consultivo, ChatGPT) el contexto minimo y
estable para revisar las piezas. El CSA revisa coherencia y calidad
contra los documentos-norte; NO decide (firma Alvaro). Archivo vivo
mantenido por Claude Code.

Ultima actualizacion: 2026-08-06 (P08b ENTREGADA: cierre formal Central+CSA CONFORME,
firmada por Alvaro. PR #6, Actions VERDE 3/3 (run 31078282783). M3 sigue abierto
-6/8: faltan P09a y P10-).

Anterior: 2026-08-04 (P08c ENTREGADA: cierre formal Central+CSA CONFORME,
firmada por Alvaro. 9 merges en main. M3 sigue abierto -5/7: faltan P08b y P09a-).

Anterior: 2026-07-30 (P08c sub-pieza MATERIALIZACION CERRADA y firmada; merge ca4d5f4).

Anterior: 2026-07-26 (P07c ENTREGADA: firmada).

## 1. Que construimos
CE v5: plataforma comercial multiusuario de analisis cuantitativo y
automatizacion sobre mercados de cripto (web + PWA instalable). NO es un
bot de trading: el trading es una capacidad gateada (BYOC, solo donde la
regulacion lo permite), no el eje. Monolito modular multiproceso sobre
EventBus externo; todo es un Componente por contratos.

## 2. Documentos-norte (CERRADOS y firmados; NO se reabren)
DOC_ARQ_V5, ADRS_PROPUESTOS (ADR-001..020), DOC_ESTRUCTURA_V5,
DOC_ROADMAP_V5, DOC_ENTREGABLES_V5. Snapshot en docs/ y docs/adr/. Si la
construccion revela un ADR incompleto, se ELEVA a Alvaro como cambio
arquitectonico; no se parchea en silencio.

## 3. Regla dura de construccion paso a paso
El periferico NUNCA entrega la pieza completa de golpe: micro-pasos, cada
uno explicado, Alvaro ejecuta y pega salida real, luego el siguiente.
Persistencia via Claude Code. (Detalle en REGISTRO_DECISIONES sec.1.)

## 4. Resultado de M0 / P00
P00 (esqueleto + CI base) ENTREGADA; M0 CERRADO. Commits d3f7ad6 ->
15f936d. Guardarrailes bloqueantes de Pieza 0 en verde 11/11 (validacion
en caliente local): backend (ruff, mypy strict, import-linter 7.1,
check_generated 7.4, pytest) y frontend (biome, type-check gate,
dependency-cruiser 7.2). Verificado que las fronteras muerden.
CI: checks equivalentes al workflow validados en local; Actions pendiente
por ausencia de remoto (no dar "Actions verde" por bueno hasta configurar
remoto y que corra).

## 5. Diferidos pendientes (tareas de entrada)
P01: tools/gen_schemas.py, tools/gen_ts_types, contracts/VERSIONING.md;
activar checks 7.3 y 7.7. P04: tools/check_manifests (7.5),
tools/check_orphans (7.6). (Detalle en REGISTRO_DECISIONES sec.3.)

## 6. Entorno
Backend: uv + Python 3.13. Frontend: Node 24 + pnpm 11, Biome, tsc,
dependency-cruiser. Windows local requiere PYTHONUTF8=1 y
PYTHONIOENCODING=utf-8. Repo con eol=lf.

## 7. Como revisa el CSA
Revisa cada pieza contra su ficha de DOC_ROADMAP ("hecho cuando", checks
obligatorios), DOC_ESTRUCTURA (fronteras/guardarrailes) y DOC_ENTREGABLES
(DoD, deuda prohibida, fixes). Senala incoherencias y riesgos; no reabre
arquitectura; decide Alvaro.

=====================================================================
REVISION CSA - PIEZA P01 (hito M1) - 2026-07-09
=====================================================================
Veredicto CSA: CONFORME, con condicion operacional (commit + barrido
limpio + hash) ya CUMPLIDA. Central conforme. Firmado por Alvaro.
Commit: 17bb584.
Puntos validados por el CSA:
- DoD de P01 cumplido (DOC_ENTREGABLES sec.4).
- Decisiones D1-D6 no reabren ADR ni rompen frontera; D2/D3/D5 recomendadas
  para registro (ya registradas en REGISTRO_DECISIONES sec.6).
- Envelope respeta ADR-003 y NO invade P02 (ranuras de tiempo como campos,
  sin semantica; idempotency_key required con formula por familia delegada
  al productor). frozen + extra prohibido compatible con tolerant reader
  en el borde de consumo.
- Familias: enum cerrado de 10 + naming dominio.accion (ADR-004), sin tipos
  concretos; no invade P04/P08/P09/P10.
- 7.7: el primer commit de P01 fija baseline real; desde ahi, cambio
  incompatible sin bump debe fallar.
- CI: solo-local aceptable con la formula exacta (checks equivalentes al
  workflow validados en local; Actions pendiente por ausencia de remoto).
Para la proxima revision (P02, modelo temporal y Clock, ADR-007): el CSA
debera comprobar que P02 da SEMANTICA a las ranuras de tiempo del envelope
sin reabrir ADR-003 ni el versionado (ADR-005), con Clock inyectable en
tests y maturity/watermark por familia.

=====================================================================
REVISION CSA - PIEZA P02 (hito M1) - 2026-07-09
=====================================================================
Veredicto CSA: CONFORME (entrega de pieza P02, no cierre de M1). Central
conforme. Firmado por Alvaro. Commit de pieza: 271d677.
Validado por el CSA:
- DoD de P02 y "hecho cuando" cubiertos.
- CA-01 aceptado: retipado pre-consumidor a EpochMillis con
  ENVELOPE_VERSION=1, firmado, con 7.7 honesto (rojo antes, verde tras
  commit). Queda constancia de que P01 tenia el defecto de tipo (datetime)
  corregido por CA-01.
- Deslinde temporal aceptado: asignacion/herencia en productores futuros.
- reemission: corrects_idempotency_key opcional; obligatorio en
  correction; prohibido en provisional/closed.
- Decisiones de area (no reexport para evitar ciclo; Clock int stdlib puro)
  y revision de D3 (paquete padre source.): conformes.
- TAREA FUTURA: extender el 7.7 a version-aware antes de la primera
  evolucion real de contrato con consumidores (P07/P08 a mas tardar).
Para la proxima revision (P02b, persistencia base + migraciones + outbox
transaccional, ADR-013): comprobar outbox/inbox transaccional, migraciones
y audit tecnico minimo, SIN RLS ni tenancy (eso es P05), y que la
persistencia respeta el envelope y el modelo temporal (EpochMillis) sin
reabrir contratos.

=====================================================================
REVISION CSA - PIEZA P02b (hito M1) - 2026-07-09
=====================================================================
Veredicto CSA: CONFORME (entrega de pieza P02b, no cierre de M1). Central
conforme. Firmado por Alvaro. Commit de pieza:
ed3e78833ce6789d9e435876dea8ae2c094421d4.
Validado por el CSA:
- DoD y "hecho cuando" cubiertos; atomicidad DB-outbox demostrada en caliente.
- Runner de migraciones propio (forward-only, append-only, checksum) aceptado
  frente a Alembic; respeta ADR-005 y DOC_ENTREGABLES sec.6.
- Outbox jsonb opaco: la DB no valida contrato; la validacion es del
  productor/publisher (ADR-006).
- Identidad de evento (event_id/idempotency_key UNIQUE, stream_key,
  event_type) coherente con ADR-003/013.
- Timestamps infra via now() correctos (no son tiempos de evento).
- Deslinde tenancy/RLS a P05 limpio; tablas system.
- Sin ORM, Session Protocol, psycopg_adapter unico conocedor del driver: OK.
- TAREAS FUTURAS: lock de migraciones antes de concurrencia/prod;
  cualificacion de idempotency_key en productores P07/P08/P10.
Para la proxima revision (P03, EventBus + adapter Redis, ADR-013): comprobar
publish/consume idempotente, DLQ, equivalente local, outbox/inbox
transaccional SOBRE la DB de P02b, replay por offset, y la validacion en
caliente CRITICA de reinicio de consumidor sin perder ni duplicar. P03
cierra M1.
=====================================================================
REVISION CSA - PIEZA P03 + CIERRE HITO M1 - 2026-07-10
=====================================================================
Veredicto CSA: P03 CONFORME; M1 CONFORME PARA CIERRE TECNICO. Central
conforme. Firmado por Alvaro. Commit de pieza P03:
cb25b81e2948977dfd574d5c3aff137b8a11eed5.
Validado (P03): DoD y validacion caliente critica (reinicio de consumidor
sin perder ni duplicar; 20 eventos, dedup 1); OutboxPublisher/InboxConsumer
en infra/db broker-neutrales; bus contract-agnostic con validacion en el
publisher (cierra el bypass del jsonb opaco de P02b); idempotencia de
consumidor (inbox transaccional, ACK tras commit); DLQ observable; replay
por offset con error si el offset fue purgado; empaquetado de
contracts/source en runtime + redis; fail-loud de mensaje-veneno con
cuarentena como tarea futura; 7.7 version-aware ahora prerrequisito duro
antes de cualquier evolucion de contrato.
Validado (M1): P01+P02+P02b+P03 demuestran la espina dorsal tecnica; no
falta P04/P05/P06 (son M2).
Proxima revision: M2 arranca con P04 (raiz Componente, manifest, discovery,
lifecycle; ADR-001/008/009/010). Comprobar discovery por carpeta que valida
el manifest ANTES de cargar codigo, lifecycle observable, y checks 7.5/7.6
activandose con el primer Componente real.
=====================================================================
REVISION CSA - PIEZA P04 (hito M2) - 2026-07-10
=====================================================================
Veredicto CSA: CONFORME (entrega de pieza P04; abre M2, no lo cierra).
Central conforme. Firmado por Alvaro. Commit de pieza:
866b434ec04dd3e04a9d43a9b3fa2f6f50dfd196.
Validado: DoD, "hecho cuando" y validacion en caliente (copiar carpeta +
reiniciar; lifecycle completo por el bus Redis). D8 aceptada con la regla
operativa fail-loud (publish nunca silencioso; emitir-antes-de-aplicar;
tests de regresion). D10 health separado en contrato, derivado minimo,
DEGRADED diferido. D1 direccion core->contracts correcta. D9 arista
STOPPED->FAILED dentro de ADR-010; aristas de politica a P06. D3/D4 enum
abierto y capabilities genericas conforme ADR-008. D6 loader inyectado,
valida antes de cargar (ADR-009). D7 y demas diferidos cumplen 5.11. Checks
7.5/7.6/7.9 activados. Correccion de registro: la regla 5.11 no estaba en
disco (no se anadio en el cierre de M1); se anade verbatim en este cierre.
Para la proxima revision (P05, tenancy shared-schema + RLS, ADR-011):
comprobar que toda tabla declara alcance (public_market/tenant/user/system),
RLS activo fail-closed, tests de aislamiento cross-tenant, check 7.8
activandose; y que las tablas system de P02b (outbox/inbox/audit) se
reconocen como tecnicas de sistema, no superficie tenant.
=====================================================================
REVISION CSA - PIEZA P05 (hito M2) - 2026-07-11
=====================================================================
Veredicto CSA: CONFORME (entrega de pieza P05; 2/4 de M2, no lo cierra). Central
conforme. Firmado por Alvaro. Commit de pieza: 795deb3.
Validado: DoD, "hecho cuando" y validacion en caliente critica (fuga cross-tenant
bloqueada en lectura, borrado y escritura; falla cerrado sin pertenencia;
AppRoleError con rol bypass; 7.8 demostrado que muerde). D4 (doble contexto
transaccional) aceptada como necesidad legitima de implementacion que NO
contradice ADR-011, con la policy de lectura acotada al propio principal. D3 sin
UNIQUE ni FK: preserva la costura de organizaciones; el resolver fail-closed
cubre la seguridad. D5/D6/D7/D8/D9 conformes. Cambio de semantica de DSN y las
cuatro obligaciones de persistencia futura: registrados como regla dura.
OBLIGACION VINCULANTE SOBRE P06b: app.current_user_id solo desde sesion/auth
verificada por backend, jamas desde entrada del cliente. Es el mayor riesgo
heredado de P05.
Para la proxima revision (P06, PolicyEvaluator + kill switch, ADR-012):
comprobar ALLOW/DENY/NOT_APPLICABLE con reason_code + policy_version, DENY>ALLOW,
fail-closed en sensibles, SensitiveActionAudit, y kill switch que propaga por
evento y corta una capability EN CALIENTE sin reinicio; y que el gate existe
ANTES que cualquier capacidad gateada (ADR-012 antes de ADR-018).
=====================================================================
REVISION CSA - PIEZA P06 (hito M2) - 2026-07-12
=====================================================================
Veredicto: CONFORME (Central y CSA). Firmado por Alvaro. P06 ENTREGADA (3/4 de
M2). Commit 06cb51ff4db3ab3943d374b339cf291e1541ec92.
Validacion en caliente CRITICA SUPERADA: DB -> outbox -> Redis -> consumidor ->
invalidacion -> DENY, sin reinicio del proceso, con TTL de 60 s que descarta la
expiracion del cache como causa; restauracion a ALLOW tambien en caliente.
DOS ENMIENDAS HISTORICAS (append-only, sin maquillar): P03/M1 (el publisher solo
podia publicar payloads vacios y no validaba ningun schema de payload; sus dos
ficheros de test usaban un event_type inexistente y consagraban el defecto) y P05
(el check 7.8 permitia que una tabla con tenant_id se autodeclarase system y
esquivase allowlist y RLS). Ninguna pieza se reabre; ambos guardarrailes se
corrigen hacia delante.
CORRECCION sobre P06b: el rol administrativo/compliance auditado NO es obligacion
de P06b (es herencia v5.1). La unica obligacion vinculante sobre P06b es que el
SubjectInputsResolver derive identidad y sujeto SOLO de autenticacion backend
verificada.
ENDURECIMIENTO del mapa de diferidos: siete campos obligatorios, pieza duena viva,
regla de salida, y prohibicion de diferir tipos ya en uso o a piezas cerradas.
Para la proxima revision (P06b - API/Auth/Realtime Gateway; ADR-002/006/011/012/
013/019): comprobar que app.current_user_id y el SubjectInputsResolver derivan SOLO
de la sesion verificada y NUNCA de entrada del cliente (obligaciones vinculantes de
P05 y P06); que la API NO evalua reglas ni ejecuta ordenes; que las capabilities se
exponen como INFORMATIVAS (la decision autoritativa es el PolicyGate en el punto
sensible); y que el enforcement de politica en los bordes usa el PolicyGate de P06.
P06b CIERRA M2.
=====================================================================
REVISION CSA - PIEZA P06b + CIERRE DEL HITO M2 - 2026-07-14
=====================================================================
Veredicto: CONFORME (Central y CSA). Firmado por Alvaro.
P06b ENTREGADA (4/4 de M2). M2 CERRADO.
Commit de pieza: 6864c2af23dbaca1b04f41a0cfff3c0323247223.
Commit final (PASO 0 del cierre): 52b26dba7e291611bfa6c050a6cba657fad477b9.
ACTIONS VERDE 3/3 sobre el commit FINAL. 598 tests en verde con CERO SKIPS.

LA PRUEBA DEL HITO M2. El operador activa un kill switch desde OTRO PROCESO y con
OTRA CREDENCIAL, y la capability pasa a DENY EN EL BORDE DE LA API en 0,52 s, SIN
reiniciar nada (mismo PID) y POR EVENTO: operador -> DB -> outbox -> bus ->
invalidacion de cache -> DENY. El TTL del cache (60 s) queda DESCARTADO POR DISENO
DEL ARNES, que ABORTA si el corte tarda lo que dura el TTL: la demostracion NO PUEDE
MENTIR. Al soltar el switch, vuelve a ALLOW en 0,52 s.

REGLAS DE PROCESO NUEVAS (detalle verbatim en REGISTRO_DECISIONES sec.5)
- 5.17 EL COMMIT NO ES LA ENTREGA. El commit de pieza va ANTES de la firma (5.13
  exige Actions verde, y Actions no corre sin commit empujado). La firma no gatea el
  commit: gatea la TANDA DE CIERRE y el estado ENTREGADA.
- 5.18 CERO SKIPS, O SKIPS DECLARADOS. Un test que se salta en silencio es un test
  que no existe. El barrido de cierre DEBE reportar el numero de skips; CERO es el
  valor por defecto. Origen: 21 tests de integracion nunca ejecutados en local y DOS
  rotos.
- 5.19 TABLAS CON SECRETOS: VENTANILLAS ESTRECHAS. Patron CA-07 (sin privilegios de
  tabla para el rol de aplicacion; acceso por SECURITY DEFINER minimas; check
  bloqueante). VINCULANTE para P10a (credenciales BYOC).

NO CONSTRUIDOS, CON DUENO O CONDICION (que el CSA debe seguir vigilando)
- El REGISTRO revela existencia con un 409. DUENO: P09a (cerrarlo exige verificacion
  por email, que exige el router de notificaciones). Junto con el password reset.
- Contador GLOBAL de rate limit: DESCARTADO con motivo, no diferido: seria una
  palanca de DoS de plataforma (un atacante barato deja fuera a TODOS).
- Contador de conexiones WS compartido entre replicas: CONDICION DISPARADORA, no
  pieza. PRERREQUISITO DURO antes de CUALQUIER despliegue multi-replica.
- require_capability en el primer endpoint SENSIBLE: VINCULANTE para P10a/P10b (las
  cinco capacidades sensibles son suyas).
- plan y role en PolicyInputs: hoy None (lo que DENIEGA lo sensible). P11 y via v5.1.
- Proveedores reales de geo/KYC/VPN: seleccion COMERCIAL de Alvaro.

T-02 - BASELINE DE DESPLIEGUE (trabajo transversal registrado en este cierre)
El ROADMAP no tiene pieza de despliegue: hueco OPERATIVO que nadie reclamaria como
suyo. DISPARADOR: antes de cualquier entorno compartido, staging real, multi-replica
o demo externa persistente. CONTENIDO MINIMO: lock de aplicacion de migraciones (de
P02b), validacion de configuracion de produccion, contador WS compartido si hay mas
de una replica, verificacion de secretos y entorno, backup/restore basico, smoke test
de API/WS, y despliegue reproducible con Actions. No modifica el Roadmap funcional.
Decide Alvaro cuando abordarlo.

PARA LA PROXIMA REVISION (P07 - INGESTA DE MARKET DATA HIBRIDA, ADR-014)
El CSA debe comprobar:
- Streams PUBLICOS compartidos por MarketStreamKey, SIN tenant_id (el dato de mercado
  no es de nadie; meterle tenant_id lo duplicaria por cliente).
- Streams PRIVADOS BYOC con RLS y geo.
- Ref-count RECONSTRUIBLE (no un contador en memoria que se pierda al reiniciar).
- Primer market.* END-TO-END.
- TAREA VINCULANTE DE CA-06: mover los TRES market.* de DEFERRED_EVENT_TYPES a
  EVENT_PAYLOAD_REGISTRY con su payload REAL (OHLCV/timeframe). El check
  tools/check_event_payload_registry.py NO LE DEJARA OLVIDARLO.
- REGLA 5.15: P07 ABRE UNA SUPERFICIE EXTERNA NUEVA (los exchanges) y por tanto DEBE
  TRAER SU BARRIDO DE LINEA BASE DE SEGURIDAD ESCRITO, CONTROL POR CONTROL, con lo no
  construido asignado a una pieza DUENA.
=====================================================================
REVISION CSA - PIEZA P07 (hito M3) - 2026-07-15
=====================================================================
Veredicto: CONFORME (Central y CSA), con doble revision y re-revision tras cerrar dos
bloqueantes. Firmado por Alvaro. P07 ENTREGADA; ABRE M3 (1/3, no lo cierra).
Commit de pieza e7c92be; commit final f62e4e0; ACTIONS VERDE 3/3 sobre f62e4e0. 870
tests, cero skips en local.

RESUMEN DE LA PIEZA: ingesta hibrida (ADR-014). Streams PUBLICOS compartidos por
MarketStreamKey SIN tenant_id (un solo stream para todos los interesados; la ventanilla
agregada da CUANTOS piden un stream, jamas QUIENES). Streams PRIVADOS/BYOC por-usuario
gateados por politica/geo antes de INITIALIZE (connector FAKE en P07; credenciales reales
en P10a). Ref-count RECONSTRUIBLE desde los intents persistidos (no un contador en
memoria). Conector REAL de Binance Spot (feed publico, sin credenciales). Primer market.*
END-TO-END demostrado en caliente.

LAS SIETE CA FIRMADAS: A (outbox por madurez: closed/corrected atomico por outbox, updated
directo al bus fail-loud); B (rol ce_v5_ingestion + regla 5.20); C (provisional gateado por
demanda, con backpressure y metricas); D (ventanilla SECURITY DEFINER sin fuga de
identidad); E (7.7 version-aware no se dispara: P07 es aditivo); F (tres exchanges por
camino B: uno real, OKX/Bybit en T-03); G (la ventanilla chocaba con R5 del 7.8 -> allowlist
de policies + R8a-d/R9; el 7.8 se ENDURECE; doce negativas desde el CATALOGO).

LOS DOS BLOQUEANTES DE LA RE-REVISION, RESUELTOS:
- Auto-bootstrap tras reconexion CONSTRUIDO EN EL MOTOR: el conector senala reconexiones
  (drain_reconnected) y el motor (drain_once, en cada tick del componente) dispara el
  bootstrap REST por el mismo camino de dedup, con fault isolation por stream. Demostrado
  en caliente contra Binance real (rellena el hueco sin duplicar).
- Las DOCE pruebas del 7.8 endurecido LEIDAS DEL CATALOGO (pg_policies /
  pg_get_function_result), no de regex sobre .sql. La baseline es la policy REAL; se
  perturba y se comprueba que MUERDE.

EVIDENCIA C-I (para pegar): idempotency_key sin colision variando cada dimension
(exchange/timeframe/symbol/madurez, cero colisiones); candle_corrected append-only con PATH
PRODUCTOR construido (el motor emite via _emitir_correccion, no solo el contrato); check
MARKET bloqueante (ingesta estrecha, ventanilla ciega); guardia 5.20 SIN modo-test (no hay
bandera que la desactive; el arnes solo acota el ENTORNO por rol); conteo de skips por job
(661 backend / 209 integracion / 870 local, cero grietas); barrido 5.15 con FECHA
(2026-07-15) y URL de la doc oficial de spot (nota: el retiro de endpoints es de DERIVADOS,
no de spot; stream.binance.com:9443 sigue vigente); negativos de catalogo (simbolo
no-ASCII saltado y contado), cardinalidad (MAX_INTENTS_PER_SUBJECT) y pool (pasarse del tope
no abre nada).

REGLA NUEVA 5.20 (verbatim en REGISTRO_DECISIONES sec.5): menor privilegio por proceso;
nadie fabrica hechos ajenos. Vinculante para P07, P08 y P10b.

DISTINCION DE DEFENSAS (que el CSA debe vigilar para no copiar sin criterio): IDENTIDAD
(P06b) usa REVOKE TOTAL como defensa primaria (la API no lee hashes ni por error);
market_subscription_intent usa RLS atada a tenant/user como defensa primaria (el rol de app
SI escribe los intents del usuario), con la ventanilla como EXCEPCION secundaria para el
worker. Defensas distintas para necesidades distintas.

INVARIANTE HACIA P08: las reglas y senales se evaluan sobre market.candle_closed
(determinista), JAMAS sobre candle_updated (vista viva). Evaluar sobre provisional seria un
cambio arquitectonico a ELEVAR.

PARA LA PROXIMA REVISION (T-03 ANTES de P08): segundo y tercer connector publico (OKX,
Bybit v5). Prueba de fuego de CE-14: si exige tocar contratos, fronteras o MarketStreamKey,
SE PARA Y SE ELEVA. Se repite el barrido 5.15 POR CADA exchange (cada uno con su heartbeat
--Bybit 15 s no 20--, formato de vela, semantica de cierre y reconexion); NO se copia el de
Binance.

--- REVISION T-03 (2026-07-16): SEGUNDO Y TERCER CONECTOR PUBLICO (OKX, BYBIT) ---
Veredicto: CONFORME (Central y CSA); firmado por Alvaro. Trabajo transversal completado; no cierra M3.
CE-14 CUMPLIDO: OKX y Bybit anadidos sin tocar el nucleo de P07; un exchange nuevo = su carpeta en infra/connectors/<exchange>/ + una linea plana de registro.
ConnectorRegistry (T-03-A): sustituyo el if-chain de seleccion del composition root por un registro minimo por convencion (register/resolve, fail-loud, factories tipadas al puerto). Pruebas nombradas en REGISTRO_DECISIONES sec.20.
HALLAZGO DE PROCESO: ni Central ni CSA cazaron el if-chain en la doble revision de P07; lo cazo T-03 en el paso 0 (leer antes de escribir). Valida probar la extensibilidad ANTES de P08.
DEFECTOS: D2 (OKX velas por /business, no /public; fuentes secundarias erroneas); D4 (heartbeat Bybit 20 s, no 15 s; ficha equivocada). Ambos cazados por verificacion contra doc oficial. D3 (OKX 403 por User-Agent; cazado por la sonda; fix cabecera UA). Barridos 5.15 por exchange (OKX y Bybit), no copiados.

PARA LA PROXIMA REVISION (P08 - MOTOR DE REGLAS, ADR-015/016/017):
  - Raiz Rule NEUTRAL con dos productos: AlertRule y TradingSignalRule.
  - Forma canonica de la regla con HASH ESTABLE.
  - Doble ciclo evaluation/attention; veto del guardian.
  - Proyeccion rule.* -> signal.*/alert.* unida por causation_id.
  - Hard caps de complejidad: N<=5, M<=3, K<=5.
  - INVARIANTE DURO (CA-P07-A): las reglas y senales se evaluan sobre market.candle_closed, JAMAS sobre market.candle_updated (vista viva que puede perderse). Evaluar sobre provisional seria cambio arquitectonico a ELEVAR.
  - Indicadores (decisiones firmadas): convencion TradingView; warm-up con maturity_state; version de formula; UNA sola implementacion para backtest y produccion.
  - Regla 5.20 (menor privilegio por proceso) VINCULANTE para P08.

=====================================================================
REVISION CSA - PIEZA P08 (hito M3) - 2026-07-21
=====================================================================
Veredicto: CONFORME (Central y CSA), por bloques. Firmado por Alvaro 2026-07-21.
P08 ENTREGADA (2/7 de M3). NO cierra M3: quedan P07b, P07c, P08b, P08c y P09a.
Commit de pieza 59855bf; refinamiento documental de las puertas de revision 107e94f;
merge a main 143f4f0 (por git con --no-ff, para PRESERVAR ambos hashes que el registro
cita). ACTIONS VERDE 3/3 sobre 107e94f, cabeza del PR wip->main (run #18: Backend,
Backend-integration y Frontend, los tres Success). El job backend-integration corrio por
PRIMERA VEZ la provision de ce_v5_rules y el check_rules_access sobre un PostgreSQL VIRGEN
del runner: es exactamente lo que la regla 5.22 exige demostrar, y no lo daba el barrido
local. 1040 tests, CERO SKIPS en local con los cinco DSN.

RESUMEN DE LA PIEZA: una Rule dispara sobre market data REAL y proyecta alert.*/signal.*
POR TRANSICION. Emision por FLANCO (CA-P08-01): firing y resolved no se repiten vela a
vela; la auditoria por-vela se persiste pero NO va al bus. FSM K3 con veto FAIL-SAFE
(CA-P08-04). Estado y outbox en UNA sola transaccion (CA-P08-02). Ventanilla cross-tenant
rules_for_market SECURITY DEFINER donde manda el tenant de la COLUMNA, jamas el del JSON
(CA-P08-03) -- de ahi nace SystemScopedDatabase, patron reutilizable en P09a/P10b. Rol
ce_v5_rules estrecho (5.20) con guardias bidireccionales. Correccion POINT-LOCAL
end-to-end (CA-P08-08): ante una regla con fuentes no point-local el manejador OMITE la
correccion con motivo logueado y la deja NO CONFORME v5.0, sin cuarentena (es alcance no
construido, no un fallo de la regla). CA-P08-09: correction_revision pasa a int
obligatorio, correccion pre-consumidor CROSS-FRONTERA sin bump (precedente CA-01).
El "sin bump" NO es una afirmacion suelta: se apoya en una REJA DE CINCO EVIDENCIAS
enumerada por escrito en REGISTRO_DECISIONES sec.22 (validador que ya prohibia None; cero
productores que lo emitan; cero consumidores que lo acepten; cero fixtures/baselines con
None; y estrechamiento de tipo sin cambio de semantica), con REGLA DE PARADA explicita: si
faltara UNA, se detiene y se reclasifica. Es lo que el CSA debe re-verificar.

LO QUE EL CSA DEBE MIRAR CON MAS DUREZA (se declara sin maquillar):
El cierre destapo un DEFECTO DE PROCESO, no de codigo: tools/check_rules_access.py estaba
CONSTRUIDO pero NO ENGANCHADO en ci.yml, y P08 no tenia NI UN test de integracion. Es
decir, el "verde" de P08 habria sido ILUSORIO sobre su propia frontera de seguridad --
misma familia que la Enmienda Historica 1 de P03 y que el defecto de T-01 (21 tests que
nunca corrian). De ahi la REGLA 5.22, firmada por Central. Correctivo integrado en el
cierre: check enganchado, ce_v5_rules provisionado en backend-integration, y 37 tests de
integracion nuevos contra PostgreSQL real (frontera 5.20 con negativos BIDIRECCIONALES
rechazados por el MOTOR, y ciclo-nucleo atomico con el ROLLBACK demostrado forzando el
fallo del INSERT de outbox). El test de atomicidad se verifico que MUERDE: replicada
fuera del repositorio la version no atomica, el estado avanzaba con el evento rechazado.

REGLAS NUEVAS (verbatim en REGISTRO_DECISIONES sec.5): 5.21 (sobre no vacio validado en
CONSTRUCCION, no solo al publicar) y 5.22 (check bloqueante enganchado y demostrado).

LIMITACIONES v5.0 DECLARADAS (no son deuda oculta, son alcance firmado): la correccion
solo actualiza el ESTADO VIGENTE, no reescribe transiciones historicas; solo fuentes
POINT-LOCAL (EMA/RSI/MACD y CVD NO CONFORMES en v5.0, diferidas a P08b/P08c); anti-flap
/"for" no existe en v5.0. Migraciones 0013-0016 bajo la regla 5.14: correccion futura de
un grant = migracion SUCESORA, nunca editar una ya commiteada.

VERIFICADO EN ESTA REVISION: que la emision es por TRANSICION y no por vela; que el tenant
autoritativo sale de la COLUMNA y nunca del JSON; que la atomicidad estado+outbox esta
probada contra motor real y no con mocks; que los negativos de la frontera 5.20 fallan por
permiso del MOTOR (permission denied / row-level security) y no por codigo nuestro; y que
ningun check bloqueante de P08 sigue dormido (regla 5.22). Se cerraron ademas tres puertas
documentales: la reja de CINCO evidencias de CA-P08-09 por escrito con su regla de parada;
el deslinde de vocabulario entre la omision de correccion en RUNTIME y los skips de pytest;
y la correccion de la errata "cuatro DSN" -> "cinco DSN", registrada en este cierre.

PARA LA PROXIMA REVISION: el orden de M3 continua con P07b (trades+footprint), P07c
(orderbook L2), P08b y P08c (DataSources), y P09a (router de notificaciones backend), que
es quien consume signal.*/alert.*. Para P09a, la herencia dura de P08 es que las
proyecciones alert.raised/signal.raised salen POR TRANSICION y ya vienen unidas a su
rule.firing por causation_id: el router entrega esos hechos, no los reinterpreta.

=====================================================================
REVISION CSA - PIEZA P07c (hito M3) - 2026-07-26
=====================================================================
Veredicto: CONFORME (Central y CSA). Firmado por Alvaro 2026-07-26.
P07c ENTREGADA (4/7 de M3). NO cierra M3: quedan P08b, P08c y P09a. Entre P08 (revision
anterior, 2026-07-21) y esta cierran tambien P07b (trades+footprint, 2026-07-23; ver
REGISTRO_DECISIONES sec.26) sin bloque propio en este documento. HEAD 8869ec9, PR #3
mergeado a main. Actions run 30195904966, 3/3 success sobre el HEAD (Backend,
Backend-integration y Frontend). ci_local 24/24; 1587 tests unit + 319 integracion, CERO
skips/xfail.

RESUMEN DE LA PIEZA: motor del libro L2 CON ESTADO y ORDER-DEPENDIENTE (a diferencia del
de trades, gemelo pero sin estado): un OrderbookBook vivo por stream, deltas aplicados EN
ORDEN con backpressure. Continuidad propia por exchange -- Binance con puente
U<=lastUpdateId+1<=u SOLO en el primer delta tras la siembra (regla oficial I-02, cierra
un hallazgo de construccion), OKX con prevSeqId estricto y sus dos excepciones que NO son
hueco (keepalive seqId==prevSeqId; mantenimiento seqId<prevSeqId), Bybit con reset u==1
que reconstruye en banda --. Un hueco REAL detectado por el motor publica su PROPIO hecho
(market.orderbook_resynced), nunca una correccion al estilo candle_corrected: un libro no
se corrige retroactivamente, se REINICIA desde una foto nueva. Snapshots top-K en dos
variantes de un mismo payload: FRONTIER (as-of el cierre de barra, publicado por outbox,
uno por barra, fire-anyway aunque el libro no tenga semilla) y SAMPLE (intra-ventana a
cadencia, persistido sin publicar, como los trades). is_complete FAIL-SAFE identico al
footprint: un hueco que solapa la ventana marca la barra incompleta aunque el libro ya se
hubiera recuperado. Topologia b-i: multiplex en worker_ingestion bajo ce_v5_ingestion,
salvo OKX que abre una 2a conexion a /ws/v5/public para el libro (mismo proceso/rol) --
correccion de trazabilidad frente a la premisa "cero sockets nuevos", cierta para
Binance/Bybit pero erronea para OKX, que separa el endpoint de libro del de trades/velas.

LO QUE EL CSA MIRO CON MAS DUREZA (dictamen G2, dos rondas de remediacion, ninguna toco el
motor de ingesta):
(G1) Las reglas de proceso 5.31 (bateria de CI por Claude Code, con salida cruda VERBATIM
como condicion) y 5.32 (validacion en caliente conducida por tandas, evidencia cruda
obligatoria) se USARON durante toda la pieza pero NO estaban REGISTRADAS ni FIRMADAS al
cerrar en caliente (commit f3870d0). Corregido: registradas verbatim en REGISTRO_DECISIONES
sec.5 (commit 7569401) y ahora FIRMADAS. Se anadio ademas la 5.33 (nivel/modelo como
criterio de agrupacion y separacion de tandas, extiende 5.23), tambien firmada, con su
mecanica de cabecera obligatoria: etiqueta [MODELO: X] + comando /model exacto citado
fuera del bloque.
(G2) El snapshot del libro se persistia con una idempotency/cache_key explicita (K,
cadencia, timeframe, as_of, formula_version, clock_source) pero SIN declaracion ADR-008:
el marco declarativo no conocia la fuente ni sus dimensiones de cache. La nota de cierre
en caliente lo habia marcado "diferido a v5.1" por error -- confundia el diferido REAL
(libro profundo + delta-log crudo, que SI sigue en v5.1) con la declaracion, que no
dependia de eso. Corregido: orderbook_snapshot_declaration() en
platform/rules/rawbook.py, espejo de market_close_declaration(), con cache_key_schema de
DIEZ dimensiones explicitas -exchange, symbol, market_type, data_family, depth_k,
cadence_ms, timeframe, frontier_time_anchor, formula_version, clock_source (esta ultima
por dictamen G2/clock_source, cerrando una observacion que el propio doc de mapeo habia
dejado abierta)-, ADITIVA (CE-14: no se cablea en el catalogo vivo del worker de reglas,
que exigiria un evaluador que tocaria el nucleo). Cada dimension tiene su test de
mutacion, verificado que MUERDE. Los 27 requisitos de la seccion 12 del CSA quedan
emparejados 1:1 con tests reales en docs/MAPEO_TESTS_P07c_SECCION12_CSA.md; tres de ellos
(24, 25, 27) son de PROCESO, no de test, y se marcan como tales con su artefacto exacto --
el 27 en particular distingue REGISTRADAS (verificable) de FIRMADAS (acto humano, no
verificable por el periferico).
Red de seguridad anadida por decision de Alvaro (no del CSA): los hosts
data-*.binance.vision (fix ee21f0f, dominio de DATOS no geobloqueado por MiCA) quedan
CLAVADOS en frio por test, para que un revert accidental al host geobloqueado se ponga
rojo sin esperar a la siguiente validacion en caliente.

REGLAS NUEVAS (verbatim en REGISTRO_DECISIONES sec.5): 5.31 (ejecucion de la bateria de CI
por Claude Code), 5.32 (conduccion de la validacion en caliente por tandas) y 5.33 (nivel/
modelo como criterio de agrupacion y separacion de tandas, extiende 5.23).

LIMITACIONES v5.0 DECLARADAS (no son deuda oculta, son alcance firmado y fechado): libro
profundo completo (mas alla del top-K) y delta-log crudo NO persistidos, diferidos a v5.1
(5.11) -- DISPARADOR DE REVISION: si el market data empezara a fluir a produccion antes de
que v5.1 construya la retencion profunda, se reabre, o seria historia perdida en
silencio--. execution.*: no hay tablas hasta M5, asi que la prueba negativa completa del
cruce de roles queda pendiente de que existan; hoy se verifica que el ingestor no fabrica
un execution.* por la outbox. La lectura legal de los dominios .vision bajo MiCA queda
PENDIENTE de asesoria de Alvaro antes de cualquier postura de PRODUCCION (no bloquea dev).

VERIFICADO EN ESTA REVISION: que el resync es un hecho publicado propio y no una
correccion; que is_complete es fail-safe en la MISMA logica que el footprint (ventana
solapada, no solo estado actual del libro); que la declaracion ADR-008 es ADITIVA de
verdad (no aparece en el catalogo vivo del worker de reglas); que cada dimension de la
cache_key discrimina la clave REAL persistida, no solo la declarada; que ningun requisito
de la seccion 12 quedo sin test o sin artefacto de proceso citado; y que las reglas 5.31/
5.32/5.33 estan registradas Y firmadas, no solo usadas.

PARA LA PROXIMA REVISION: el orden de M3 continua con P08b y P08c (DataSources
candle-derived y footprint/L2-derived), que consumiran por primera vez las declaraciones
ADR-008 -incluida orderbook_snapshot_declaration()- desde el catalogo real; y P09a (router
de notificaciones backend). P08c hereda de P07c el criterio memory_model=RECURSIVE del
libro: cualquier fuente derivada del orderbook NO es candidata a correccion por ventana
acotada, a diferencia de las derivadas de market.close (POINT_LOCAL).

## CIERRE: sub-pieza P08c MATERIALIZACION (CE-14) - 2026-07-30
Estado: CERRADA con APROBADO FINAL (Central + CSA + Alvaro). Merge ca4d5f4 en main con
Actions VERDE 3/3 (run 30564656066); ci_local 24/24, cero skips/xfail. Rango
873453f..c762ffc. SIN deuda: el handoff a P08b esta entregado
(docs/HANDOFF_P08c_MATERIALIZACION.md, requerimiento P08c-R1). OJO: cierra la SUB-PIEZA,
no la pieza P08c ni M3.

QUE QUEDA CONSTRUIDO. El catalogo VIVO se puebla por discovery EXPLICITO -cada modulo
productor expone declarations(), agregadas por discover_declarations- y se valida antes
de compilar nada (grafo completo y aciclico + cache_key por naturaleza). La
materializacion despacha por SOURCE_ID contra un registro polimorfico (Protocol
SourceMaterializer) que vive en el composition root del worker, no en la declaracion
(dato puro ADR-008) ni en platform. Cableadas: market.close (POINT_LOCAL), vp.poc/vah/val
(WINDOWED, ventana 100), orderflow.delta (POINT_LOCAL) y cvd.value (INTEGRATOR).

PUNTOS QUE EL CSA DEBE TENER PRESENTES (no son deuda, son contexto vivo):
- GUARDA DEL COMPILADOR, TEMPORAL. El contrato SI admite params de fuente
  (DataSourceRef.params) y el validador semantico los acepta, pero el compilador los
  descartaba y la materializacion usaria el default: servir 50 a quien pidio 7 seria la
  deuda D-E2.1 de v4 reproducida, y ademas mentiria la cache_key (bin_count SI esta en
  ella). Se RECHAZA en compilacion (CompilationError -> cuarentena). Se RETIRA cuando el
  compilador propague params (MAT-05 Q4).
- CvdIntegratorSpec TIENE EFECTO. Es el unico materializador con ESTADO: tras calcular la
  serie PERSISTE el snapshot de la barra vigente (MAT-07 D2). Es idempotente (ON CONFLICT
  DO NOTHING) y no escribe nada si no hay base. Un materializador que escribe es una
  novedad respecto a los demas, que son lecturas puras.
- cvd_snapshot es scope=system y el rol de reglas ESCRIBE en el. Es la primera vez que
  ce_v5_rules escribe algo que no es su outbox ni su estado de regla. Se acota a
  SELECT+INSERT (append-only: UPDATE/DELETE/TRUNCATE revocados y verificados en los dos
  sentidos por check_rules_access, categoria RULES_STATE_TABLES). NO es dato de mercado:
  es su estado de replay, por eso no cruza frontera de tenant.
- MIGRACION 0022 EDITADA EN SITIO. Se corrigio el COMMENT (le faltaba isolation_scope,
  que exige el check 7.8) reaplicandola, no con una sucesora. Condiciones que lo
  justificaron: no mergeada, no publicada en main, no aplicada en entorno no desechable,
  base local desechable, CI la aplica desde cero, checksum no parcheado a mano, 0 filas.
  Juicio 5.14-adyacente: la 5.14 protege migraciones PUBLICADAS.
- validate_rules_worker.py NO ESTA EN ci_local. Su mapa sintetico de grants quedo
  desalineado por T5a-1/T5b-2a y estuvo ROJO varias tandas sin que la bateria lo notara,
  precisamente porque no esta enganchado. Ya esta arreglado (c762ffc, el mapa deriva de
  RULES_STATE_TABLES para auto-seguir), pero DECIDIR SI ENTRA EN ci_local es una decision
  separada y PENDIENTE. Roza la regla 5.22 (un check que existe y no esta enganchado es
  un check que no existe).

PARA LA PROXIMA REVISION: P08c continua con sus sub-piezas restantes (deteccion) y P08b
puede arrancar ya sobre este mecanismo: sus fuentes candle-derived se declaran y
materializan con el MISMO patron, sin reabrirlo. orderflow.delta_momentum (WINDOWED sobre
una fuente derivada, DAG de 2o nivel) sigue SIN cablear a proposito: si una regla la
referencia, el motor falla ruidoso en vez de servir una serie equivocada.

## CIERRE: sub-pieza P08c delta_momentum - merge 9025588
orderflow.delta_momentum CABLEADA como DAG de 2o NIVEL: consume otra fuente DERIVADA
(orderflow.delta), no el footprint crudo, mediante un DerivedSeriesSpec nuevo
(base_source_id + transform de serie + lookback=1). MAT-08: si la base de un
DerivedSeriesSpec no esta en el registro se levanta UnwiredSourceError, extendiendo el
fallo ruidoso de MAT-06 al segundo nivel del DAG. Con esto queda cerrado el unico
"sin cablear" que dejaba la sub-pieza de materializacion.

## CIERRE: sub-pieza P08c PIVOTPHASE (FSM + confidence + CE-14) - 2026-07-31
Estado: CERRADA. Merge f0a728b en main (--no-ff) con Actions VERDE 3/3 (run 30664591767).
Rango c1803ae..5dfe278. OJO: cierra la SUB-PIEZA, no la pieza P08c ni M3.

LECTURA DE ci_local (para que no se lea como regresion). En LOCAL la bateria da 23/24 con
un unico rojo: el check 7.8 por la tabla ema_snapshot, que crea la migracion 0023 de la
pieza HERMANA P08b y vive en el PostgreSQL local COMPARTIDO entre worktrees. Es AJENA a
esta rama (P08c llega a 0022 y anade la 0024). En CI la BD se aprovisiona desde cero solo
con las migraciones de la rama: alli el 7.8 sale VERDE y la bateria es 24/24. Es la
"clausula A" del DICTAMEN P08c-CI-01, y el unico rojo tolerado en local.

QUE QUEDA CONSTRUIDO. La FSM de pivote 0-5 (IDLE / IMPULSE / ENCOUNTER / ABSORPTION /
EXHAUSTION / FLIP) en paridad SEMANTICA con v4, como nucleo PURO y determinista, mas un
modelo de CONFIANZA 0-100 por factores ponderados que sustituye la formula simple de v4
(50 + zone_strength/2). pivotphase.phase y pivotphase.confidence estan DECLARADAS en el
catalogo vivo (discovery) Y REGISTRADAS en SOURCE_MATERIALIZERS: el cableado CE-14 esta
completo, no a medias.

FACTORES: ACTIVOS F2 (agotamiento de delta), F4 (esfuerzo vs resultado) y F6 (contexto de
volume profile). DIFERIDOS con gatillo explicito: F1 (absorption.*/candle.open, P08b), F3
(swing.*, P08b), F5 (imbalance) y F7 (notrade, hoy NO consumible en el catalogo).

PUNTOS QUE EL CSA DEBE TENER PRESENTES (no son deuda, son contexto vivo):
- TECHO DE CONFIANZA 60, NO 100. Con 3 de 7 factores activos, la confianza maxima
  alcanzable es 60. Es una limitacion DECLARADA, coherente con DEC-PROVISIONAL-02: un
  factor ausente aporta 0 y NO se renormaliza el denominador (la evidencia ausente no
  infla). Si se lee un 60 como "confianza mediocre" se estara leyendo mal: es el maximo.
- F6 SE CORRIGIO SOBRE LA MARCHA (PIVOT-05). Se habia especificado como vol_ratio y era
  IMPOSIBLE de construir: las fuentes vp.* exponen PRECIOS, no ratios de volumen. Pasa a
  normalizarse por DISTANCIA del precio a vp.hvn/vp.lvn, y formula_version sube a 2.
- impulse_score ES LA VARIANTE 6a, NO EL MOTOR DE v4. Escalado de |delta| por percentil
  de su distribucion reciente (0-100). Es DERIVA FIRMADA respecto a v4, registrada como
  tal, no un olvido.
- EL SNAPSHOT ES AS-OF, NO CONTINUIDAD (PIVOT-09, Estrategia A). El replay bootstrapea la
  FSM DESDE IDLE sobre (history_bars + NORM_WINDOW=100) barras; las primeras NORM_WINDOW
  avanzan la FSM sin emitirse. Como las secuencias de la FSM son BOUNDED (< NORM_WINDOW),
  el bootstrap reconstruye el estado entero, asi que el snapshot vale como auditoria y no
  como cadena de continuidad. Consecuencia aceptada: DOBLE REPLAY (las dos specs replayan
  por separado), tolerado por ser determinista (ADR-007).
- EL PERIFERICO SE DETUVO DOS VECES EN VEZ DE IMPROVISAR, y las dos veces tenia razon:
  (i) la regla de seleccion de vp.hvn/vp.lvn no era aplicable porque compute_volume_nodes
  DESCARTABA el volumen por nodo -> se ratifico la opcion A (helper factorizado
  _volume_node_indices + select_hvn_price/select_lvn_price con fallbacks deterministas
  obligatorios, sin tocar el contrato publico de VolumeNodes); (ii) el glue del replay,
  escrito primero en platform/rules/, violaba la frontera de capas (importaba infra y
  entrypoints) y el check 7.1 lo tumbo -> se movio al composition root
  (entrypoints/worker_rules/pivotphase_materializer.py), con el import de
  SOURCE_MATERIALIZERS diferido dentro de la funcion para no crear ciclo.
- MIGRACION 0024 (pivotphase_snapshot). El numero 0023 lo ocupa EN PARALELO P08b
  (ema_snapshot): coordinacion 5.34. scope=system, append-only; params_version entra en
  la PK porque un cambio de parametros o de formula produce un replay DISTINTO.

PARA LA PROXIMA REVISION: la secuencia firmada de siguientes pasos es P08c -> MAT-05-Q2,
luego P08b -> swing.*, P08b -> LOTE3, P08b -> LOTE4 y D1 -> LOTE5. Los factores diferidos
F1/F3 se desbloquean con P08b (absorption.*/candle.open y swing.*), asi que el techo de
confianza sube conforme P08b entregue sus fuentes: activarlos es anadir peso + funcion +
input, NO reestructurar el modelo.

=====================================================================
REVISION CSA - PIEZA P08c (hito M3) - 2026-08-04
=====================================================================
Veredicto: CONFORME (Central y CSA). Firmado por Alvaro 2026-08-04.
P08c ENTREGADA (5/7 de M3). NO cierra M3: faltan P08b y P09a.
9 merges en main: 19bed2b, 6c3b63b, ca4d5f4, 9025588, 89f8534, 6905aa7, f0a728b,
1a8c2ab, 06d739f. 1527 passed, 1 skip autorizado (phase3_zone_break). Cero deuda.

RESUMEN DE LA PIEZA: catalogo VIVO de DataSources footprint/L2-derived con materializacion
CE-14 completa. Discovery explicito (declarations() por modulo, discover_declarations).
Dispatch por SOURCE_ID (Protocol SourceMaterializer). 9 fuentes en el catalogo (market.close
POINT_LOCAL, orderflow.delta POINT_LOCAL, orderflow.delta_momentum WINDOWED/DAG-2,
footprint.price_range POINT_LOCAL, vp.poc/vah/val WINDOWED, vp.hvn/lvn WINDOWED, cvd.value
INTEGRATOR, pivotphase.phase RECURSIVE, pivotphase.confidence RECURSIVE). Pivotphase: FSM
0-5 paridad v4 + confianza F2/F4/F6 (techo 60) + replay bootstrap-desde-IDLE. MAT-05 Q2:
propagacion selectiva (overridable_params) + fail-loud para params no consumidos.

ARCO DE CONSTRUCCION (por los cinco puntos solicitados en el dossier):
1. ARCO COMPLETO: CONFORME. Progresion tecnicamente coherente (fuentes -> pre-registros ->
   infra CE-14 -> DAG 2o nivel -> FSM -> modelo estadistico -> replay/cableado -> params).
   No se observan inversiones peligrosas del orden de construccion.
2. DIFERIDOS: CONFORME. Todos con propietario, pieza destino, condicion de activacion. No
   afectan al contrato vigente ni alteran el comportamiento de v5.0. Sin huerfanos.
3. SKIP DOCUMENTADO: CONFORME CON OBSERVACION DOCUMENTAL. phase3_zone_break tiene dueno,
   explicacion, pieza desbloqueadora (P08b), no es regresion. La observacion: el informe
   debe decir "1 skip autorizado", no "cero skips" (5.18). Incorporado al cierre.
4. MAT-05 Q2: CONFORME. La combinacion overridable_params + propagacion explicita +
   fail-loud para params no consumidos evita exactamente "params que parecen propagarse pero
   nadie usa". Respeta la filosofia fail-closed del proyecto.
5. OBJECIONES: ninguna objecion arquitectonica demostrable. No se observa reapertura de ADR.

RECOMENDACIONES (no bloqueantes, para piezas futuras):
1. Mantener una tabla viva de factores F1-F7 (activos/diferidos/peso 0).
2. Versionar con formula_version si F1/F3/F5 alteran la semantica de confidence.
3. Documentar params congelados vs calibrables al llegar la calibracion.

VERIFICADO EN ESTA REVISION: que el arco completo es coherente y aditivo; que no se
reabre ningun ADR; que los diferidos tienen dueno y gatillo; que el skip esta documentado
y gateado; que MAT-05 Q2 respeta fail-closed; que el techo de confianza 60 es limitacion
declarada y no defecto; que F6 se corrigio sobre la marcha (PIVOT-05, formula_version=2);
que impulse_score es variante 6a (deriva firmada, no olvido); que el replay es
determinista (ADR-007); que la separacion FSM/confianza es limpia.

PARA LA PROXIMA REVISION: P08b (DataSources candle-derived) se reactiva. Construye
swing.* (desbloquea F3 de pivotphase), luego LOTE 3 (EMA/RSI/MACD, con la limitacion
de multi-instancia param gateada a cache_key-valor), luego LOTE 4 (fib), luego D1 y
LOTE 5. P09a (router de notificaciones backend) cierra M3. Para P08b el CSA debe vigilar:
que los factores diferidos F1/F3 se activan SIN reestructurar el modelo de confianza (solo
anadir peso+funcion+input); que el techo sube correctamente; y que formula_version se
incrementa si la semantica cambia.

## CIERRE: PIEZA P08b (DataSources candle-derived) - 2026-08-06

Veredicto: CONFORME (Central y CSA). Firmado por Alvaro 2026-08-06.
P08b ENTREGADA (6/8 de M3). NO cierra M3: faltan P09a y P10.
PR #6, HEAD 0bc376d, 16 commits (c8040ce a 0bc376d). Actions VERDE 3/3 (run 31078282783). 1903 passed,
1 skip preexistente ajeno (P08c pivotphase). Cero deuda.

RESUMEN: catalogo VIVO de 22 fuentes candle-derived servibles + 1 NON_SERVIBLE (fib.levels). LOTES 1-5
completos. D1 (carrier ScalarValue, firma unica) resuelto: habilita fuentes STRING/BOOLEAN sin alterar
semantica Decimal. 5 snapshots recursivos (0023/0025/0026/0027/0028) con GATE bit-exacto ADR-007.
Evaluador extendido con EQ/NE STRING/BOOLEAN (aditivo, sin operador nuevo). Bloque 3 fail-loud para
tipos incompatibles.

DICTAMENES: SWING-01 (forma servible + W=100 + fallback), LOTE3-01 (EMA 20 / RSI 14 / MACD 12-26-9,
snapshots por columnas), FIB-01 (RECURSIVE histeresis 0.414, resuelve DEC-FIB-RANGO-DIFERIDO), D1-01 a
D1-05 (carrier ScalarValue, fib.levels NON_SERVIBLE, divergence RECURSIVE). 8 elevaciones, todas
dictaminadas sin desviacion.

DETECTORES DESBLOQUEADOS: P08b entrego swing.* + candle.open que desbloquean absorption.*/climax.*/
void.*/notrade.* y F1/F3 de pivotphase. Colocados en P10 (pieza nueva, NO extension de P08c).

RECOMENDACIONES CSA (3, no bloqueantes, para P10):
  1. formula_version al activar F1/F3/F5/F7.
  2. Tabla viva F1-F7 (disponible/construido/activo/peso).
  3. Documentar patron ScalarValue como contrato general.

PARA LA PROXIMA REVISION: P10 (detectores footprint + pivotphase completion) construye
absorption.*/climax.*/void.*/notrade.*, activa F1/F3 en pivotphase.confidence, resuelve skip
PHASE3_ZONE_BREAK, sube el techo de confianza. P09a (router de notificaciones backend) cierra M3 junto
con P10. El CSA debe vigilar: que F1/F3 se activan SIN reestructurar el modelo (solo peso+funcion+input);
que formula_version se incrementa; que el techo nuevo se documenta; que el patron ScalarValue no se
reimplemente en paralelo.
