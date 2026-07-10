# CONTEXTO PARA EL CSA (ChatGPT) - CONSTRUCCION Crypto Engine V5

Proposito: dar al CSA (revisor consultivo, ChatGPT) el contexto minimo y
estable para revisar las piezas. El CSA revisa coherencia y calidad
contra los documentos-norte; NO decide (firma Alvaro). Archivo vivo
mantenido por Claude Code.

Ultima actualizacion: 2026-07-08 (cierre de M0).

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
