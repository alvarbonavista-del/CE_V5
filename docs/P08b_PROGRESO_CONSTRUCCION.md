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
