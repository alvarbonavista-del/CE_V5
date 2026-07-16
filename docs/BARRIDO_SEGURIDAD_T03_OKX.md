# Barrido de seguridad 5.15 - Conector OKX Spot (T-03)

Superficie externa NUEVA: feed publico de OKX Spot (WebSocket + REST), sin credenciales.
Este barrido es PROPIO de OKX: NO se copia el de Binance (regla dura de T-03). Cada
exchange tiene su endpoint, su heartbeat, su formato y sus limites.

Fecha de verificacion de limites: 2026-07-16.
Fuentes vigentes:
- Limites WS/REST y ping-pong: https://www.okx.com/docs-v5/en/
- Migracion del canal candle a /business:
  https://www.okx.com/en-us/help/changes-to-v5-api-websocket-subscription-parameter-and-url

Alcance: solo datos PUBLICOS (velas y catalogo). Las credenciales BYOC de exchange son
P10a (otra pieza, otro rol de DB, cifrado, gate de politica). Este conector NO acepta
ninguna credencial.

## Control por control

### C1. Entrada no confiable validada antes del bus (ADR-006)
CONSTRUIDO. El conector solo TRADUCE formato (translate.py); la validacion de dominio
(precio finito y positivo, volumen no negativo, rango OHLC, alineacion de ventana,
coherencia de madurez) la hace la unica frontera de confianza platform/market/normalize.py,
igual para los tres exchanges. Un array malformado, un canal no-vela, un instId no
canonico o un timeframe no soportado NO llegan al bus: se cuentan como translation_errors
y se descartan, nunca crashean.

### C2. Limites de cardinalidad (anti-DoS de plataforma)
CONSTRUIDO. pool.py reparte suscripciones con tope: 200 por conexion (margen bajo el
limite PUBLICADO de OKX de 240, error 60014) y un techo propio de 20 conexiones. Si la
demanda excede la capacidad, ExchangeLimitExceeded ANTES de abrir nada. La cola de velas
tiene tope (max_queue=50000) con backpressure OBSERVABLE (dropped_full_queue): nunca crece
sin limite en memoria.

### C3. Respeto del rate limit PUBLICADO por OKX
CONSTRUIDO / DECLARADO. Limites vigentes (2026-07-16):
- 240 suscripciones de canal por conexion (error 60014). Margen propio: 200.
- 480 peticiones subscribe/unsubscribe por hora y conexion.
- 3 peticiones de conexion por segundo por IP.
- Corte de conexion si no llega dato en 30 s.
OKX NO publica un tope de conexiones concurrentes por IP (a diferencia de Binance,
300/IP/5min): max_connections=20 es un techo PROPIO conservador. NOTA a escala: el ritmo
de 3 conexiones/seg por IP no lo controla el pool; a escala v5.0 (1-2 conexiones) no
aplica; si algun dia se abrieran muchas conexiones de golpe, hay que espaciar los connect.
Punto de vigilancia, no deuda (no lo exige v5.0).

### C4. Timeouts
CONSTRUIDO. REST con timeout de 10 s (rest_timeout_s). El bucle de lectura usa recv con
timeout de 20 s (idle_ping_s, por debajo del corte de 30 s de OKX).

### C5. Reconexion sin martillear (backoff + jitter)
CONSTRUIDO. Backoff exponencial de 1 s a 60 s con jitter deterministico por hilo: tras un
corte, las conexiones no reintentan todas en el mismo instante (evita un DDoS involuntario
contra OKX justo cuando se recupera).

### C6. Keep-alive (ping/pong) propio de OKX
CONSTRUIDO. OKX corta si no llega dato en 30 s. El cliente, si no recibe nada en 20 s,
ENVIA el texto 'ping' y espera 'pong' (no es un ping de protocolo). Ademas se responde
'pong' a cualquier 'ping' entrante. Es una diferencia dura con Binance (que no usa este
mecanismo): copiar el barrido de Binance habria omitido este control.

### C7. TLS siempre verificado
CONSTRUIDO. WebSocket wss:// y REST https:// con ssl.create_default_context(); la
verificacion NO se desactiva jamas, ni para depurar. Sobre estos precios se disparan
reglas y, en M5, ordenes.

### C8. Cero secretos
CONSTRUIDO. Feed publico sin credenciales; el conector no acepta ninguna (si apareciera una
API key seria un error de capa: BYOC es P10a). No se registran payloads ni material
sensible.

### C9. Fault isolation por stream
CONSTRUIDO. Un mensaje malformado o un simbolo no representable no tumba el proceso
(translation_error + continue). Un hilo lector captura toda excepcion y reconecta: un
lector NO puede matar el proceso. El catalogo aplica ADR-006 (saltar y contar) por el
mecanismo generico de sync_catalog. El bootstrap tras reconexion es por-stream (un stream
fallido no tumba a los demas), orquestado por el motor.

## Diferencias con el barrido de Binance (por que NO se copia)
- Endpoint: /ws/v5/business (no /public); Binance usa host y ruta distintos.
- Suscripcion: por mensaje JSON tras conectar; Binance la lleva en la URL.
- Heartbeat: ping/pong de aplicacion con corte a 30 s; Binance no lo usa (corta a 24 h).
- Limite por conexion: 240 suscripciones (OKX) frente a 1024 streams (Binance).
- Sin tope publicado de conexiones concurrentes (OKX) frente a 300/IP/5min (Binance).

## CI hermetico vs validacion en caliente (regla 5.18)
El CI NO abre sockets. Lo que el CI prueba a fondo es lo separado de la red: symbols.py,
translate.py y pool.py (tests hermeticos con datos sinteticos). connector.py (el IO real)
NO se prueba en CI: se valida EN CALIENTE contra OKX real (streaming con provisionales y al
menos una cerrada, reconexion forzada con auto-bootstrap y dedup, y catalogo sincronizado).
La evidencia de esa validacion va en el informe de T-03.
