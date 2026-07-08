# REGISTRO DE DECISIONES DE CONSTRUCCION - Crypto Engine V5

Archivo vivo (sin logica). Registra decisiones de proceso y cambios
aprobados durante la construccion, con motivo escrito (DOC_ENTREGABLES
sec.9). Mantenido por Claude Code; Alvaro lo resube al knowledge al
cerrar cada hito.

Creado: 2026-07-08 (cierre de M0).

=====================================================================
1. REGLA DURA DE CONSTRUCCION PASO A PASO (aplica a TODAS las piezas)
=====================================================================
El Claude periferico NUNCA entrega la pieza completa de golpe (ni como
paquete, ni como tanda unica gigante). Se construye en MICRO-PASOS:
  1. El periferico da UN micro-paso (un fichero, un comando, una idea).
  2. Lo explica a nivel principiante (Alvaro no programa).
  3. Alvaro lo ejecuta (en su maquina o via Claude Code) y PEGA la
     salida real.
  4. Solo tras ver la salida, el periferico da el siguiente micro-paso.
Motivo: Alvaro es decisor y relay, no programa; el ritmo paso a paso
evita errores en cascada, mantiene el control en Alvaro y hace observable
cada avance. OBLIGATORIA de P01 en adelante. Un periferico puede afinar
el tamano del micro-paso, nunca saltarselo ni entregar la pieza entera
de una vez. Persistencia en disco via Claude Code, no pegando ficheros a
mano en PowerShell.

=====================================================================
2. DIFERIDOS DE P00 (se materializan en su pieza, no en P00)
=====================================================================
En P00 no se crearon ficheros cuyo OBJETO aun no existe (crearlos vacios
seria placeholder, prohibido):
- contracts/VERSIONING.md ..... P01 (reglas de evolucion, ADR-005).
- tools/gen_schemas.py ........ P01 (Pydantic source -> JSON Schema).
- tools/gen_ts_types .......... P01 (JSON Schema -> tipos TS).
- tools/check_manifests ....... P04 (validacion de manifests, 7.5).
- tools/check_orphans ......... P04 (huerfanos de componentes, 7.6).
En P00, 7.1 lo corre import-linter (contrato en pyproject) y 7.2
dependency-cruiser; 7.4 lo corre tools/check_generated.py; el type-check
del frontend es un gate de madurez (tools/check_types_frontend.mjs).

=====================================================================
3. TAREAS DE ENTRADA HEREDADAS (diferidos como tareas de la pieza)
=====================================================================
P01 (Contratos base y envelope):
  [ ] tools/gen_schemas.py (source Pydantic -> contracts/schemas).
  [ ] tools/gen_ts_types (schemas -> frontend generated).
  [ ] contracts/VERSIONING.md (reglas de evolucion dual, ADR-005).
  [ ] Activar checks 7.3 y 7.7 (contratos y compatibilidad de schema).
P04 (Raiz Componente, manifest, discovery, lifecycle):
  [ ] tools/check_manifests (validacion de manifests, check 7.5).
  [ ] tools/check_orphans (huerfanos, check 7.6).

=====================================================================
4. ENTORNO Y ARREGLOS ACOTADOS REGISTRADOS EN M0
=====================================================================
- Windows local: import-linter (via rich) exige consola UTF-8; fijados
  PYTHONUTF8=1 y PYTHONIOENCODING=utf-8 persistentes para el usuario. Fin
  de linea del repo forzado a LF via .gitattributes (* text=auto eol=lf).
- Arreglos acotados en la validacion de P00 (sin deuda, sin tocar logica):
  quitar BOM de 19 ficheros (Set-Content de PowerShell), ruff format en 2,
  normalizar biome.json a LF.
- Soporte anadido en P00 (no arquitectura): pnpm-workspace.yaml,
  .python-version, .gitattributes, tools/check_types_frontend.mjs.
Ninguna de estas decisiones reabre un ADR.
