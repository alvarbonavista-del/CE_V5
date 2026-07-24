# Barrido de seguridad 5.15 - Libro L2 con estado (P07c)

Superficie externa: el feed publico del LIBRO L2 (orderbook) de tres exchanges, por
WebSocket (+ REST solo en Binance), SIN credenciales. Barrido PROPIO de P07c: MAS EXIGENTE
que el de P07b (velas/trades) porque el libro es una superficie **CON ESTADO** -- se
reconstruye desde una foto y avanza por secuencia --, con mas procesamiento (apply en
orden) y backpressure. Barrido POR EXCHANGE (regla dura T-03: no se copia); la parte de
PLATAFORMA (Motor, ingestor, snapshot, writer, DB) es UNA sola implementacion para los
tres y se audita una vez.

Fecha de verificacion de limites: 2026-07-24. Alcance: solo datos PUBLICOS. Las
credenciales BYOC de exchange son P10a (otra pieza, otro rol de DB, cifrado, gate de
politica); ningun connector de P07c acepta ninguna.

EVIDENCIA EN CALIENTE (5.32, Tandas V/VI + fix de siembra): siembra limpia al PRIMER
intento en los tres (8/8 en Binance tras el pre-buffer; OKX/Bybit por snapshot WS);
discontinuidad -> is_complete=False + resync publicado -> recuperacion por re-siembra ->
is_complete=True; caudal ~9-10 deltas/seg por simbolo (acotado por la cadencia de 100 ms),
coste de mantenimiento 0.07-0.22% de un core por simbolo.

---

## Parte A -- Plataforma CON ESTADO (Motor + ingestor + snapshot + writer + DB)

Comun a los tres exchanges: el connector solo TRADUCE; el estado, la validacion de
dominio, la resiliencia y la persistencia viven aqui, una vez.

### A1. Validacion en el BORDE (ADR-006): el dato del exchange NO es confiable
CONSTRUIDO. `platform/market/orderbook_book.py` es la unica frontera de confianza del
libro. Un nivel entra solo si es integro: `_validated_levels` + `_decimal` rechazan precio
no numerico, no finito (NaN/Infinity) o no positivo, y tamano negativo; una secuencia
ausente (`_require_seq`) es contrato roto. El rechazo es TIPADO y DATO, no texto:
`RawOrderbookRejected` con `RawOrderbookRejectionReason` (SYMBOL_MISMATCH, MALFORMED_NUMBER,
CONTRACT_VIOLATION), conjunto CERRADO (ADR-016). ATOMICO: una foto o un delta con UN nivel
podrido se rechaza ENTERO y el libro se queda como estaba. Los precios/tamanos viajan como
Decimal EN TEXTO (nunca float: 0.1 binario no es 0.1, y el libro es la base del precio de
ejecucion en M5). Anti-suplantacion: un mensaje de OTRO (exchange/market_type/symbol) que
el del libro se rechaza (`_verificar_pertenencia`).

### A2. ESTADO e is_complete FAIL-SAFE (lo que P07b NO tenia)
CONSTRUIDO. El libro es CON ESTADO y ORDER-DEPENDIENTE. Ante la MENOR duda, `is_complete`
es False (`orderbook_book.py`): antes de la primera foto, y tras un hueco no resuelto. Un
libro con un agujero conocido JAMAS se publica como completo -- en M5 alimentaria ordenes
reales sobre una profundidad mentida. El Motor NO adivina lo que falta ni encadena a
ciegas: marca incompleto y SENALA resync. El snapshot (`orderbook_snapshot.py`) propaga
ese fail-safe de forma UNIFORME: el frontier de una barra sale `is_complete=False` si el
libro no esta completo AHORA **o** si una discontinuidad solapa `[open_time, close_time)`
-- mismo criterio que el footprint (cond.3). La frontera sin semilla se EMITE con
`is_complete=False` y niveles vacios (opcion B): la incompletitud va EN EL CANON, no en una
metrica; el validador 5.21 del contrato lo admite SOLO si `is_complete=False`.

### A3. RESYNC OBSERVABLE por secuencia (no checksum)
CONSTRUIDO. La integridad es POR NUMERO DE SECUENCIA del exchange, no por checksum (ver
por-exchange). Un hueco detectado por el Motor es un HECHO PROPIO del libro (no una
candle_corrected): `orderbook_ingestor.py` lo PUBLICA como `market.orderbook_resynced` por
outbox atomico (persist_and_enqueue). Una reconexion se resuelve RE-SEMBRANDO (foto nueva)
y su discontinuidad se APUNTA (record_discontinuity) para que el frontier de las barras
solapadas salga incompleto. Metricas OBSERVABLES (`OrderbookIngestionMetrics`):
deltas_applied, resyncs, reseeds, discontinuities_recorded, seed_errors, unseeded_dropped,
rejected por motivo, degraded_streams; sin ellas, un stream que solo re-sincroniza seria
invisible.

### A4. SIEMBRA por el procedimiento oficial de cada exchange
CONSTRUIDO. Un libro arranca de una FOTO; sin ella un delta no significa nada. La siembra
sigue el procedimiento OFICIAL de cada exchange (ver por-exchange): Binance por REST
`/api/v3/depth` con PRE-BUFFER del WS (procedimiento I-02, cierra la ventana perdida
foto<->buffer); OKX/Bybit por el snapshot del propio WS. Un resync se resuelve pidiendo
OTRA foto, no parcheando.

### A5. BACKPRESSURE (manda el motor, no el exchange)
CONSTRUIDO. Cola SEPARADA del libro por connector (`_cola_orderbook`, maxsize=max_queue,
50000), distinta de las de velas y trades: un pico de deltas no desaloja velas. `poll_deltas`
es PULL CON TOPE: el motor pide lo que digiere y el resto ESPERA en el feed. Si la cola se
llena, se DESCARTA y se CUENTA (`dropped_full_queue_orderbook`, + degraded_streams): nunca
una cola infinita en memoria (una cola infinita no es resiliencia, es una bomba). El
`OrderbookIngestionConfig` acota los deltas por ciclo (max_batch=500).

### A6. Menor privilegio y APPEND-ONLY (regla 5.20)
CONSTRUIDO. Migracion `0020_market_orderbook.sql` (append-only, sucesora de 0019, no edita
lo aplicado -- 5.14): SOLO `ce_v5_ingestion` escribe (`GRANT SELECT, INSERT`);
`ce_v5_app` solo LEE (`GRANT SELECT`); `ce_v5_operator` no toca (`REVOKE ALL`). APPEND-ONLY
REAL: `REVOKE UPDATE, DELETE, TRUNCATE` a TODOS los roles de runtime en
`market_orderbook_snapshot` y `market_orderbook_discontinuity` -- nadie reescribe el libro,
ni quien lo escribe; un resync es un HECHO NUEVO, no una edicion. Datos publicos
(isolation_scope=public_market, sin tenant_id; la discontinuidad sin RLS, como
market_trade_gap). El writer (`infra/db/market_orderbook.py`) persiste el frontier/resync Y
la outbox en LA MISMA transaccion (ADR-013). `tools/check_market_access.py` verifica en
Actions (arbitro final) que la API NO puede escribir estas tablas y que el registro
event_type->payload cubre `market.orderbook_frontier` y `market.orderbook_resynced`.

### A7. DIFERIDOS a v5.1 con disparador de revision (no deuda silenciosa)
REGISTRADO (dueno: Central; justificacion 5.11 / cond.4 del cierre (a)). El LIBRO PROFUNDO
(mas alla del top-K) y el DELTA-LOG crudo NO se persisten hoy: el market data aun no fluye
a produccion, y persistir profundidad+delta-log sin consumidor seria coste sin invariante.
Documentado en los COMMENT de las tablas en 0020. DISPARADOR DE REVISION explicito: si el
market data empezara a servirse antes de v5.1, se reabre la decision y se evalua persistir
profundidad y delta-log. No es olvido: es una decision fechada con su gatillo.

---

## Parte B -- Binance Spot (dominio de DATOS)

### B1. Endpoints y allowlist (cond.4)
- WS combinado: `wss://data-stream.binance.vision:443/stream?streams=btcusdt@depth@100ms/...`
- REST foto: `https://data-api.binance.vision/api/v3/depth?symbol=..&limit=100`
DOMINIO DE DATOS (`data-*.binance.vision`), NO geo-restringido por MiCA (lo esta el
SERVICIO, no el dato): sirve los MISMOS streams y payloads que `stream.binance.com` /
`api.binance.com`. Verificado en caliente. El libro NO abre socket nuevo: `@depth@100ms`
se MULTIPLEXA sobre la conexion combinada que ya llevan velas y trades; solo cambia el
`streams=`. Todo sin credencial.

### B2. TLS y cero secretos
CONSTRUIDO. `ssl.create_default_context()` en WS y REST; la verificacion NO se desactiva
jamas (`connector.py`). Feed publico: el connector no acepta ninguna credencial; si
apareciera una API key seria un error de capa (BYOC es P10a).

### B3. Limites / presupuesto de conexiones
CONSTRUIDO / DECLARADO (`binance/pool.py`, ConnectionPlanner): limite PUBLICADO 1024 streams
por conexion; 300 conexiones por IP cada 5 min (margen propio: `max_connections=200`). El
libro suma UN stream (`@depth@100ms`) al plan combinado del par, no una conexion. Si la
demanda excede la capacidad, `ExchangeLimitExceeded` ANTES de abrir nada.

### B4. Integridad por SECUENCIA (U/u) + siembra I-02
CONSTRUIDO. `_classify_binance` (`orderbook_book.py`): descarta `u <= lastUpdateId`
(reenvios); el PRIMER delta tras la foto ABARCA (`U <= lastUpdateId+1 <= u`, regla oficial,
`first_after_seed`); despues, continuidad estricta `U == u_previo+1`; un salto es hueco
(fail-safe). SIEMBRA (`connector.seed`): BUFFERIZA el WS ANTES de pedir la foto REST
(`_esperar_buffer_libro`, timeout acotado `orderbook_seed_buffer_timeout_s`) para que el
buffer ABARQUE `lastUpdateId` -- cierra la ventana perdida foto<->buffer diagnosticada
(8/8 siembras frescas limpias en caliente; antes ~50% arrancaban en resync). Un stream
muerto no cuelga: vencido el tope procede y actua el fail-safe.

### B5. Routing y fault isolation
CONSTRUIDO. `_encolar_orderbook` enruta el `depthUpdate` a la cola del LIBRO (no a velas ni
trades) por el campo `e`; un `depthUpdate` sin mapa nativo->canonico se cuenta como
translation_error, no crashea. Un hilo lector captura toda excepcion y reconecta; una
reconexion marca la clave del libro (`_registrar_reconexion` + `drain_reconnected`) para
que el motor re-siembre. Backpressure: `dropped_full_queue_orderbook` (A5).

---

## Parte C -- OKX Spot (DOS carriles WS)

### C1. Endpoints y allowlist (cond.4) -- 2a conexion OBLIGADA por el exchange
- `wss://ws.okx.com:8443/ws/v5/business` -> velas (candle<bar>) + trades (trades-all).
- `wss://ws.okx.com:8443/ws/v5/public`   -> LIBRO (books).  [2a conexion, Tanda VI]
- `https://www.okx.com` -> REST (catalogo, bootstrap de velas). El libro NO usa REST.
OKX movio 'candle' a /business pero dejo 'books' SOLO en /public: suscribir books en
/business da 60018 (verificado en caliente, Tanda V). La 2a conexion la OBLIGA el exchange,
no es un capricho; sigue siendo b-i (MISMO proceso worker_ingestion, MISMO rol
ce_v5_ingestion; solo cambia el socket). De PLENO DERECHO (no fire-and-forget): mismo
lector, reconexion y re-semilla. Todo sin credencial.

### C2. TLS y cero secretos
CONSTRUIDO. `ssl.create_default_context()` en AMBOS carriles WS y en REST. Feed publico sin
credenciales; el connector no acepta ninguna (BYOC es P10a). Unico header: User-Agent (OKX
rechaza con 403 el de urllib tras Cloudflare), sin secretos.

### C3. Limites / presupuesto por carril (cond.5)
CONSTRUIDO (`okx/pool.py`): 240 subscripciones por conexion (error 60014); margen propio
`max_subscriptions_per_connection=200`, `max_connections=20`. DOS ConnectionPlanner
INDEPENDIENTES, uno por carril (`_planner` business, `_planner_books` public): cada uno
computa su capacidad por separado y ninguno cuenta contra el otro. OKX no publica tope de
conexiones concurrentes por IP; el techo propio es conservador.

### C4. Integridad por SECUENCIA (seqId/prevSeqId); checksum IGNORADO
CONSTRUIDO. `_classify_okx` (`orderbook_book.py`): encadena por `prevSeqId == seqId del
anterior`. DOS EXCEPCIONES OKX QUE NO SON HUECO (y confundirlas dispararia resyncs
inutiles): keepalive (`seqId == prevSeqId`: el libro no cambio) y mantenimiento
(`seqId < prevSeqId`: OKX reinicio su contador) -> NOOP, ni incompleto ni resync. Solo un
mensaje que AVANZA cuyo prevSeqId NO encadena es hueco. El CHECKSUM de OKX se IGNORA a
proposito (`translate.py`): es un mecanismo deprecado; la integridad la da la cadena de
secuencias, no un CRC. SIEMBRA por el snapshot del WS (primer `action=snapshot`,
`prevSeqId=-1`): sin ventana perdida (llega por el mismo socket); un re-snapshot marca
reconexion para re-sembrar.

### C5. Fault isolation por MarketStreamKey + carriles separados (cond.3)
CONSTRUIDO. /public (books) y /business (velas+trades) son SOCKETS SEPARADOS: un fallo de
uno no tumba al otro. `force_reconnect_all` cierra AMBOS carriles; `shutdown` para ambos.
Un mensaje malformado o un simbolo no representable se cuenta y se salta; un hilo lector
captura toda excepcion y reconecta. Keep-alive de aplicacion (OKX corta a 30 s): el cliente
manda 'ping' en inactividad, en ambos carriles.

---

## Parte D -- Bybit v5 Spot

### D1. Endpoints y allowlist (cond.4)
- WS: `wss://stream.bybit.com/v5/public/spot` -> topic `orderbook.200.{symbol}` (100 ms).
- REST: `https://api.bybit.com` (catalogo/bootstrap de velas). El libro NO usa REST.
Todos los canales publicos de spot (kline, publicTrade, orderbook) van por el MISMO
endpoint: el libro se MULTIPLEXA sobre la conexion que ya existe, sin socket nuevo. Sin
credencial.

### D2. TLS y cero secretos
CONSTRUIDO. `ssl.create_default_context()` en WS y REST. Feed publico sin credenciales; el
connector no acepta ninguna. Unico header: User-Agent, sin secretos.

### D3. Limites / presupuesto de conexiones
CONSTRUIDO / DECLARADO (`bybit/pool.py`): hasta 10 args por PETICION de suscripcion (el
connector suscribe en TANDAS de <=10); tope de 21.000 caracteres de args por conexion; no
mas de 500 conexiones cada 5 min. `max_connections=20` es techo PROPIO. El libro suma un
topic al plan, no una conexion.

### D4. Integridad por SECUENCIA (u/seq); RESET por u==1
CONSTRUIDO. `_classify_bybit` (`orderbook_book.py`): la continuidad va por `u` (updateId);
`u == u_previo+1` encadena, un `u` ya visto es reenvio, un salto es hueco. Un `u == 1` (o
`is_snapshot`) es un RESET: Bybit reinicia su updateId y reenvia una FOTO cuando su servicio
se reinicia -> el libro se RECONSTRUYE (recupera de un resync sin reconexion). El campo
`seq` (secuencia cruzada) se conserva sin usar. SIEMBRA por el snapshot del WS
(`type=snapshot`): sin ventana perdida. El simbolo va PEGADO (BTCUSDT): la vuelta
nativo->canonico se CONSULTA (set_symbol_map), no se deduce.

### D5. Fault isolation y keep-alive
CONSTRUIDO. `data` del libro es un OBJETO (no lista, a diferencia de velas/trades): se
enruta por PREFIJO de topic ANTES del check de lista. Un mensaje malformado se cuenta y se
salta; un hilo lector captura toda excepcion y reconecta; la reconexion marca la clave del
libro para re-sembrar. Keep-alive: Bybit corta si no recibe ping en ~20 s; el cliente manda
`{"op":"ping"}` cada 18 s SIEMPRE (aunque fluya dato), a diferencia de OKX (solo en
inactividad) y Binance (sin ping de aplicacion).

---

## CI hermetico vs validacion en caliente (regla 5.18)
El CI NO abre sockets. Prueba a fondo lo separado de la red: por-exchange `translate.py`,
`pool.py`, `symbols.py` y el routing del connector con fakes; y la PLATAFORMA con estado
(`orderbook_book.py`, `orderbook_ingestor.py`, `orderbook_snapshot.py`, el writer y las
migraciones/grants) con secuencias controladas y fakes. El IO real de cada connector NO se
prueba en CI: se valida EN CALIENTE (5.32) con `tools/validate_orderbook_live.py` (los tres
exchanges) y `tools/diag_binance_seed_window.py` (la ventana de siembra de Binance). La
evidencia cruda de esas validaciones va en los informes de las Tandas V/VI y del fix de
siembra.
