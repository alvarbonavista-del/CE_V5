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
