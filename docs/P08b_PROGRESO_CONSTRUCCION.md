# P08b -- DATASOURCES CANDLE-DERIVED -- PROGRESO DE CONSTRUCCION (documento de trabajo)

NATURALEZA: registro de TRABAJO EN CURSO de P08b. NO es fuente de verdad
autoritativa. El registro AUTORITATIVO al cierre es
REGISTRO_DECISIONES_CONSTRUCCION; este documento se consolida alli al cerrar la
pieza. Existe para que los dictamenes y el estado no vivan solo en el chat
(leccion P04/P06b). Mantenido por Claude Code en el worktree wip/p08b; Alvaro lo
resube al knowledge.

ESTADO: P08b EN CURSO (M3 ampliado, EXP-M3-01). Worktree AISLADO wip/p08b
(reglas 5.34/5.35). La mayor parte esta gateada o pausada; ver secciones 2 y 4.

## 0. IDENTIDAD DE LA PIEZA
Catalogo de DataSources que SOLO necesitan VELAS (ficha DOC_ROADMAP A-1.4).
Paralela a P08/P07b/P07c/P08c. Consume market.candle_closed (invariante de P07 y
DEC-PROVISIONAL-02: SOLO dato cerrado, jamas provisional ni intrabar). NO abre
superficie externa; barrido 5.15 minimo (cero llamadas a API en vivo, T-04).
Presupuesto de complejidad de P08 (5/3/5/3): VINCULANTE.

## 1. DICTAMENES DE CENTRAL (P08b-01..05)
P08b-01 (referente): nucleo = referente Wilder DETERMINISTA e INDEPENDIENTE
  (Opcion A). TradingView = cotejo OPCIONAL gateado por Q-T04-3, fuera del DoD
  nuclear, NO bloquea. La lectura operativa de T-04 GOBIERNA sobre la ficha A-1.4.
P08b-02 (fundadas vs a ciegas):
  - FUNDADAS por primaria (construir): RSI = Wilder/RMA, semilla = SMA de N
    (TradingView + Wikipedia). SMA = media simple real (NUNCA el "SMA" de
    KLineChart, que es un EMA).
  - A CIEGAS (pausado): semilla del EMA (I-01 la deduce de un espejo, NO
    VERIFICADO). MACD hereda la semilla del EMA. Estructura EMA/MACD SI fundada
    (alpha=2/(N+1); MACD 12/26/9, histograma x1).
  - warm-up = PARAMETRO calibrado sobre fixtures, no constante fundada.
P08b-03 (metodo paralelo, reglas 5.34/5.35): worktree por pieza; propiedad de
  superficie compartida (un dueno, los demas consumen, el cambio compartido se
  ELEVA); integracion por TURNO/testigo (bateria+push+merge serializados);
  migraciones numeradas en el push + rebase/renumera; rebase/orden de merge
  (primero-verde mergea, la otra rebasa). Cabecera de identidad + worktree en
  cada tanda [CLAUDE CODE].
P08b-04 (hallazgos del molde):
  - P1 WINDOWED: MemoryModel no lo tiene. Lo anade P08c (dueno); P08b lo CONSUME
    en Fase 2 (swing/divergence).
  - P2 SMA subsumido por average: NO se construye sma.*; "SMA(N)" =
    average(market.close, N); entregable = un test. Resuelve la tension SI/NO de
    la ficha a favor del NO-list (anti codigo muerto).
  - P3 catalogo por BUNDLE por pieza: p08b_declarations() es de P08b; el enganche
    en build_catalog lo OWNS P08c. P08b NO edita build_catalog.
  - P4 recursivas (RSI): la materializacion vive en composition.py, NO en el
    evaluador puro. RSI = computo FORWARD desde reset fijo (Wilder olvida =>
    bit-a-bit) en un materializer recursivo registrado en la capa de
    materializacion que OWNS P08c. NO se construye snapshot+replay en v5.0
    (diferido; revisa la asignacion de P08b-03 Capa 2-i); contingencia: re-elevar
    si aparece una razon REAL para snapshots en v5.0. CE-14 intacto (aditivo,
    evaluador sin tocar).
P08b-05 (hito + decisiones): RSI puro reconocido como hito. Persistencia de este
  doc AUTORIZADA. Semilla del EMA -> decision de Alvaro (via a empirica / b
  periferico). Gate de integracion del RSI confirmado.

## 2. ESTADO POR FUENTE
RSI ............... HECHO (computo puro). Ver seccion 3. Integracion GATED a P08c.
SMA ............... CERRADO (= average(market.close,N); test, no fuente propia).
EMA ............... PAUSADO (semilla del EMA sin fundar).
MACD .............. PAUSADO (hereda la semilla del EMA).
candle/volume/vwap/fib ... PENDIENTES (Fase 1; vwap/fib con ancla por resolver,
  observacion 4.2 no dictaminada).
swing.* ........... FASE 2. Primitiva geometrica N=R (DA-I03-1/4); necesita
  WINDOWED (P08c). SIN confianza (geometrico; la confianza vive en pivotphase,
  P08c).
divergence.* ...... FASE 2. precio-vs-RSI; el warm-up del RSI la gatea; usa swing.*.

## 3. RSI -- LO CONSTRUIDO (origin/wip/p08b)
Commits: 6da498d (feat: RSI puro + referente exacto + verificacion SMA=average),
  b669907 (test: refuerzo). ci_local COMPLETA VERDE 24/24, cero skips, en ambos.
Artefactos:
  backend/src/ce_v5/platform/rules/indicators/rsi.py
    - wilder_rsi(closes, period) -> tuple[Decimal|None,...]. Forward desde reset,
      semilla SMA de N, bordes avg_loss==0 -> 100 y avg_gain==0 -> 0 (plano cae en
      100 por convencion documentada), warm-up=None. Contexto Decimal PINNEADO
      (prec 34, ROUND_HALF_EVEN) para bit-a-bit.
    - RSI_FORMULA_VERSION = 1 (sube ante cualquier cambio de formula/semilla/
      contexto).
  tests/unit/platform/rules/test_rsi.py (6): referente exacto, warm-up,
    saturacion, reproducibilidad, historia insuficiente.
  tests/unit/platform/rules/test_sma_is_average.py (2): average = SMA real.
  tests/unit/platform/rules/test_rsi_extended.py (11): periodos 1/2/7/14/21, serie
    larga determinista, bordes, independencia del contexto ambiente, candado
    GOLDEN (ata la salida exacta a RSI_FORMULA_VERSION).
Referente: aritmetica racional EXACTA (fractions.Fraction), camino de codigo
  distinto del de produccion (Decimal). Es la Opcion A del nucleo (P08b-01).

## 4. GATES Y DUENOS (lo que hereda cada gate)
INTEGRACION RSI -> espera el MERGE a main de la CAPA DE MATERIALIZACION (P08c).
  Al mergear: P08b registra un materializer RECURSIVE que llama a wilder_rsi
  (forward desde reset), declara la fuente rsi en p08b_declarations() y REBASA
  sobre main. Requisitos ya entregados a P08c (nota de coordinacion). Abiertas a
  resolver EN la integracion: SourceType (OBSERVABLE+consumes vs DERIVED nuevo) y
  source_id del RSI (p.ej. rsi.value). cache_key_schema previsto: (exchange,
  symbol, timeframe, price_source, period, formula_version).
EMA/MACD -> decision de la SEMILLA del EMA (Alvaro; via a empirica / b periferico).
  Fundada la semilla, se construyen con el patron del RSI. PROHIBIDO hornear la
  semilla a ciegas.
WINDOWED -> P08c lo anade (aditivo); P08b lo consume en Fase 2.
snapshot+replay de VALOR -> DIFERIDO (no v5.0). Contingencia: re-elevar si el
  trimming de historia se adelanta (romperia el reset fijo / bit-a-bit).

## 5. INVARIANTES VINCULANTES (recordatorio)
Dato CERRADO (DEC-PROVISIONAL-02); warm-up honesto; UNA sola implementacion
backtest+produccion; formula congelada atada a formula_version (I-01 B5b);
cache_key completo (ADR-008); naming en ingles (ADR-016); CE-14 (si obliga a
tocar nucleo, PARAR y ELEVAR); reglas 5.34/5.35 (worktree por pieza, cabecera de
identidad).

FIN P08b_PROGRESO_CONSTRUCCION (documento de trabajo).

---

## divergence.* -- COMPLETADA (commit da80d66)

Fuente derivada de velas: deteccion de divergencias precio/RSI, re-expresion
pura en v5 de la logica de v4 (paridad de RESULTADO/SEMANTICA, sin engines).

Convenciones fijadas (fieles a v4; go de Central tras el I-03 ADDENDUM):
  - Pivotes GEOMETRICOS via swing.symmetric_pivots (DA-I03-9): maximos sobre
    HIGH, minimos sobre LOW.
  - RSI Wilder (rsi.wilder_rsi) leido EN la barra del pivote de precio
    (convencion 'i' de v4).
  - Pares de pivotes consecutivos del mismo tipo (equivale a "ultimos 2" de
    v4 sobre replay).
  - Desigualdad ESTRICTA en precio Y en RSI.
  - Orden determinista: (barra de confirmacion, prioridad de v4).
  - Defaults de paridad: strength=2, rsi_period=14.
  - No hay aritmetica Decimal propia: solo compara Decimals de fuentes ya
    bloqueadas (rsi.*, swing.*).

Verificacion:
  - Referente EXACTO independiente (RSI con fractions.Fraction + reglas y
    orden reimplementados aparte); test diferencial detect_divergences ==
    referente sobre serie LCG de 400 barras (15 divergencias, cubre las 4
    clases: regular_bear 5, regular_bull 5, hidden_bear 4, hidden_bull 1).
  - Clasificador de par probado a mano para las 4 reglas + estrictitud.
  - Guard DIVERGENCE_FORMULA_VERSION = 1.
  - Reproducibilidad: salida independiente del contexto Decimal ambiente.
  - 14 tests verdes; ci_local completa 24/24 verde.

Nota golden: la "prueba de oro" de esta fuente toma forma DIFERENCIAL
(referente independiente) en vez de literal congelado, por ser salida
estructural (lista de eventos). Pendiente de decision de Central si se
quiere ADEMAS un literal congelado.

Fix aplicado durante la entrega: B905 (zip con strict=False explicito) en
divergence.py y test_divergence.py, 1 por fichero, dentro del limite de 2.

PENDIENTE (no en esta tanda): integracion = declaracion + materializador +
p08b_declarations(), condicionada al merge de la capa de materializacion de
P08c.

---

## candle.* -- COMPLETADA (commit 603e0a0)

Familia de fuentes descriptivas deterministas sobre la vela, re-expresion pura de v4 (dictamen P08b-10, D1..D5).

Fuentes (una capacidad = un DataSource):
  - candle.body_pct, candle.upper_shadow_pct, candle.lower_shadow_pct: escalares Decimal (componente/rango*100), EXACTAS (sin redondeo; el round-2 de v4 era presentacion, D3).
  - candle.direction: categorica BULLISH / BEARISH / NEUTRAL.
  - candle.new_high / candle.new_low: booleanas, estricto sobre las lookback barras ANTERIORES (param lookback=20).
  - candle.shadow_signal: categorica HAMMER / SHOOTING_STAR / NONE (param shadow_ratio=2; requiere cuerpo>0).
  - candle.pullback_moment: categorica M1/M2/M3 x bull/bear + NONE (params window=8, doji=10% del rango).

Decisiones aplicadas:
  - NO existe candle.pivot: se reusa swing.* (D2).
  - Decimal pinned (prec 34, HALF_EVEN) en toda la aritmetica; sin AHP (descriptivas, no predictivas, D4).
  - volume_confirm FUERA: composicion hacia volume.* (D5).
  - Rama inalcanzable de v4 en el pullback ("last_seg[1] >= 2") NO reproducida: los segmentos comprimidos alternan, asi que n>=3 -> M3, ==2 -> M2, ==1 -> M1; mismo resultado sin codigo muerto (CE-8).

Verificacion:
  - Anatomia: golden exacto (OHLC de suma 100) + diferencial contra Fraction exacta (tol 1e-30) + borde rango=0 + independencia del contexto Decimal.
  - direction / new_high / new_low / shadow_signal: casos escritos a mano.
  - pullback_moment: patrones M1/M2/M3 a mano + DIFERENCIAL contra replica LITERAL del algoritmo de v4 (incluida su rama inalcanzable) sobre serie de 400 barras -> identico.
  - Guard CANDLE_FORMULA_VERSION = 1.
  - 17 tests verdes; ci_local completa 24/24 verde.

Nota Actions: los push a wip/p08b no disparan el workflow (configurado para main/PR); el veredicto de Actions llegara en el PR de integracion a main. Gate vigente en la rama = ci_local (5.30), verde.

PENDIENTE (no en esta tanda): integracion (declaracion + materializador + p08b_declarations()), condicionada al merge de la capa de materializacion de P08c.

---

## volume.* -- COMPLETADA (commit 8bca564, certificado sobre pila de BD limpia)

Familia de fuentes descriptivas deterministas sobre el volumen (dictamen P08b-11).

Fuentes:
  - volume.ratio_vs_avg: escalar Decimal exacto; volumen actual / media de las N barras anteriores; barra 0 -> None; avg<=0 -> 1 (fail-safe v4).
  - volume.direction: categorica UP/DOWN; actual vs barra anterior; empate -> UP; barra 0 -> None.
  - volume.is_increasing: booleana; media de la 2a mitad vs la 1a de las N barras anteriores; requiere >=4 (si no, False).

Decisiones aplicadas (P08b-11):
  - volume.avg INTERNO (denominador de ratio), NO fuente, hasta que una Rule lo consuma de forma independiente (CE-8).
  - pct_above OMITIDO (transformada monotona trivial de ratio; deriva una Rule).
  - above_avg NO existe: el umbral (20%) es de DECISION -> vive en la Rule/factor (principio general de Central: umbral definitorio -> fuente; umbral de decision -> rule). candle.volume_confirm se resuelve como Rule (ratio > umbral), con el 20% como semilla de paridad.
  - Volumen crudo = market.volume (basico servible de P07); volume.* solo deriva.
  - Decimal pinned (prec 34, HALF_EVEN); sin AHP (descriptivas, no predictivas).

Verificacion:
  - ratio_vs_avg: golden exacto + edge avg<=0 -> 1 + diferencial contra Fraction exacta (tol 1e-30) + independencia del contexto Decimal.
  - direction: casos a mano (incluye empate -> UP).
  - is_increasing: hand (creciente / decreciente / insuficiente) + diferencial contra Fraction.
  - Guard VOLUME_FORMULA_VERSION = 1.
  - test ajustado para mypy strict (narrowing de None en el referente Fraction).
  - 10 tests unitarios verdes; ci_local COMPLETA 24/24 verde sobre pila de BD LIMPIA.

Nota de proceso (para revision de hito): el primer push de 8bca564 se hizo con la bateria en 22/24 (dos tests de integracion de tenancy/rate-limit en rojo) y con salida RESUMIDA, invocando mal la 5.31. Los dos fallos eran estado sucio de los contenedores Docker persistentes, NO de volume.py (funciones puras). Correccion: se recreo la pila de BD limpia (docker compose down/up en infra/compose/docker-compose.yml) y se re-corrio la bateria completa -> 24/24 verde con salida cruda verbatim. 8bca564 CERTIFICADO. Recordatorio operativo: recrear los contenedores antes de cada ci_local para evitar FK/estado acumulado (evita repetir el falso rojo).

PENDIENTE (no en esta tanda): integracion (declaracion + materializador + p08b_declarations()), condicionada al merge de la capa de materializacion de P08c.

---

## vwap.* -- COMPLETADA (commit b82b2bc, certificado 24/24 sobre pila de BD limpia)

Familia de fuentes sobre el VWAP de ventana movil (dictamen P08b-12, D1..D6 + A/B/C).

Fuentes:
  - vwap.value: escalar Decimal; VWAP movil de N velas = SUM(HLC3*vol)/SUM(vol); vol_total=0 -> None. param n_candles=20.
  - vwap.distance_pct: escalar Decimal; |close-vwap|/vwap*100; vwap None -> None; vwap<=0 -> 0.
  - vwap.side: categorica ABOVE/BELOW; close vs vwap; empate -> ABOVE; vwap None -> None.
  - vwap.direction: categorica UP/DOWN; vwap[i] vs vwap[i-1]; empate -> DOWN; sin previo / None -> None.

Paridad fijada (A/B/C):
  - A: ventana MOVIL de N velas, NO anclado a sesion (un VWAP de sesion seria otra fuente).
  - B: precio tipico = HLC3 = (H+L+C)/3.
  - C: Decimal exacto; los round() de v4 eran presentacion.

Decisiones (P08b-12):
  - near_vwap NO existe: umbral 0.5% de DECISION -> Rule de pullback-a-VWAP (DEC-UMBRAL-LOCUS). Fuente = distance_pct exacto.
  - distance_pct es fuente (tiene consumidor: la Rule de pullback); pasa CE-8.
  - H/L/C/V basicos servibles de P07; current_price = candle.close.
  - Decimal pinned (prec 34, HALF_EVEN); sin AHP (descriptivas, no predictivas).

Verificacion:
  - value: golden exacto (ventana movil, HLC3) + diferencial contra Fraction (tol 1e-28) + edge vol=0 -> None + independencia del contexto Decimal.
  - distance_pct / side / direction: diferencial contra referente Fraction (incluye empate side -> ABOVE, empate direction -> DOWN, edges None).
  - Guard VWAP_FORMULA_VERSION = 1.
  - 9 tests unitarios verdes; ci_local COMPLETA 24/24 verde sobre pila de BD recreada desde cero (aplicado el recordatorio operativo: recrear contenedores antes de la bateria -> sin falso rojo).

Nota tecnica: mypy strict exigio narrowing de valores indexados X | None (patron ya visto en volume.*); corregido asignando variables locales antes de comparar, en vwap.py::direction y en el test. Sin cambio de semantica (certificado por golden + diferencial verdes).

PENDIENTE (no en esta tanda): integracion (declaracion + materializador + p08b_declarations()), condicionada al merge de la capa de materializacion de P08c.

---

## fib.* (nucleo puro) -- COMPLETADA (commit 7612a39, certificado 24/24 sobre pila de BD limpia)

Nucleo PURO de niveles Fibonacci parametrizado por rango explicito (dictamen P08b-13). El proveedor del rango queda DIFERIDO (DEC-FIB-RANGO-DIFERIDO).

Fuentes (nucleo puro):
  - fib.levels: grid de 17 niveles (7 retrazados dentro + 5 extensiones arriba + 5 abajo) dado (pivot_high, pivot_low); Decimal exacto; ordered_levels/ordered_pcts de abajo a arriba.
  - fib.nearest_level: nivel mas cercano al precio (empate -> indice menor, como v4).
  - fib.level_pct: pct del nivel mas cercano (0=low, 100=high; <0 o >100 en extensiones).
  - fib.direction: ABOVE/BELOW respecto al nivel cercano (empate -> ABOVE).

Constantes definitorias (paridad v4): retrazados [0, 23.6, 38.2, 50, 61.8, 78.6, 100]; extensiones arriba [127.2, 141.4, 161.8, 200, 261.8]; abajo [-27.2, -41.4, -61.8, -100, -161.8]. Decimal exacto; round() de v4 = presentacion.

Decisiones (P08b-13):
  - DEC-FIB-RANGO-DIFERIDO: el proveedor del rango (stateless swing.* vs recursive con histeresis L2) se decide cuando el materializador recursivo este integrado; hasta entonces fib.* toma el rango como parametro explicito.
  - price_in_level (touch_pct 0.3%) y bounce_confirmed -> Rule (DEC-UMBRAL-LOCUS); no son fuentes.
  - Pivotes = swing.* + fallback max/min (D4, dentro del proveedor diferido).
  - DrawingStore (UI) fuera (D5). Restriccion 1D = wiring. Sin AHP.

Verificacion:
  - Golden exacto (rango 0..100: niveles, nearest, pct, direction).
  - Diferencial contra referente Fraction sobre 300-400 casos: niveles exactos; nearest exacto; level_pct verificado contra la formula geometrica (nearest-low)/rango*100 (NO contra la constante hardcodeada); empate nearest -> indice menor; empate direction -> ABOVE.
  - Independencia del contexto Decimal; validacion de rango invalido (<=0).
  - Guard FIB_FORMULA_VERSION = 1.
  - 9 tests unitarios verdes; ci_local COMPLETA 24/24 verde sobre pila de BD recreada desde cero.
  - Nota tecnica: 3 fixes ruff B905 (zip strict=) dentro del limite; sin cambio de semantica.

PENDIENTE: (1) proveedor del rango (DEC-FIB-RANGO-DIFERIDO); (2) integracion de las 5 familias (declaracion + materializador + p08b_declarations()), gateada al merge de la capa de materializacion de P08c.

---

## P08b -- CHECKLIST DE CIERRE (pendiente; retomar tras el merge de la materializacion de P08c)

Estado: las 5 familias de fuentes candle-derived estan CONSTRUIDAS y verificadas en frio (ci_local 24/24) en wip/p08b. La pieza NO esta ENTREGADA todavia: faltan puntos del DoD (ficha ROADMAP A-1.4).

Fuentes construidas (commits): divergence.* da80d66 ; candle.* 603e0a0 ; volume.* 8bca564 ; vwap.* b82b2bc ; fib.* nucleo puro 7612a39. (RSI / EMA / MACD / SMA / swing.* previos, ya en la rama.)

PENDIENTE PARA ENTREGA (DoD ficha P08b, en orden):
  1. Declaraciones DataSource (ADR-008): cache_key_schema COMPLETO (datasource_id, exchange, symbol, timeframe, price_source, bucket_offset, formula_version, parametros, ventana, as_of) + memory_model + servibility; y p08b_declarations() en el catalogo real. Gateado al merge de la capa de materializacion de P08c.
  2. Materializador por familia (POINT_LOCAL / ventana / recursivo segun la fuente). Mismo gate (P08c).
  3. swing.* cableado como primitiva UNICA tambien para pivotes de RSI y de CVD (DA-I03-4), no solo de precio.
  4. Proveedor del rango de fib.* (DEC-FIB-RANGO-DIFERIDO): stateless swing.* vs recursive con histeresis L2; se decide cuando este el materializador recursivo.
  5. Validacion en caliente OBLIGATORIA: series sobre datos reales comparadas con TradingView (+ verificacion TradingView como DoD auxiliar). No rebajable.
  6. Actions verde 3/3, cero skips (llega en el PR de integracion a main; los push a wip/p08b no disparan el workflow).
  7. Con 1-6 en verde: informe de entrega P08b -> doble revision (Central + CSA) -> firma Alvaro -> tanda de cierre que actualiza los 4 archivos de contexto (5.5 / 5.9 / 5.17).

Retomar cuando P08c haya mergeado su capa de materializacion.

---

## P08b -- PLAN DE CABLEADO (dictamen Central P08b-INT-01, 2026-07-30)

Interfaz de materializacion de P08c consumida (handoff commit 7c56472). Cableado aprobado en 5 lotes:
  LOTE 1: read_candle_window (lo ANADE P08b en infra/db, consume market_candle de P07 -GRANT 0016-, ADITIVO; pluma de P08b por turno 5.34) + declaraciones/specs de las Decimal directas: candle.body_pct / upper_shadow_pct / lower_shadow_pct (POINT_LOCAL) + vwap.value / vwap.distance_pct / volume.ratio_vs_avg (WINDOWED).
  LOTE 2: EMA (RECURSIVE, snapshot Decimal unico, mecanismo materialize_recursive actual).
  LOTE 3: RSI / MACD (RECURSIVE multi-estado; spec por-fuente que snapshotea el estado completo -avg_gain/avg_loss en RSI; 3 estados EMA en MACD-; COORDINADO con P08c: toca el plumbing de snapshot; P08b propone diseno, P08c dictamina o eleva).
  LOTE 4: fib (cuando llegue el proveedor de rango, DEC-FIB-RANGO-DIFERIDO).
  LOTE 5: DIFERIDAS hasta que el contrato de Serie soporte no-Decimal (o su primer consumidor real las abra): categoricas (candle.direction/shadow_signal/pullback_moment, vwap.side/direction, volume.direction), booleanas (candle.new_high/new_low), de-lista (swing.*, divergence.*), grid (fib.levels). NO se codifican a Decimal (no se ensucia el contrato).

Decisiones: D1 diferir no-escalares (opcion c); D2 EMA ya / RSI-MACD spec enriquecido coordinado con P08c; D3 read_candle_window lo anade P08b; D4 swing/divergence diferidas.
Convenciones vinculantes: materializar con DEFAULTS (guarda del compilador rechaza ref.params no vacio); NOT_EVALUABLE sin inventar barras (menos valores o ()); UnwiredSourceError si no hay materializador; sin correccion WINDOWED/RECURSIVE/INTEGRATOR en v5.0; aditividad; ADR-008 + fixture bit-a-bit + ci_local 24/24; fixes max 2; ASCII-safe.

SECUENCIA REAL: preparar declaraciones/specs/lector se DISENA ahora; el CABLEADO (registro en discovery.py + SOURCE_MATERIALIZERS en el worker + read_candle_window en infra/db) y el ci_local requieren wip/p08b REBASADO sobre main con la materializacion mergeada (pendiente PR + Actions + cierre de P08c). Hasta el rebase: solo diseno/borrador + verificacion de envoltorios puros (que solo dependen de las funciones puras ya commiteadas).

---

## P08b -- LOTE 1 RATIFICADO (dictamen Central P08b-INT-02, 2026-07-30) -- LISTO PARA EJECUTAR POST-MERGE

Decisiones ratificadas:
- D-A (A): N (n_candles / lookback) es PARAM declarado, va en cache_key. VWAP-20 != VWAP-50 (hechos distintos). El materializador usa el DEFAULT (guarda del compilador); ese default produce la clave canonica.
- D-B (A): las candle-derived son HOJAS, consumes=(). Leen market_candle crudo via read_candle_window (identico a market.close / read_close_window). NO se declara market.candle (seria no-escalar + DAG de 2o nivel no cableado).
- D-C: P08c cierra SIN deuda hacia P08b. El mecanismo entregado (materialize_windowed + materialize_recursive + Protocol + registro + dispatch por SOURCE_ID) es suficiente. P08b anade TODO su cableado en su turno post-merge (aditivo); orden P08c-mergea -> P08b-rebasa-y-anade.

ESPECIFICACION DE CABLEADO LOTE 1 (ejecutar en el turno post-merge, tras rebase de wip/p08b sobre main con la materializacion mergeada):
  1. read_candle_window(session, exchange, symbol, timeframe, open_time, count) -> Sequence[CandleClosedPayload], en ce_v5/infra/db/market_candle.py. Calca read_footprint_window: oldest->newest, `count` velas cerradas terminando en open_time, un solo flujo. Lee la tabla market_candle de P07 (GRANT SELECT a ce_v5_rules via 0016).
  2. Specs nuevos en entrypoints/worker_rules/materializers.py (aditivo, mismo patron que los de footprint):
     - CandlePointLocalSpec(extract: (CandleClosedPayload) -> Decimal): lee read_candle_window(..., history_bars) y proyecta extract por barra.
     - CandleWindowedSpec(transform: (Sequence[CandleClosedPayload]) -> Decimal, window_bars): lee history_bars + window_bars - 1 y aplica materialize_windowed.
  3. Declaraciones (source_type=OBSERVABLE, servibility=CONTINUOUS, value_type=DECIMAL, history_units=(BARS,), shared_evaluation=True, sharing_scope=PUBLIC_CROSS_TENANT, consumes=()):
     - candle.body_pct / candle.upper_shadow_pct / candle.lower_shadow_pct: POINT_LOCAL, params=(), cache_key=(exchange, symbol, timeframe); spec CandlePointLocalSpec(extract=<pct de la vela T>).
     - vwap.value / vwap.distance_pct: WINDOWED, params=(n_candles INT def 20), cache_key=(exchange, symbol, timeframe, n_candles); spec CandleWindowedSpec(transform=<value/distance de la ventana>[-1], window_bars=20).
     - volume.ratio_vs_avg: WINDOWED, params=(lookback INT def 20), cache_key=(exchange, symbol, timeframe, lookback); spec CandleWindowedSpec(transform=ratio_vs_avg(ventana)[-1], window_bars=lookback+1).
     transform/extract = envoltorios finos sobre las funciones puras ya commiteadas (extraen O/H/L/C/V y toman el ultimo valor de la serie). vwap.distance_pct recomputa el VWAP internamente (hoja; DAG de 2o nivel no cableado).
  4. Alta de cada spec en SOURCE_MATERIALIZERS (registro compartido) + declaraciones publicadas via el modulo productor y registradas en discovery.py (bundle P08b).
  5. Al codificar read_candle_window: confirmar columnas contra el DDL de market_candle y los campos de CandleClosedPayload (open/high/low/close/volume/open_time/exchange/symbol/timeframe).
  6. Verificacion: fixture bit-a-bit por fuente (misma base + misma funcion pura -> misma serie) + ci_local 24/24 sobre pila de BD LIMPIA (recrear contenedores antes).

GATE: ejecutar cuando P08c mergee a main (Actions verde + cierre de contexto) y wip/p08b rebase sobre main. Disparo de vuelta: HANDOFF_P08c_MATERIALIZACION seccion 12 (aviso a Alvaro).

---

## P08b -- LOTE 1a CABLEADO (commit c8040ce, ci_local 24/24 sobre pila limpia)

Primer cableado EN VIVO de fuentes candle-derived, tras rebase de wip/p08b sobre main (materializacion de P08c mergeada). Dictamen P08b-INT-03: C1 (reuso read_ohlcv_window, SIN lector nuevo), C2(B) (solo las 4 siempre-Decimal).

Fuentes materializadas:
  - candle.body_pct / candle.upper_shadow_pct / candle.lower_shadow_pct: POINT_LOCAL, consumes=(), cache_key=(exchange, symbol, timeframe); spec CandlePointLocalSpec sobre read_ohlcv_window.
  - volume.ratio_vs_avg: WINDOWED, param lookback (INTEGER, def 20) en cache_key, window_bars=lookback+1; spec CandleWindowedSpec sobre read_ohlcv_window.

Cableado (aditivo):
  - Reuso de read_ohlcv_window (ya en main, T-05) -> CandleOHLCV; sin lector nuevo (C1).
  - CandlePointLocalSpec + CandleWindowedSpec en entrypoints/worker_rules/materializers.py; alta de las 4 en SOURCE_MATERIALIZERS.
  - declarations() en indicators/candle.py e indicators/volume.py; registradas en discovery.py (bundle P08b). _DEFAULT_LOOKBACK -> LOOKBACK_DEFAULT (publico) en volume.py.

Verificacion:
  - tests/unit/test_candle_derived_materializers.py: extract/transform bit-a-bit == funcion pura + "muerde" (los tres pct distintos; ratio no constante) + memory_model por fuente + lookback en cache_key.
  - Aserciones de conjunto exacto actualizadas (SOURCE_MATERIALIZERS y discovery _EXPECTED).
  - 1 fix ruff (docstring >88), dentro del limite. ci_local COMPLETA 24/24 verde sobre pila recreada.

PENDIENTE: LOTE 1b (vwap.value / vwap.distance_pct), gateado por el tratamiento del hueco de volumen-cero (elevacion P08b-INT-04). Luego LOTE 2 (EMA), LOTE 3 (RSI/MACD), LOTE 5 (categoricas / booleanas / listas).

---

## P08b -- LOTE 1b CABLEADO (commit 6ab840a) -- LOTE 1 COMPLETO

vwap.value y vwap.distance_pct materializados (WINDOWED, param n_candles def 20, window_bars=n_candles) sobre read_ohlcv_window. Con esto las 6 fuentes candle-derived siempre-Decimal quedan EN VIVO.

Fallback de volumen cero (dictamen P08b-INT-04, opcion B):
  - La funcion pura vwap.* se queda FIEL: None si el volumen total de la ventana es 0 (VWAP indefinido).
  - La POLITICA del degenerado vive en el CABLEADO (transform): si vol total = 0, VWAP := media NO PONDERADA de HLC3 de la ventana (limite del VWAP con pesos uniformes; coincide con el precio plano real). distance_pct mide contra ese VWAP de fallback.
  - Fixture bit-a-bit del degenerado: (a) la pura devuelve None; (b) el materializador devuelve el fallback HLC3 (valor 10, distancia 40); (c) muerde (10 != close 6, != 0). Caso normal: transform == funcion pura bit-a-bit.

Nota de proceso: la tanda importaba vwap_distance_pct en materializers.py sin usarlo (el transform de distancia recomputa desde el VWAP efectivo); ruff --fix lo limpio. Sin efecto funcional; la equivalencia con la pura la garantiza el test del caso normal. ci_local 24/24 verde sobre pila limpia.

FUENTES EN VIVO (LOTE 1 completo): candle.body_pct / candle.upper_shadow_pct / candle.lower_shadow_pct (POINT_LOCAL); volume.ratio_vs_avg / vwap.value / vwap.distance_pct (WINDOWED). Todas hoja (consumes=()), sobre read_ohlcv_window.

PENDIENTE: LOTE 2 (EMA, RECURSIVE -- requiere plumbing de snapshot: elevacion de diseno + coordinacion con P08c). LOTE 3 (RSI/MACD, recursive multi-estado). LOTE 4 (fib, gateado al proveedor de rango). LOTE 5 (categoricas / booleanas / listas / grid, gateado al contrato no-Decimal).
