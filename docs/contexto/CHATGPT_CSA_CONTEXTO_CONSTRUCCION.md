# CONTEXTO PARA EL CSA (ChatGPT) - CONSTRUCCION Crypto Engine V5

Proposito: dar al CSA (revisor consultivo, ChatGPT) el contexto minimo y
estable para revisar las piezas. El CSA revisa coherencia y calidad
contra los documentos-norte; NO decide (firma Alvaro). Archivo vivo
mantenido por Claude Code.

Ultima actualizacion: 2026-07-15 (entrega de pieza P07; abre el hito M3).

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
