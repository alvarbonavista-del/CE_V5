# HANDOFF -- SUB-PIEZA MATERIALIZACION P08c (CE-14)

Estado: construccion completa; pendiente doble revision + merge. Rama wip/p08c,
commits 6c3b63b..HEAD: 11 commits, de 873453f (T1 discovery) a 38d6096 (T5b-2b-ii
cvd INTEGRATOR). 6c3b63b es el tip actual de main (el merge de la sub-pieza F7
anterior), por lo que ese rango son EXACTAMENTE los commits de esta sub-pieza y
equivale a main..HEAD.
Tag: sin tag, se cita por commit (el repo no tiene ningun tag creado).

Commits de la sub-pieza, en orden:

    873453f  T1   discovery explicito del catalogo vivo
    4782f62  T2   materializador WINDOWED (mecanismo puro)
    ba1c2a1  T3   materializador RECURSIVE/INTEGRATOR (replay desde snapshot)
    0399319  T4   validacion cache_key por naturaleza (MAT-03)
    466f349  T5a-1 grant SELECT market_footprint al rol de reglas (0021)
    0da5007  T5a-2 parte 1: read_footprint_window (lector base WINDOWED)
    4d78e5c  T5a-2 parte 2: dispatch por source_id + vp.* + guarda compilador
    e005e76  T5b-1 registro polimorfico + orderflow.delta POINT_LOCAL
    78376ce  T5b-2a tabla cvd_snapshot + grant INSERT/SELECT + check 5.20
    f49c948  T5b-2b-i store de snapshot de cvd + lector de rango de bar_delta
    38d6096  T5b-2b-ii cvd.value INTEGRATOR + GATE bit-exacto ADR-007

## 1. QUE DESBLOQUEA (para P08b)
La capa de materializacion convierte una fuente DECLARADA (ADR-008) en su SERIE
tuple[Decimal, ...] oldest->newest que consume el evaluador. Con esto P08b puede
declarar y consumir sus fuentes candle-derived siguiendo el MISMO patron, sin
reabrir el mecanismo.

## 2. CONVENCION DE DISCOVERY (D1)
Cada modulo productor expone una funcion EXPLICITA que retorna sus declaraciones,
con esta firma EXACTA (identica en los cinco modulos productores: rawclose.py,
rawfootprint.py, volume_profile.py, orderflow.py, cvd.py):

    def declarations() -> tuple[DataSourceDeclaration, ...]:

El agregador vive en platform/rules/discovery.py con la misma forma de retorno:

    def discover_declarations() -> tuple[DataSourceDeclaration, ...]:

y compone una LISTA EXPLICITA de modulos productores (desempaquetando el
declarations() de cada uno). build_catalog COLECTA discover_declarations(),
registra y valida el DAG (completo + aciclico) ANTES de compilar. No hay
descubrimiento implicito por nombre de fichero.

## 3. DISPATCH DE MATERIALIZACION (MAT-06)
El binding source_id -> materializador vive en el composition root del worker
(entrypoints/worker_rules/materializers.py), NO en la declaracion (dato puro
ADR-008) ni en platform. El dispatch en _series_for es por SOURCE_ID, no por
memory_model (que es solo metadata): market.close se lee directo; el resto pasa por
el registro SOURCE_MATERIALIZERS; una fuente servible SIN materializador ->
UnwiredSourceError (no se sirve una serie por defecto).

## 4. API DE CADA MATERIALIZADOR
Todos implementan el Protocol SourceMaterializer:
  materialize(session, exchange, symbol, timeframe, open_time, history_bars)
    -> tuple[Decimal, ...]   (oldest->newest; menos valores o () si falta historia,
    que el evaluador trata como NOT_EVALUABLE; nunca inventa barras).
- POINT_LOCAL (market.close): read_close_window (la lectura ES la serie).
- POINT_LOCAL sobre footprint (orderflow.delta): FootprintPointLocalSpec(extract);
  lee read_footprint_window y proyecta el valor de cada barra (bar_delta).
- WINDOWED sobre footprint (vp.poc/vah/val): FootprintWindowedSpec(transform,
  window_bars=100); lee history_bars + window_bars - 1 footprints y aplica la
  funcion pura sobre cada ventana rodante (compute_volume_profile).
- INTEGRATOR (cvd.value): CvdIntegratorSpec; siembra materialize_recursive con el
  snapshot vigente ANTERIOR a la ventana (read_cvd_snapshot_before) y acumula los
  deltas posteriores (read_footprint_delta_range); sin ancla, bootstrap desde el
  inicio de ventana. PERSISTE el snapshot de la barra vigente (write_cvd_snapshot):
  materializador CON ESTADO, idempotente (ON CONFLICT DO NOTHING).

## 5. ESTADO POR memory_model
- POINT_LOCAL: cableado (market.close, orderflow.delta).
- WINDOWED: cableado (vp.poc/vah/val). orderflow.delta_momentum (WINDOWED sobre
  delta, DAG de 2o nivel) SIN cablear: fallo ruidoso hasta su tanda.
- INTEGRATOR: cableado (cvd.value, rolling). session_utc diferido.
- RECURSIVE general: nucleo puro listo (materialize_recursive); sin fuente viva aun.
Correccion de WINDOWED/RECURSIVE/INTEGRATOR: NO-CONFORME en v5.0 (solo materializa-
cion hacia delante); no reabre el enum MemoryModel.

## 6. cache_key POR FUENTE (dimensiones; sin campo nuevo del contrato, MAT-03)
- market.close:      (exchange, symbol, timeframe)                    [pin spot].
- market.footprint:  (exchange, symbol, market_type, timeframe).
- orderflow.delta / delta_momentum: (exchange, symbol, market_type, timeframe).
- vp.poc/vah/val:    (exchange, symbol, market_type, timeframe, bin_count).
- cvd.value:         (exchange, symbol, market_type, timeframe, reset_policy).
Ejemplo por memory_model (para que P08b declare sin ambiguedad):
- POINT_LOCAL: cvd/vp NO; el param que distingue el hecho entra como dim (ninguno en
  orderflow.delta; bin_count en vp; reset_policy en cvd).
- WINDOWED: el param de la funcion pura entra en la clave (bin_count). La VENTANA
  (window_bars=100) NO es dim: es constante fija de materializacion (MAT-05 Q3).
- INTEGRATOR: reset_policy entra en la clave (rolling vs session_utc son HECHOS
  distintos). El ancla del snapshot NO es dim (es estado, no identidad).
Regla del validador (MAT-03): dims de flujo obligatorias (exchange, symbol,
timeframe) + todo param declarado presente en la clave. formula_version/as_of se
ACEPTAN como opcionales; no se exigen.

## 7. GUARDA TEMPORAL DEL COMPILADOR (MAT-05 Q4)
v5.0 NO propaga overrides de parametro de fuente: el compilador RECHAZA
(CompilationError -> cuarentena) una regla cuyo termino lleve ref.params no vacio.
Protege el invariante cache_key = default = constante. Es TEMPORAL: se RETIRA cuando
el compilador propague params al plan (opcion 1, con su propio dictamen).

## 8. ESTADO PROPIO DEL MOTOR: cvd_snapshot (MAT-07)
Tabla cvd_snapshot (migracion 0022, scope=system, sin tenant_id). El rol ce_v5_rules
ESCRIBE y LEE su propio estado de replay (categoria RULES_STATE_TABLES en
check_rules_access): SELECT+INSERT, sin UPDATE/DELETE/TRUNCATE (append-only). Un
snapshot por (flujo, reset_policy, open_time). GATE ADR-007 verificado: replay desde
CUALQUIER snapshot valido reproduce la cola identica bit a bit.

## 9. ADITIVIDAD (D7)
Confirmada: market.close y las reglas existentes NO cambian de comportamiento.
Bateria ci_local 24/24 verde en cada tanda. Los unicos tests que cambiaron de forma
fueron consecuencias AUTORIZADAS por dictamen (fuente-ejemplo del fallo ruidoso al
cablear orderflow.delta y luego cvd.value; fixture 5.20 al abrir la categoria de
estado).

## 10. INVARIANTES DE CONSUMIDOR (D6, requerimiento P08b-R1)
PENDIENTE DE APORTE EXTERNO -- NO RESOLUBLE EN DISCO. El requerimiento P08b-R1 NO
existe en este repositorio: la cadena "P08b-R1" no aparece en ningun fichero
(.md/.py/.txt), y las menciones de P08b en docs/ (DOC_ROADMAP_V5.md y los cuatro
ficheros de contexto) no enuncian ningun R1/R2/R3. Por tanto el CRUCE uno a uno
contra R1-R3 no se ha podido hacer y queda como accion para Alvaro/Central: aportar
el texto EXACTO de R1-R3 para confirmarlos o marcar el hueco.

Garantias OFRECIDAS por esta sub-pieza (lo que el consumidor puede dar por cierto):
- Determinismo: misma entrada -> misma serie, bit a bit.
- Reproducibilidad del INTEGRATOR desde snapshot (ADR-007), verificada con el GATE
  de dos anclas distintas (seccion 8).
- NOT_EVALUABLE por historia corta: se devuelven menos valores o (), nunca se
  inventa una barra.
- Dispatch por convencion (source_id) + fallo ruidoso (UnwiredSourceError) ante una
  fuente servible sin materializador: nunca una serie por defecto.

## 11. PENDIENTE / DIFERIDO
- orderflow.delta_momentum (WINDOWED sobre delta): su tanda cablea el DAG de 2o
  nivel (materializar una fuente que consume otra derivada).
- session_utc de cvd (tras propagacion de params).
- Retirada de la guarda del compilador (opcion 1).
- Correccion de fuentes no-point-local (mejora coordinada posterior).
- Cruce de las garantias de la seccion 10 contra R1-R3 de P08b-R1 (texto no
  disponible en el repo).

## 12. AVISO PARA ALVARO
Tras merge a main con Actions verde y cierre de contexto, DISPARAR la vuelta de P08b
(requerimiento P08b-R1): sus fuentes candle-derived ya tienen el mecanismo de
materializacion que necesitaban.
