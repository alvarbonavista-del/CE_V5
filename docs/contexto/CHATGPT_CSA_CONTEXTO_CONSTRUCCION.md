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
