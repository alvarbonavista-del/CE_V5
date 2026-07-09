# ESTADO DE CONSTRUCCION - Crypto Engine V5

Archivo vivo de estado de proceso (sin logica). Lo mantiene Claude Code
en disco; Alvaro lo resube al knowledge cada vez que se cierra una pieza
o un hito (DOC_ENTREGABLES sec.8).

Ultima actualizacion: 2026-07-09 (cierre de pieza P01).

## Hito actual
M1 - Un evento viaja de punta a punta con envelope, idempotencia y Clock
     sobre el bus externo, con outbox transaccional; reinicio sin perdida:
     EN CURSO. Piezas: P01, P02, P02b, P03.

## Pieza actual
P01 - Contratos base y envelope: ENTREGADA.
  Commit: 17bb584 (17bb58490bb2091b8469b30503bab5c03915b7cf).
  Envelope canonico y familias como fuente Pydantic v2; cadena
  source -> JSON Schema -> TS reproducible; contracts/VERSIONING.md;
  checks 7.3/7.4/7.7 activos y verdes en local.
  Doble revision Central + CSA conforme; firmado por Alvaro.
  CI: checks equivalentes al workflow validados en local; Actions
      pendiente por ausencia de remoto.

## Proxima pieza
P02 - Modelo temporal y Clock (ADR-007): dara semantica a las ranuras de
  tiempo que P01 dejo como campos del envelope (event_time,
  ingestion_time, processing_time, time_anchor_ref).

## Piezas cerradas
- P00 - Esqueleto de repositorio + CI base: ENTREGADA (hito M0 CERRADO).
  Commits: d3f7ad6 -> 15f936d.
- P01 - Contratos base y envelope: ENTREGADA. Commit 17bb584.

## Regla de trabajo (REGISTRO_DECISIONES sec.1)
Construccion en micro-pasos: el periferico nunca entrega la pieza entera
de golpe. Un paso, se explica, Alvaro ejecuta y pega salida, siguiente.

## Notas
- Guardarrailes vivos desde el commit 0. Sin deuda, sin codigo muerto,
  sin placeholders.
- Windows local requiere PYTHONUTF8=1 y PYTHONIOENCODING=utf-8.
- Checks activos tras P01: 7.1, 7.2, 7.3, 7.4, 7.7 (+ lint/format/type y
  biome/tsc/depcruise). Inactivos hasta existir su objeto: 7.5/7.6 (P04),
  7.8 (primera tabla tenant/user), 7.9 (primer Componente).
