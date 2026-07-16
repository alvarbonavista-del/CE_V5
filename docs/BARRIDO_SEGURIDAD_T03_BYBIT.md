# Barrido de seguridad 5.15 - Conector Bybit v5 Spot (T-03)

Superficie externa NUEVA: feed publico de Bybit v5 Spot (WebSocket + REST), sin
credenciales. Barrido PROPIO de Bybit: NO se copia el de OKX ni el de Binance (regla
dura de T-03). Cada exchange tiene su endpoint, su heartbeat, su formato y sus limites.

Fecha de verificacion de limites: 2026-07-16.
Fuentes vigentes:
- WS kline y confirm: https://bybit-exchange.github.io/docs/v5/websocket/public/kline
- Conexion, heartbeat y limites: https://bybit-exchange.github.io/docs/v5/ws/connect
- REST kline e instruments: https://bybit-exchange.github.io/docs/v5/market/kline

Alcance: solo datos PUBLICOS (velas y catalogo). Las credenciales BYOC son P10a. Este
conector NO acepta ninguna credencial.

## Control por control

### C1. Entrada no confiable validada antes del bus (ADR-006)
CONSTRUIDO. El conector solo TRADUCE formato (translate.py); la validacion de dominio la
hace la unica frontera de confianza platform/market/normalize.py, igual para los tres
exchanges. Un mensaje malformado, un topic no-kline, un simbolo no mapeado o un intervalo
no soportado NO llegan al bus: se cuentan como translation_errors y se descartan, nunca
crashean.

### C2. Limites de cardinalidad (anti-DoS de plataforma)
CONSTRUIDO. pool.py reparte suscripciones con tope: 200 por conexion y 20 conexiones,
techos PROPIOS conservadores (Bybit no publica un tope explicito de topics por conexion).
El connector suscribe en TANDAS de <=10 args, que es el limite PUBLICADO de spot por
peticion de suscripcion. La cola de velas tiene tope (max_queue=50000) con backpressure
OBSERVABLE (dropped_full_queue).

### C3. Respeto de los limites PUBLICADOS por Bybit
CONSTRUIDO / DECLARADO. Limites vigentes (2026-07-16):
- Spot: hasta 10 args por peticion de suscripcion (respetado en tandas).
- Tope de 21.000 caracteres de args por conexion.
- No mas de 500 conexiones cada 5 minutos.
- Ping de keep-alive cada 20 s (ver C6).
max_connections=20 es un techo PROPIO conservador, no un limite de Bybit.

### C4. Timeouts
CONSTRUIDO. REST con timeout de 10 s. El bucle de lectura usa recv con timeout de 5 s
(para poder mandar el ping periodico aunque no llegue dato).

### C5. Reconexion sin martillear (backoff + jitter)
CONSTRUIDO. Backoff exponencial de 1 s a 60 s con jitter deterministico por hilo.

### C6. Keep-alive (ping) propio de Bybit
CONSTRUIDO. Bybit corta si no recibe un ping en ~20 s. El cliente envia JSON
{"op":"ping"} cada 18 s, SIEMPRE, aunque el feed este empujando datos (Bybit lo exige, a
diferencia de OKX, que solo pinguea en inactividad, y de Binance, que no usa ping de
aplicacion). El pong del servidor se ignora. Copiar el barrido de otro exchange habria
puesto mal este control.

### C7. TLS siempre verificado
CONSTRUIDO. WebSocket wss:// y REST https:// con ssl.create_default_context(); la
verificacion NO se desactiva jamas.

### C8. Cero secretos
CONSTRUIDO. Feed publico sin credenciales; el conector no acepta ninguna. El unico header
que se manda es un User-Agent de identificacion (sin secretos). No se registran payloads
ni material sensible.

### C9. Fault isolation por stream
CONSTRUIDO. Un mensaje malformado o un simbolo no representable no tumba el proceso
(translation_error + continue). Un hilo lector captura toda excepcion y reconecta. El
catalogo aplica ADR-006 (saltar y contar) por el mecanismo generico de sync_catalog. El
bootstrap tras reconexion es por-stream, orquestado por el motor.

## Diferencias con OKX/Binance (por que NO se copia)
- Endpoint: wss://stream.bybit.com/v5/public/spot.
- Simbolo PEGADO (BTCUSDT): la vuelta nativo->canonico se CONSULTA (set_symbol_map, como
  Binance; OKX era identidad).
- Suscripcion por topic kline.{interval}.{symbol}, en tandas de <=10 args.
- Heartbeat: JSON {"op":"ping"} cada ~18 s, SIEMPRE (OKX: texto 'ping' en inactividad;
  Binance: sin ping de aplicacion).
- Vela WS como OBJETO con campos nombrados (start/end/timestamp/confirm); REST como array.
- REST envuelto en {"retCode":0,"result":{"list":[...]}}.

## CI hermetico vs validacion en caliente (regla 5.18)
El CI NO abre sockets. Prueba a fondo lo separado de la red: symbols.py, translate.py y
pool.py (tests hermeticos). connector.py (el IO real) NO se prueba en CI: se valida EN
CALIENTE contra Bybit real. Resultado de la validacion en caliente (2026-07-16): streaming
real (provisionales + cerrada), reconexion forzada con auto-bootstrap del motor y dedup
(filas == claves distintas: cero duplicados), y catalogo sincronizado (592 instrumentos).
