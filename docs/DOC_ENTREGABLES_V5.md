===================================================================
DOC_ENTREGABLES_V5.md
===================================================================
Politica de proceso de construccion de Crypto Engine V5.

Naturaleza: PRESCRIPTIVO / DE PROCESO. Define QUE significa "entregado",
como se valida, como se arreglan fallos, como se gestiona la deuda y como
colaboran los Claude durante la construccion. SUBORDINADO a DOC_ARQ_V5,
DOC_ESTRUCTURA_V5 y DOC_ROADMAP_V5: no reabre arquitectura, no redefine
carpetas ni checks de CI, no reordena piezas.
Autoridad de decision: Alvaro (decisor unico). CSA consultivo.
Estado: APROBADO (CSA consultivo) y FIRMADO por Alvaro (2026-07-06).
Fecha: 2026-07-06.

===================================================================
0. METADATOS Y PREMISAS DE ENTRADA
===================================================================
- Version: 1.0.
- Documento hermano de DOC_ESTRUCTURA_V5 y DOC_ROADMAP_V5.
- Deriva de: DOC_ARQ_V5 (20 ADR), DOC_ESTRUCTURA_V5 (sec.7 guardarrailes),
  DOC_ROADMAP_V5 (piezas/hitos), LECCIONES_V4, DIFICULTADES_Y_REFACTORS_V4
  (sec.12 sistema multi-Claude), METODOLOGIA (formato de tandas).
- Premisas: DOC_ARQ_V5, DOC_ESTRUCTURA_V5 y DOC_ROADMAP_V5 cerrados y
  firmados. Este es el ULTIMO documento de la fase de paso a construccion.
- Autoridad: Alvaro; CSA consultivo.

===================================================================
1. PROPOSITO Y FRONTERAS
===================================================================
Fija la "constitucion de proceso" de la construccion: entrega, validacion,
fixes, deuda y colaboracion multi-Claude. NO redefine los checks de CI
(DOC_ESTRUCTURA sec.7) ni el "hecho cuando" ni el orden de piezas
(DOC_ROADMAP); los ENVUELVE en una politica. Regla de conflicto: en
politica de proceso/entrega manda este documento; en arquitectura manda
DOC_ARQ_V5; en estructura, DOC_ESTRUCTURA_V5; en orden, DOC_ROADMAP_V5.

===================================================================
2. MODELO DE TRABAJO MULTI-CLAUDE EN CONSTRUCCION
===================================================================
Se retoma el patron multi-Claude de v4, PERO corrigiendo sus fallos
operativos conocidos (DIFICULTADES sec.12: desincronizacion, decisiones
redescubiertas, coste de relay, documento maestro tardio).

2.1 CENTRAL DE CONSTRUCCION
Reparte el trabajo por AREA. Para cada area redacta el PROMPT INICIAL del
Claude periferico correspondiente. Mantiene la coherencia global y es el
unico que habla con el decisor (Alvaro) sobre arquitectura y avance.

2.2 PERIFERICOS POR AREA
Cada area es un chat Claude periferico. Su prompt inicial, redactado por
Central, SIEMPRE incluye:
- que AREA cubre y sus limites;
- que ARCHIVOS DEL KNOWLEDGE debe consultar ANTES de responder (lista
  explicita: los ADR relevantes, la seccion de DOC_ESTRUCTURA y las
  piezas de DOC_ROADMAP que le tocan);
- la regla de que NO redescubre convenciones ya cerradas: si duda, las
  consulta en los documentos, no las reinventa (anti-fallo de v4).
El periferico DEFINE los pasos de programacion de su area y los explica
como a quien no sabe programar (ver 2.5 y sec.3).

2.3 CSA DE CONSTRUCCION
Revisor consultivo (no decide). Revisa coherencia y calidad de codigo/
entrega contra los documentos-norte. Igual que en investigacion: firma
Alvaro, no el CSA.

2.4 CLAUDE CODE
Ejecutor de persistencia en disco (codigo y documentos). Recibe tandas
con el formato de METODOLOGIA (un bloque, ASCII-safe, anclajes exactos,
verificacion final).

2.5 ALVARO
Decisor unico. NO programa. Todo se le explica como a quien no sabe
programar: pasos concretos, numerados, sin jerga asumida, uno a uno. Es
el relay entre instancias (como en v4) y quien ejecuta las acciones en su
maquina.

Correccion de los fallos de v4 (DIFICULTADES sec.12): documentos maestros
mantenidos desde el primer dia (no como parche tardio); cada hito cierra
con tanda de contexto (sec.8) para que no haya desincronizacion; ninguna
convencion se redescubre (esta en los documentos-norte).

===================================================================
3. FORMATO DE INSTRUCCIONES (regla dura)
===================================================================
Toda instruccion dirigida a Alvaro DEBE declarar su DESTINO con etiqueta,
porque Alvaro ejecuta en sitios distintos y no debe adivinar donde va cada
cosa:
- [POWERSHELL]        comandos de terminal Windows (arrancar, instalar,
                      ejecutar, ver salida). ASCII-safe (cp1252).
- [CLAUDE CODE]       tandas de persistencia/codigo en disco (crear/editar
                      ficheros), formato METODOLOGIA.
- [CONSOLA NAVEGADOR] cuando se programa o depura el entorno grafico
                      (frontend): comandos/inspeccion en la consola del
                      navegador.
Reglas: pasos NUMERADOS; uno a uno; explicados a nivel principiante; nunca
mezclar destinos en un mismo bloque; si una accion es de Alvaro y no
delegable (p.ej. ejecutar la app), se marca claramente (leccion v4:
python main.py era de Alvaro, nunca de Claude Code).

===================================================================
4. DEFINITION OF DONE (DoD) POR PIEZA
===================================================================
Una pieza (P00-P13 del ROADMAP) esta ENTREGADA solo si cumple TODO:
- checks de CI de su fase en verde (DOC_ESTRUCTURA sec.7);
- "hecho cuando" de la pieza (DOC_ROADMAP sec.3) satisfecho;
- tests presentes y en verde (unit y, si aplica, integration);
- validaciones en caliente criticas superadas (sec.5);
- documentacion minima por carpeta/componente (README con proposito;
  anti-R1);
- CERO deuda tecnica no aprobada (sec.7);
- sin codigo muerto, sin placeholders, sin "disabled por si acaso"
  (anti-R3/R4; DOC_ESTRUCTURA 5.2).
Estados de una pieza: EN CURSO -> EN REVISION -> ENTREGADA. Una pieza en
revision no se da por entregada hasta la doble revision del hito si es
cabecera de hito (sec.8).

4.1 CLASES DE ENTREGA (vocabulario comun)
- ENTREGA DE PIEZA: una pieza Pxx cumple su DoD (sec.4).
- CIERRE DE HITO: conjunto de piezas que demuestra Mx; requiere doble
  revision (sec.8).
- FIX: correccion acotada de una entrega (sec.6).
- CAMBIO ARQUITECTONICO: cualquier modificacion de contrato, frontera,
  familia de evento o ADR; NO se trata como fix, se eleva a Alvaro.

===================================================================
5. VALIDACION EN CALIENTE
===================================================================
Definicion: validar una pieza o paso EN RUNTIME, ejecutando y devolviendo
a Claude la SALIDA REAL del terminal o de la consola (como en v4: se
ejecutaba y se pegaban las lineas del terminal). No es un test automatico
de CI; es comprobacion viva del comportamiento real.

Quien decide:
- Las validaciones manual/caliente declaradas en DOC_ROADMAP_V5 para una
  pieza son OBLIGATORIAS y NINGUN periferico puede rebajarlas ni omitirlas.
- El periferico de area puede PROPONER validaciones adicionales segun
  criticidad (nunca menos que las obligatorias).
- Central VERIFICA que no se ha omitido ninguna validacion obligatoria del
  Roadmap antes de dar una pieza por entregada.
- Rebajar o eliminar una validacion ya marcada como critica requiere
  aprobacion EXPLICITA de Alvaro y registro del motivo (anti-R2; evita el
  fallo de v4 de perifericos reinterpretando convenciones cerradas).

Criticidad (guia): CRITICO (requiere validacion en caliente) todo lo que
toca dinero, credenciales, seguridad, aislamiento entre usuarios o el
arranque del sistema (ej. reinicio de bus P03, fuga cross-tenant P05, kill
switch P06, orden BYOC sandbox y reconciliacion P10b, push real P09b,
chart movil P13). NO CRITICO (basta CI/tests): logica pura,
transformaciones, helpers.

Sobre que entorno: SIEMPRE sandbox antes que real. NUNCA dinero real hasta
que el sandbox este superado. Salvaguardas activas durante la validacion:
fail-closed y kill switch operativos (ADR-012/018). La salida real se pega
a Central/periferico para diagnostico (patron de v4).

===================================================================
6. POLITICA DE FIXES (regla dura)
===================================================================
- Un fix NO reabre arquitectura. Si el arreglo exige tocar un contrato,
  una familia de evento o una frontera de capa, NO es un fix: se ELEVA a
  Alvaro como cambio arquitectonico (protege ADR-005/006; evita R2).
- LIMITE DE DOS: maximo 2 fixes por PROBLEMA y maximo 2 fixes por ARCHIVO
  editable ORDINARIO; el contador salta por lo que se alcance PRIMERO. Si
  haria falta un tercer intento, se detiene el parcheo y se abre
  REELABORACION CONTROLADA, cuyo modo depende del tipo de artefacto:
  * Archivos de implementacion ORDINARIOS: se BORRA y se rehace el archivo
    desde cero.
  * Artefactos HISTORICOS, append-only, FIRMADOS o VERSIONADOS (migraciones
    ya aplicadas, ADRs/documentos firmados, registros, contratos publicos,
    schemas versionados): NUNCA se borran ni reescriben en silencio; se
    crea una migracion/cambio SUCESOR, se aplica expand-and-contract, o se
    eleva a Alvaro segun corresponda (coherente con DOC_ESTRUCTURA:
    incompatibles fallan en CI).
  * Artefactos GENERADOS (contracts/schemas, */generated): NUNCA se
    parchea el generado; se corrige la FUENTE (contracts/source) y se
    regenera.
- Todo fix lleva TEST DE REGRESION que reproduce el fallo y verifica su
  arreglo; no deja codigo muerto ni comentado (anti-R3/R4).
- VALVULA UNICA para el caso ordinario: si "borrar y rehacer" un archivo
  ordinario fuese claramente peor que un tercer fix limpio (archivo grande
  y correcto salvo un detalle), la unica salida valida es la APROBACION
  EXPLICITA de Alvaro, registrada. Sin ella, al tercer intento se rehace.
  Nunca se salta la regla en silencio.

===================================================================
7. DEUDA TECNICA (regla dura)
===================================================================
La deuda tecnica esta PROHIBIDA por norma general. No se entrega algo "a
medias para arreglar luego". Se hace bien o no se entrega.
Unica excepcion: deuda con APROBACION EXPLICITA de Alvaro. En ese caso se
REGISTRA obligatoriamente: que deuda es, por que se acepta, que la paga y
cuando. Deuda sin aprobacion y sin registro no existe: es un defecto que
bloquea la entrega (DoD, sec.4). Esto ataca R3/R4 de raiz: en v4 la deuda
se acumulo por no tener esta regla.

===================================================================
8. CIERRE DE HITO TECNICO (doble revision + tanda de contexto)
===================================================================
Cada hito del ROADMAP (M0-M5) completado se cierra asi:
1. DOBLE REVISION: primero Central revisa el hito completo contra DoD y
   documentos-norte; despues el CSA lo revisa (consultivo). Dos pares de
   ojos independientes.
2. Si ambas revisiones estan conformes y Alvaro firma, se lanza una TANDA
   de Claude Code sobre los ARCHIVOS LOCALES DE CONTEXTO DE CONSTRUCCION
   del proyecto EN DISCO. La tanda ACTUALIZA/SINCRONIZA (no borra
   historiales): estado de construccion, registro de hitos, contexto del
   CSA de construccion y cualquier indice maestro que exista. Si alguno
   de esos archivos aun no existe, el Hito correspondiente lo crea
   explicitamente. Claude Code NO modifica el Knowledge remoto de ChatGPT/
   Claude; Alvaro sube/sincroniza esos archivos al Knowledge despues si
   aplica (mismo patron que en investigacion).
3. Solo tras esa tanda se considera el hito CERRADO y se abre el
   siguiente.

Archivos de contexto de construccion previstos (se crean en M0 si no
existen; nombres orientativos):
- ESTADO_CONSTRUCCION_V5.md
- REGISTRO_HITOS_V5.md
- CHATGPT_CSA_CONTEXTO_CONSTRUCCION.md
- REGISTRO_DECISIONES_CONSTRUCCION.md (cuando aparezca una decision de
  proceso o un cambio aprobado).

===================================================================
9. GESTION DE CAMBIOS Y ANTI-DERIVA (R2)
===================================================================
Todo cambio de rumbo o de opinion queda con su MOTIVO ESCRITO (en el
registro de construccion). Nada importante vive solo en el chat. Si algo
importante quedo solo en chat, es error de proceso y se corrige antes de
avanzar (misma disciplina que en investigacion). Ninguna convencion ya
cerrada se reabre sin motivo registrado.

===================================================================
10. TRAZABILIDAD
===================================================================
DoD (sec.4) .................... DOC_ESTRUCTURA sec.7; DOC_ROADMAP sec.3
clases de entrega (4.1) ........ vocabulario comun Central/perifericos
validacion en caliente (sec.5) . ADR-012/018; ROADMAP "validacion
                                 manual/caliente" por pieza; patron v4
formato de instrucciones (sec.3) DIFICULTADES sec.12 (destino de cada
                                 instruccion; main.py era de Alvaro)
politica de fixes (sec.6) ...... R2/R3/R4; ADR-005/006 (elevar si toca
                                 contrato); DOC_ESTRUCTURA (generados)
deuda prohibida (sec.7) ........ R3/R4; DoD
cierre de hito (sec.8) ......... METODOLOGIA (tandas); patron de
                                 investigacion; anti-desincronizacion v4
modelo multi-Claude (sec.2) .... DIFICULTADES sec.12 (fallos de v4 y sus
                                 correcciones)

FIN DOC_ENTREGABLES_V5 (v1.0, aprobado CSA + firmado Alvaro 2026-07-06).
