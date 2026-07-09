# REGISTRO DE DECISIONES DE CONSTRUCCION - Crypto Engine V5
Archivo vivo (sin logica). Registra decisiones de proceso y cambios
aprobados durante la construccion, con motivo escrito (DOC_ENTREGABLES
sec.9). Mantenido por Claude Code; Alvaro lo resube al knowledge al
cerrar cada pieza o hito. Append-only: no se borra historial.
Creado: 2026-07-08 (cierre de M0).
Actualizado: 2026-07-09 (cierre de pieza P01).
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
=====================================================================
5. REGLAS DE PROCESO ANADIDAS DURANTE M1 (dictadas por Alvaro)
=====================================================================
Complementan (no sustituyen) a DOC_ENTREGABLES. Se registran aqui para
que no vivan solo en el chat (anti-deriva, DOC_ENTREGABLES sec.9).
5.1 Agrupacion de micro-pasos: por velocidad se permite agrupar varios
    micro-pasos afines en UNA sola tanda [CLAUDE CODE] cuando son
    escritura en disco, explicando antes a Alvaro que hace el grupo y sin
    mezclar validacion en caliente intermedia. Refina la regla 1, no la
    anula.
5.2 Prioridad [CLAUDE CODE] sobre [POWERSHELL]: escribir/editar ficheros
    siempre por Claude Code (mas rapido; evita el BOM de Set-Content).
    PowerShell solo para instalar dependencias, ejecutar checks/tests,
    commit y ver la salida real.
5.3 Un periferico por pieza (no por hito): arranca leyendo el estado en
    disco/knowledge, no la memoria de un chat anterior. Excepcion a
    criterio de Central: dos piezas muy pequenas y muy acopladas.
5.4 Central define QUE debe contener cada tanda; el periferico la REDACTA.
5.5 Procedimiento estandar de cierre de pieza: (a) el periferico produce
    informe de entrega; (b) Central prepara el dossier para el CSA; (c) el
    CSA dictamina; (d) Alvaro firma; (e) el periferico monta la tanda de
    cierre que actualiza los CUATRO archivos de contexto
    (ESTADO_CONSTRUCCION_V5, REGISTRO_HITOS_V5,
    REGISTRO_DECISIONES_CONSTRUCCION, CHATGPT_CSA_CONTEXTO_CONSTRUCCION)
    mas commit + barrido limpio + hash; (f) Alvaro resube los cuatro al
    knowledge.
5.6 Formula exacta de CI mientras no haya remoto: "checks equivalentes al
    workflow validados en local; Actions pendiente por ausencia de
    remoto". Nunca "Actions verde".
5.7 Una pieza no es ENTREGADA hasta commit + git status limpio + barrido
    limpio posterior + hash registrado.
5.8 Doble revision por pieza (Central + CSA) antes de la firma de Alvaro,
    ademas de la consolidacion en el cierre de hito.
=====================================================================
6. CIERRE DE PIEZA P01 - CONTRATOS BASE Y ENVELOPE
=====================================================================
Estado: ENTREGADA. Commit: 17bb584
(17bb58490bb2091b8469b30503bab5c03915b7cf).
Doble revision Central + CSA conforme; firmado por Alvaro.
CI: checks equivalentes al workflow validados en local; Actions pendiente
por ausencia de remoto.
Condicion operacional del CSA cumplida: commit + git status limpio +
barrido limpio posterior + hash registrado. Tras este commit, el check
7.7 tiene baseline real en git.
Tareas de entrada de P01 (seccion 3), todas cerradas:
  [x] tools/gen_schemas.py (source Pydantic -> contracts/schemas).
  [x] tools/gen_ts_types.mjs (schemas -> frontend generated).
  [x] contracts/VERSIONING.md (reglas de evolucion dual, ADR-005).
  [x] Activar checks 7.3 y 7.7 (contratos y compatibilidad de schema).
Decisiones de construccion de P01 (dentro de area; ninguna reabre un ADR):
- D1. JSON Schema con el exportador NATIVO de Pydantic v2
  (model_json_schema): cero dependencia extra (ADR-006).
- D2. Tipos TS con json-schema-to-typescript 15.0.4: opera sobre el
  artefacto JSON Schema y respeta el flujo de tres zonas (DOC_ESTRUCTURA
  2.5), frente a pydantic-to-typescript que saltaria el schema intermedio.
  Versiones verificadas con web_search; pydantic==2.13.4.
- D3. contracts/source como raiz importable (pytest pythonpath, mypy_path,
  ruff src); paquetes envelope/ y families/ directos, sin capa intermedia,
  para no alterar el arbol de DOC_ESTRUCTURA sec.3. Plugin pydantic.mypy.
- D4. tools/check_generated.py (7.4) AMPLIADO a regenerar-y-comparar, como
  su propio comentario de P00 anticipaba; 7.3 y 7.4 quedan como una misma
  comparacion por zona (schemas y TS).
- D5. Se emite family.schema.json (+ family.ts) ademas del envelope, para
  exponer al frontend el conjunto cerrado de familias; artefacto aditivo,
  coherente con ADR-004/006.
- D6. biome excluye la carpeta generada; tsconfig la incluye a proposito
  (tsc valida que los tipos generados compilan).
Validacion en caliente (recomendada, superada): demostrado que el check
7.3/7.4 muerde (edicion manual -> FALLA; regenerar -> OK) en las dos
zonas. La deteccion del 7.7 esta probada por sus tests.
Guardarrailes activos tras P01: 7.1, 7.2, 7.3, 7.4, 7.7, lint/format/type
(backend) y biome/tsc/depcruise (frontend), todos verdes en local.
Inactivos por no existir aun su objeto: 7.5, 7.6 (P04), 7.8 (primera tabla
tenant/user), 7.9 (primer Componente).
