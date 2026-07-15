# Barrido de linea base de seguridad - P07 (superficie EXCHANGE)

Regla 5.15: P07 abre una superficie externa nueva (los exchanges). Este barrido
recorre, control por control, la linea base de esa superficie. Cada control esta
CONSTRUIDO AHORA (con donde se verifica) o REGISTRADO con pieza duena / condicion
disparadora y justificacion (regla 5.11). Sin este barrido escrito no se cierra la
pieza.

Alcance de la superficie en P07: feed PUBLICO de un exchange real (Binance Spot,
elegido y justificado en el cierre) por WebSocket, mas su bootstrap REST y su
catalogo. El feed publico NO lleva credenciales: las credenciales BYOC son P10a y
la ejecucion P10b, fuera de esta superficie.

## 1. La entrada del exchange es ENTRADA NO CONFIABLE (ADR-006)
CONSTRUIDO. Ninguna vela cruda se convierte en hecho del sistema sin pasar la
frontera de confianza (platform/market/normalize.py). Alli se rechaza: precio no
finito (NaN/Infinity), precio no positivo, volumen negativo, rango OHLC incoherente,
vela desalineada con su intervalo, numero ilegible y vela cuyo estado de madurez no
concuerda con su tipo. Verificado en tests/unit/platform/market/test_normalize.py y,
end-to-end desde el formato real de Binance, en
tests/unit/infra/connectors/binance/test_translate.py
(test_una_vela_incoherente_de_binance_NO_entra).

## 2. Anti-suplantacion de flujo (una vela debe pertenecer al stream que se pidio)
CONSTRUIDO. Antes que nada, normalize compara exchange + market_type + symbol +
timeframe de la vela contra la clave suscrita; si no coinciden -> SYMBOL_MISMATCH y
la vela NO entra. Motivo: un exchange comprometido o un bug podria colar una vela de
OTRA moneda por el stream de BTC, y escribiriamos su precio en el historico ajeno.
Verificado en TestAntiSuplantacion de test_normalize.py y en el motor
(test_ingestor.py::test_una_vela_de_otro_simbolo_no_entra_ni_al_bus_ni_al_writer).

## 3. El feed publico no puede FABRICAR hechos ni tocar hechos ajenos (regla 5.20)
CONSTRUIDO Y VERIFICADO POR EL MOTOR. La API (expuesta a internet) NO puede escribir
market data; el ingestor NO puede tocar identidad, politica ni auditoria; nadie
reescribe el historico (append-only); la outbox del ingestor esta acotada por RLS a
los tres market.*. Rol ce_v5_ingestion estrecho, guardias de arranque bidireccionales
y check bloqueante tools/check_market_access.py. Pruebas negativas contra PostgreSQL
real en tests/integration/test_market_access.py.

## 4. TLS con verificacion de certificado, siempre
CONSTRUIDO. El connector real usa wss:// con ssl.create_default_context()
(verificacion de certificado ACTIVA); no existe ninguna via en el codigo para
desactivarla. Bootstrap REST por https. Declarado en
infra/connectors/binance/connector.py.

## 5. Limite de tamano de mensaje / recursos
CONSTRUIDO. El bus ya limita el tamano de mensaje aguas abajo. En el borde, poll()
tiene TOPE (max_batch, IngestionConfig): el ingestor lee por tandas acotadas, nunca
"todo lo que haya". Un mensaje anomalo se rechaza en normalize antes de tocar memoria
persistente. Verificado en TestBackpressure de test_ingestor.py.

## 6. Backpressure: quien manda es el ingestor, no el exchange
CONSTRUIDO. Modelo PULL con tope (poll + max_batch). Lo que no cabe en un ciclo espera
en el feed; no se crea cola infinita en memoria. Si el bus se degrada al publicar una
provisional, es FAIL-LOUD (propaga), no se traga. La cola interna del connector real
tiene maximo y cuenta los descartes como metrica observable.

## 7. Timeouts y heartbeat
CONSTRUIDO (parcial, con condicion declarada). El connector real respeta el ping/pong
de Binance (ping cada 20 s; desconexion si no hay pong en 1 min) y usa timeout en el
bootstrap REST. La CALIBRACION FINA de timeouts bajo carga real (ventanas, umbrales)
se ajusta en la validacion en caliente (B12) y se re-mide en T-03 por cada exchange
(cada uno tiene su propio heartbeat: Bybit usa 15 s, no 20).
FUENTE (verificado 2026-07-15, no de memoria): ping cada 20 s y desconexion si no hay
pong en 1 min; una conexion valida 24 h con aviso serverShutdown 10 min antes. Doc
oficial de spot (WebSocket streams):
https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md

## 8. Reconexion con backoff exponencial + jitter (no martillear al exchange)
CONSTRUIDO. El connector real reconecta con backoff exponencial y jitter (el jitter
desincroniza reconexiones simultaneas tras un corte). El corte de 24 h de Binance y su
evento serverShutdown se tratan como reconexion normal, no como error. Tras reconectar,
bootstrap REST rellena el hueco (ADR-014). La reconexion REAL se ejercita en caliente
(B12): un fake no se cae, por eso Central exigio un exchange real.

## 9. Respeto del rate limit publicado del exchange
CONSTRUIDO. El pool de conexiones respeta los limites publicados de Binance
(1024 streams/conexion; margen bajo las 300 conexiones/IP por 5 min) y reparte streams
de forma estable (un alta no reubica los vivos). Verificado en
tests/unit/infra/connectors/binance/test_pool.py. La cuenta fina de mensajes/segundo
(5 msg/s entrantes) se respeta al no reenviar controles innecesarios; se re-mide en
caliente.
FUENTE (verificado 2026-07-15, no de memoria): 5 mensajes entrantes/segundo por
conexion; 1024 streams por conexion; 300 conexiones por IP cada 5 min. Doc oficial de
spot (WebSocket streams):
https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md

NOTA (endpoint spot vs derivados): Binance retiro endpoints ANTIGUOS de DERIVADOS
(fstream) el 2026-04-23; el endpoint SPOT usado por P07
(wss://stream.binance.com:9443) sigue vigente, verificado 2026-07-15 contra la doc
oficial de spot. Referencia del aviso (derivados, NO spot):
https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Important-WebSocket-Change-Notice

## 10. Fault isolation por stream (un stream caido no tumba los demas)
CONSTRUIDO. En el motor, una vela corrupta se cuenta por su reason code y NO aborta el
lote: el resto de streams siguen. En el componente, una excepcion en un tick() se
captura, marca degradado y reintenta al siguiente ciclo sin matar el componente.
Verificado en TestAislamientoPorStream (test_ingestor.py) y TestFaultIsolation
(test_market_ingestor_public).

## 11. Cero secretos en la superficie publica y en logs
CONSTRUIDO. El connector de feed publico NO acepta ni guarda credenciales (esta escrito
que una API key aqui seria error de capa: es P10a). Los logs de ingesta registran
metricas y reason codes, nunca payloads crudos con material sensible. El feed publico
no tiene material sensible por definicion.

## 12. Aislamiento de lo PRIVADO (camino BYOC) por RLS + geo-gate
CONSTRUIDO (camino, con connector FAKE). El connector privado es por-usuario
(lifecycle_scope=user), gateado por politica/geo ANTES de INITIALIZE: sujeto sin
entitlement -> QUARANTINED sin abrir conexion; sujeto habilitado -> arranca. Los
intereses privados estan aislados por RLS y no aparecen en la demanda publica.
Verificado en tests/integration/test_private_connector_gated.py. Las CREDENCIALES
reales de exchange son P10a; la EJECUCION es P10b.

## 13. Integridad del historico: quien puede fabricar una vela
CONSTRUIDO. Solo ce_v5_ingestion inserta velas; la insercion y su evento van en la
MISMA transaccion (imposible divergencia); el historico es append-only para todos,
incluido el ingestor; una correccion no muta el original y numera su revision. La
provisional NO se persiste (la propia tabla lo prohibe con un CHECK). Verificado en
test_market_candles.py y test_market_access.py.

## 14. Cuota / fair-use y tope de cardinalidad por sujeto
CONSTRUIDO (tope tecnico) + REGISTRADO (cuota comercial). El tope TECNICO de intereses
por sujeto (MAX_INTENTS_PER_SUBJECT) impide que un usuario abra miles de streams (DoS);
verificado en test_registry.py (SUBJECT_LIMIT_EXCEEDED). La CUOTA COMERCIAL por plan es
P11 + el gate, no P07 (frontera de producto de Alvaro). El fair-use por exchange
(cuantas conexiones abrir contra un exchange en total) se dimensiona en operacion:
DUENO T-02 (baseline de despliegue), condicion disparadora = primer entorno
multi-replica o carga sostenida.

## 15. Sin dependencias nuevas
CONSTRUIDO. P07 no anade ninguna dependencia de terceros: el WebSocket usa websockets
(ya presente desde P06b) y el bootstrap REST usa urllib de la stdlib. Menos superficie
de cadena de suministro que auditar.

## LO NO CONSTRUIDO, CON DUENO O CONDICION (resumen)
- Calibracion fina de timeouts/heartbeat bajo carga real: se ajusta en B12 y se re-mide
  por exchange en T-03.
- Reconexion REAL bajo un corte real del exchange: se ejercita en la validacion en
  caliente B12 (obligatorio antes del cierre).
- Fair-use global de conexiones por exchange bajo carga sostenida: DUENO T-02.
- Segundo y tercer exchange (OKX, Bybit) y SU PROPIO barrido 5.15 (heartbeat, formato
  de vela, semantica de cierre y reconexion distintos): DUENO T-03, ANTES de P08. NO se
  copia este barrido: cada exchange se barre entero.
- Cuota comercial por plan: P11 + el gate (frontera de Alvaro).

Limites de Binance verificados el 2026-07-15 contra la documentacion oficial de spot
(URLs arriba), no de memoria (regla 5.15).

FIN del barrido P07. Ningun control queda sin construir o sin dueno/condicion.
