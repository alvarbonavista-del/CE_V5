# ESTADO DE CONSTRUCCION - Crypto Engine V5

Archivo vivo de estado de proceso (sin logica). Lo mantiene Claude Code
en disco; Alvaro lo resube al knowledge cada vez que se cierra un hito
(DOC_ENTREGABLES sec.8).

Ultima actualizacion: 2026-07-08 (cierre de M0).

## Hito actual
M0 - Base estructural: CERRADO (doble revision Central + CSA conforme;
     firmado por Alvaro).

## Pieza actual
P00 - Esqueleto de repositorio + CI base: ENTREGADA.
  Commits: d3f7ad6 (esqueleto) -> 15f936d (snapshot documentos-norte).
  CI: checks equivalentes al workflow validados en local; Actions
      pendiente por ausencia de remoto.

## Proxima pieza
P01 - Contratos base y envelope: envelope canonico y familias de evento
  como fuente Pydantic (ADR-003, ADR-004, ADR-005, ADR-006). Incluye las
  tareas de entrada heredadas de P00 (REGISTRO_DECISIONES sec.3):
  gen_schemas, gen_ts_types, contracts/VERSIONING.md.

## Regla de trabajo (REGISTRO_DECISIONES sec.1)
Construccion en micro-pasos: el periferico nunca entrega la pieza entera
de golpe. Un paso, se explica, Alvaro ejecuta y pega salida, siguiente.

## Notas
- Guardarrailes vivos desde el commit 0. Sin deuda, sin codigo muerto,
  sin placeholders.
- Windows local requiere PYTHONUTF8=1 y PYTHONIOENCODING=utf-8.
