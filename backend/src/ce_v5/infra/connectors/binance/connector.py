"""Conector REAL de Binance Spot: feed publico (ADR-014). AQUI VIVE EL IO.

ESTE MODULO NO SE PRUEBA EN CI, Y ESO SE DECLARA (regla 5.18: la diferencia entre lo
que cubre el CI y lo que cubre la validacion en caliente se escribe, no se supone).
El CI es HERMETICO: ningun test abre un socket. Lo que el CI SI prueba a fondo es todo
lo que se pudo separar de la red -- la traduccion de mensajes (translate.py), el
reparto entre conexiones (pool.py) y los simbolos (symbols.py) --, que es donde de
verdad se puede meter un error de logica. Este fichero se valida EN CALIENTE (B12),
contra el Binance real, que es la unica forma honesta de probar que un socket funciona.

CERO SECRETOS. El feed publico de Binance no lleva credenciales y este conector no
acepta ninguna. Si alguna vez apareciese una API key por aqui, seria un ERROR DE CAPA:
las credenciales BYOC de exchange son P10a, viven cifradas, con su rol de DB y su
gate de politica. Un feed publico que pide una llave es un feed que no entendio para
que existe.

TLS SIEMPRE VERIFICADO: wss:// con ssl.create_default_context(). La verificacion NO se
desactiva nunca, ni "para depurar": un feed de precios sin verificar el certificado es
un feed que un intermediario puede reescribir, y sobre esos precios se disparan reglas
y, en M5, ordenes reales.
"""

from __future__ import annotations

import json
import queue
import ssl
import threading
import urllib.parse
import urllib.request
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from typing import Any

import websockets.sync.client as ws_client

from ce_v5.infra.connectors.binance.pool import BinanceLimits, ConnectionPlanner
from ce_v5.infra.connectors.binance.symbols import (
    to_native,
    to_orderbook_stream_name,
    to_stream_name,
    to_trade_stream_name,
)
from ce_v5.infra.connectors.binance.translate import (
    BinanceTranslationError,
    raw_candle_from_binance,
    raw_orderbook_delta_from_binance,
    raw_orderbook_seed_from_binance,
    raw_trade_from_binance,
    supported_binance_timeframes,
)
from source.families.market import (
    Instrument,
    LastSeenTrade,
    MarketDataKind,
    MarketStreamKey,
    RawCandle,
    RawOrderbookDelta,
    RawOrderbookSeed,
    RawTrade,
    Timeframe,
    TradeBackfillResult,
)

_WS_BASE = "wss://stream.binance.com:9443/stream"
_REST_BASE = "https://api.binance.com"
_MARKET_TYPE = "spot"
# Techo de /api/v3/trades SIN clave de API. NO es un parametro de configuracion: es lo
# que el endpoint publico da. De aqui sale la cota REAL del relleno tras una reconexion.
_REST_TRADES_MAX = 1000
# Profundidad de la foto REST del libro (/api/v3/depth). 100 basta para el top-K
# del frontier (25-50) y es ligera; los deltas @depth mantienen el top actualizado.
_DEPTH_LIMIT = 100


def _menor_event_time(trades: Sequence[RawTrade]) -> int | None:
    """El event_time mas antiguo del lote, o None si esta vacio."""
    return min((trade.event_time_ms for trade in trades), default=None)


def _coverage_binance(
    last_seen: LastSeenTrade, backfill: Sequence[RawTrade]
) -> tuple[bool, int | None, int | None]:
    """Decide si el relleno REST alcanzo lo que ya teniamos. PURA: sin red, sin reloj.

    Binance numera los trades de cada simbolo con un id MONOTONO, y eso es lo que
    permite responder la pregunta con exactitud en vez de con una estimacion temporal:
    si el trade mas antiguo del relleno es el SIGUIENTE al ultimo que ya teniamos (o uno
    anterior, es decir, hay solape), la serie quedo CONTIGUA y no falta nada. Si es
    posterior, entre medias hay trades que este relleno no alcanzo y que el endpoint
    publico ya no puede devolver: hueco real.

    Devuelve (covered, gap_from_event_time_ms, gap_to_event_time_ms). Con covered=True
    los limites van a None: no hay hueco que delimitar.

    FAIL-SAFE EN LOS TRES CAMINOS INCIERTOS -- ids no numericos, relleno vacio, o
    cualquier cosa que impida razonar sobre contiguidad -- se declara hueco. Declarar un
    hueco que no existe marca barras como incompletas sin motivo; NO declarar uno que si
    existe publica una barra a la que le faltan trades como si estuviera completa.
    """
    if last_seen.trade_id is None:
        # PRIMERA CONEXION: no hay nada persistido, luego no hay hueco posible. No se
        # puede haber perdido lo que nunca se tuvo.
        return True, None, None

    try:
        ultimo_id = int(last_seen.trade_id)
        ids = [int(trade.trade_id) for trade in backfill]
    except (TypeError, ValueError):
        # Un id que no es un entero rompe el razonamiento por contiguidad. No se
        # improvisa otro criterio: se declara hueco y se acota por event_time.
        return False, last_seen.event_time_ms, _menor_event_time(backfill)

    if not ids:
        # El REST no devolvio NADA con lo que acotar el extremo superior. Se declara el
        # hueco con ese extremo DESCONOCIDO (None) en vez de inventarle un limite: un
        # limite inventado es peor que un limite ausente.
        return False, last_seen.event_time_ms, None

    indice_mas_antiguo = min(range(len(ids)), key=lambda i: ids[i])
    if ids[indice_mas_antiguo] <= ultimo_id + 1:
        return True, None, None
    return (
        False,
        last_seen.event_time_ms,
        backfill[indice_mas_antiguo].event_time_ms,
    )


@dataclass(frozen=True, slots=True)
class BinanceConfig:
    """Parametros del conector. Ningun secreto: el feed publico no lleva credencial."""

    limits: BinanceLimits = field(default_factory=BinanceLimits)
    # Tope de la cola de mensajes ya traducidos. Si Binance manda mas rapido de lo que
    # el ingestor drena, se DESCARTA y se cuenta: nunca se crece sin limite en memoria
    # (una cola infinita no es resiliencia, es una bomba de relojeria).
    max_queue: int = 50_000
    rest_timeout_s: float = 10.0
    # Reconexion: backoff EXPONENCIAL CON JITTER. El exponencial evita martillear al
    # exchange; el JITTER evita que, tras un corte, TODAS las conexiones reintenten en
    # el mismo instante y le hagan un DDoS involuntario justo cuando se recupera.
    backoff_initial_s: float = 1.0
    backoff_max_s: float = 60.0
    backoff_jitter_s: float = 0.5


@dataclass(slots=True)
class ConnectorMetrics:
    """Observabilidad: sin esto, una cola que descarta es un agujero invisible."""

    dropped_full_queue: int = 0
    # Contador PROPIO para los trades: sumarlos al de velas ocultaria CUAL de los dos
    # flujos esta perdiendo datos, y son de escalas muy distintas (un par liquido
    # publica miles de trades por minuto y una vela por minuto).
    dropped_full_queue_trades: int = 0
    # Contador PROPIO del libro: los deltas @depth son el de mayor caudal (100 ms),
    # de otra escala que velas y trades; mezclarlo ocultaria cual pierde datos.
    dropped_full_queue_orderbook: int = 0
    translation_errors: int = 0
    reconnections: int = 0
    degraded_streams: set[str] = field(default_factory=set)


class BinanceSpotConnector:
    """Feed publico de Binance Spot. Cumple MarketDataSourcePort y TradeDataSourcePort
    por FORMA.

    NO importa platform: los dos puertos se satisfacen estructuralmente.

    VELAS Y TRADES VIAJAN POR LA MISMA CONEXION, multiplexados sobre el endpoint
    combinado (?streams=a/b/c). Abrir un socket aparte para los trades del mismo par
    gastaria el doble contra el limite de conexiones por IP -- el limite que un baneo
    hace cumplir -- sin ganar absolutamente nada: el mensaje ya viene etiquetado con su
    campo 'e', asi que separarlos es enrutar, no reconectar. Lo que SI esta separado es
    la COLA de cada clase: un pico de trades no puede desalojar velas, ni al reves.
    """

    def __init__(
        self,
        config: BinanceConfig | None = None,
        native_to_canonical: dict[str, str] | None = None,
    ) -> None:
        self._config = config or BinanceConfig()
        self._planner = ConnectionPlanner(self._config.limits)
        self._deseados: dict[str, MarketStreamKey] = {}
        # La resolucion nativo -> canonico NO SE CALCULA: se consulta. De 'BTCUSDT' no
        # se puede deducir donde parte (BTC-USDT o BT-CUSDT). El catalogo lo dice.
        self._native_to_canonical: dict[str, str] = native_to_canonical or {}
        self._cola: queue.Queue[RawCandle] = queue.Queue(maxsize=self._config.max_queue)
        # Cola SEPARADA para los trades, con el mismo tope y el mismo backpressure
        # observable. Compartir cola con las velas dejaria que una avalancha de trades
        # (miles por minuto en un par liquido) expulsase las velas, que son el dato
        # sobre el que se evaluan las reglas.
        self._cola_trades: queue.Queue[RawTrade] = queue.Queue(
            maxsize=self._config.max_queue
        )
        # Cola SEPARADA para el libro, con el mismo tope y backpressure observable. El
        # caudal de deltas del libro (100 ms) es de otra escala: compartir cola dejaria
        # que expulsase velas o trades.
        self._cola_orderbook: queue.Queue[RawOrderbookDelta] = queue.Queue(
            maxsize=self._config.max_queue
        )
        self._lectores: dict[int, threading.Thread] = {}
        self._parar = threading.Event()
        self._ssl = ssl.create_default_context()  # verificacion ON. No se toca.
        self.metrics = ConnectorMetrics()
        # Claves canonicas que RECONECTARON y aun no ha recogido el motor. Los lectores
        # (hilos) las escriben; el motor las lee en drain_reconnected. De ahi el lock.
        self._reconnected: set[str] = set()
        self._reconnected_lock = threading.Lock()
        # Conexion viva por indice de lector: la usa force_reconnect_all para cerrarlas
        # y forzar el ciclo de reconexion (primitiva de operacion y validacion en
        # caliente).
        self._conexiones: dict[int, Any] = {}

    # -- MarketDataSourcePort ------------------------------------------------

    def open(self, key: MarketStreamKey) -> None:
        """Marca el flujo como deseado y replanifica las conexiones."""
        self._deseados[key.as_stream_key()] = key
        self._replanificar()

    def close(self, key: MarketStreamKey) -> None:
        self._deseados.pop(key.as_stream_key(), None)
        self._replanificar()

    def active(self) -> AbstractSet[str]:
        return set(self._deseados)

    def poll(self, timeout_ms: int) -> Sequence[RawCandle]:
        """DRENA la cola. PULL con tope: manda el ingestor, no el exchange."""
        lote: list[RawCandle] = []
        try:
            primero = self._cola.get(timeout=timeout_ms / 1000.0)
        except queue.Empty:
            return lote
        lote.append(primero)
        while True:
            try:
                lote.append(self._cola.get_nowait())
            except queue.Empty:
                break
        return lote

    def fetch_recent(self, key: MarketStreamKey, limit: int) -> Sequence[RawCandle]:
        """BOOTSTRAP REST tras una reconexion (ADR-014): rellena el hueco.

        Devuelve datos TAMPOCO validados: el REST de un exchange no es mas confiable
        que su WebSocket. Los valida la misma frontera de confianza.
        """
        if key.timeframe is None:
            return []
        params = urllib.parse.urlencode(
            {
                "symbol": to_native(key.symbol),
                "interval": key.timeframe.value,
                "limit": max(1, min(limit, 1000)),
            }
        )
        datos = self._get_json(f"/api/v3/klines?{params}")
        if not isinstance(datos, list):
            return []
        velas: list[RawCandle] = []
        for fila in datos:
            traducida = self._kline_rest(fila, key)
            if traducida is not None:
                velas.append(traducida)
        return velas

    def list_instruments(self, market_type: str) -> Sequence[Instrument]:
        """Catalogo publico (GET /api/v3/exchangeInfo).

        De aqui sale native_symbol, que es lo que hace posible la resolucion
        nativo -> canonico sin adivinar.
        """
        if market_type != _MARKET_TYPE:
            return []
        datos = self._get_json("/api/v3/exchangeInfo")
        if not isinstance(datos, dict):
            return []
        simbolos = datos.get("symbols")
        if not isinstance(simbolos, list):
            return []
        instrumentos: list[Instrument] = []
        for entrada in simbolos:
            if not isinstance(entrada, dict):
                continue
            base = str(entrada.get("baseAsset", ""))
            quote = str(entrada.get("quoteAsset", ""))
            nativo = str(entrada.get("symbol", ""))
            if not base or not quote or not nativo:
                continue
            instrumentos.append(
                Instrument(
                    exchange="binance",
                    market_type=_MARKET_TYPE,
                    symbol=f"{base}-{quote}",
                    native_symbol=nativo,
                    active=entrada.get("status") == "TRADING",
                )
            )
        return instrumentos

    def supported_timeframes(self) -> frozenset[Timeframe]:
        return supported_binance_timeframes()

    # -- TradeDataSourcePort -------------------------------------------------
    #
    # open/close/active/drain_reconnected los comparte con el puerto de velas: son las
    # MISMAS suscripciones sobre la MISMA conexion, distinguidas por el data_kind de la
    # clave. Aqui solo estan los dos metodos propios de trades.

    def poll_trades(self, timeout_ms: int) -> Sequence[RawTrade]:
        """DRENA la cola de trades. Espejo exacto de poll(): PULL con tope, manda el
        motor y no el exchange. En trades importa aun mas, porque el caudal es de otro
        orden de magnitud.
        """
        lote: list[RawTrade] = []
        try:
            primero = self._cola_trades.get(timeout=timeout_ms / 1000.0)
        except queue.Empty:
            return lote
        lote.append(primero)
        while True:
            try:
                lote.append(self._cola_trades.get_nowait())
            except queue.Empty:
                break
        return lote

    def backfill_after_reconnect(
        self, key: MarketStreamKey, last_seen: LastSeenTrade
    ) -> TradeBackfillResult:
        """RELLENA el hueco de una reconexion por REST publico y DICE si lo cubrio.

        La cota NO es configurable: es _REST_TRADES_MAX (1000), el techo de
        /api/v3/trades SIN clave de API. Pedir mas no es cuestion de subir un numero,
        es que el endpoint publico no lo da.

        Datos TAMPOCO validados, igual que en velas: el REST de un exchange no es mas
        confiable que su WebSocket, y los valida la MISMA frontera de confianza. El
        solape con lo ya persistido lo absorbe el dedup por identidad natural.

        El IO vive aqui; la DECISION de cobertura vive en _coverage_binance, que es pura
        y se prueba en frio. Un calculo de cobertura enterrado en el camino de red seria
        un calculo que solo se puede probar contra el mercado real.
        """
        params = urllib.parse.urlencode(
            {"symbol": to_native(key.symbol), "limit": _REST_TRADES_MAX}
        )
        datos = self._get_json(f"/api/v3/trades?{params}")
        trades: list[RawTrade] = []
        if isinstance(datos, list):
            for fila in datos:
                traducido = self._trade_rest(fila, key)
                if traducido is not None:
                    trades.append(traducido)
        covered, gap_from, gap_to = _coverage_binance(last_seen, trades)
        return TradeBackfillResult(
            raw_trades=trades,
            covered=covered,
            gap_from_event_time_ms=gap_from,
            gap_to_event_time_ms=gap_to,
        )

    def _trade_rest(self, fila: object, key: MarketStreamKey) -> RawTrade | None:
        """Una fila de /api/v3/trades (objeto) -> RawTrade.

        FAULT ISOLATION POR FILA: una fila mala se cuenta y se SALTA. Perder un trade
        del bootstrap es infinitamente menos grave que perder los otros noventa y
        nueve porque el exchange colo uno con un campo raro.
        """
        if not isinstance(fila, dict):
            self.metrics.translation_errors += 1
            return None
        try:
            return RawTrade(
                exchange="binance",
                market_type=_MARKET_TYPE,
                symbol=key.symbol,
                trade_id=str(fila["id"]),
                # TEXTO TAL CUAL, como en el WebSocket.
                price=str(fila["price"]),
                qty=str(fila["qty"]),
                # isBuyerMaker: si el COMPRADOR fue el maker, quien cruzo el spread fue
                # el VENDEDOR. Mismo hecho que el flag 'm' del socket, otro nombre.
                aggressor_side="sell" if bool(fila["isBuyerMaker"]) else "buy",
                # 'time' es el instante del trade en el EXCHANGE (ADR-007).
                event_time_ms=int(fila["time"]),
                source_sequence=int(fila["id"]),
            )
        except (KeyError, TypeError, ValueError):
            self.metrics.translation_errors += 1
            return None

    # -- OrderbookDataSourcePort ---------------------------------------------
    #
    # open/close/active/drain_reconnected los comparte con velas y trades: las MISMAS
    # suscripciones sobre la MISMA conexion, distinguidas por el data_kind de la clave.
    # Aqui solo estan los dos metodos propios del libro.

    def poll_deltas(self, timeout_ms: int) -> Sequence[RawOrderbookDelta]:
        """DRENA la cola del libro. Espejo de poll()/poll_trades(): PULL con tope, manda
        el motor y no el exchange. En el libro importa aun mas: los deltas @depth llegan
        cada 100 ms, el caudal mas alto de las tres clases.
        """
        lote: list[RawOrderbookDelta] = []
        try:
            primero = self._cola_orderbook.get(timeout=timeout_ms / 1000.0)
        except queue.Empty:
            return lote
        lote.append(primero)
        while True:
            try:
                lote.append(self._cola_orderbook.get_nowait())
            except queue.Empty:
                break
        return lote

    def seed(self, key: MarketStreamKey) -> RawOrderbookSeed:
        """La FOTO de partida del libro por REST /api/v3/depth (ADR-014).

        Binance siembra el libro por REST (lastUpdateId + bids/asks) y encadena los
        deltas @depth del WS por U/u: el motor descarta los de u <= lastUpdateId y
        detecta el hueco por la continuidad. Datos NO validados, igual que el WS: los
        valida la MISMA frontera de confianza (el motor del libro). El IO vive aqui; la
        traduccion es pura (raw_orderbook_seed_from_binance) y se prueba en frio.
        """
        params = urllib.parse.urlencode(
            {"symbol": to_native(key.symbol), "limit": _DEPTH_LIMIT}
        )
        datos = self._get_json(f"/api/v3/depth?{params}")
        if not isinstance(datos, dict):
            msg = f"foto de libro de Binance con forma inesperada: {type(datos)!r}."
            raise BinanceTranslationError(msg)
        return raw_orderbook_seed_from_binance(datos, key.symbol, _MARKET_TYPE)

    # -- Cableado del catalogo (B6b) -----------------------------------------

    def set_symbol_map(self, instruments: Sequence[Instrument]) -> None:
        """Puebla la resolucion nativo -> canonico desde el catalogo (cierra B6b).

        El conector nace con el mapa VACIO a proposito: de 'BTCUSDT' NO se puede DEDUCIR
        donde parte (BTC-USDT o BT-CUSDT), hay que CONSULTARLO. El arranque sincroniza
        el catalogo y aqui le entrega esa resolucion, desde los MISMOS Instrument que ya
        trajo list_instruments (sin una segunda llamada de red). Sin este mapa, cada
        kline se contaria como translation_error (fault isolation, JAMAS en silencio) y
        el feed pareceria sano sin ingerir un solo dato.

        REEMPLAZA el mapa entero (no acumula): un resync refleja el catalogo VIGENTE. Se
        publica un dict NUEVO y se reasigna la referencia de una sola vez: los hilos
        lectores leen la referencia con .get(), asi que ven el mapa viejo o el nuevo
        completo, nunca uno a medias (no hace falta candado).
        """
        self._native_to_canonical = {
            instrument.native_symbol: instrument.symbol for instrument in instruments
        }

    # -- IO interno ----------------------------------------------------------

    def _nombre_de_stream(self, key: MarketStreamKey) -> str | None:
        """El nombre de stream de Binance de una clave deseada. None si no aplica.

        Es el UNICO sitio donde se decide como se llama un stream, y por eso lo usan
        tanto _replanificar (para suscribirse) como _key_for_stream_name (para resolver
        la vuelta al marcar reconexiones): si las dos direcciones no derivasen del mismo
        sitio, un dia dejarian de coincidir y las reconexiones marcarian el stream
        equivocado -- o ninguno.

        FOOTPRINT devuelve None a proposito: tiene timeframe, pero es dato DERIVADO que
        agregamos NOSOTROS, no un flujo que Binance publique. Suscribirse a el seria
        pedirle al exchange algo que no existe.
        """
        if key.data_kind is MarketDataKind.TRADES:
            return to_trade_stream_name(key.symbol)
        if key.data_kind is MarketDataKind.ORDERBOOK:
            return to_orderbook_stream_name(key.symbol)
        if key.data_kind is MarketDataKind.CANDLES and key.timeframe is not None:
            return to_stream_name(key.symbol, key.timeframe.value)
        return None

    def _replanificar(self) -> None:
        """Reparte los streams deseados y (re)arranca los lectores que hagan falta.

        Velas y trades entran en el MISMO reparto: ambos son nombres de stream que
        viajan por las mismas conexiones combinadas.
        """
        nombres = {
            nombre
            for key in self._deseados.values()
            if (nombre := self._nombre_de_stream(key)) is not None
        }
        plan = self._planner.assign(nombres)
        for indice, streams in plan.items():
            if indice not in self._lectores or not self._lectores[indice].is_alive():
                hilo = threading.Thread(
                    target=self._leer,
                    args=(indice, tuple(streams)),
                    name=f"binance-reader-{indice}",
                    daemon=True,
                )
                self._lectores[indice] = hilo
                hilo.start()

    def _leer(self, indice: int, streams: tuple[str, ...]) -> None:
        """Bucle lector de UNA conexion. Reconecta con backoff exponencial + jitter.

        El corte a las 24h que Binance aplica a toda conexion, y el aviso
        serverShutdown que manda 10 minutos antes, se tratan como una RECONEXION
        NORMAL: no son errores, son el comportamiento documentado del exchange.
        """
        espera = self._config.backoff_initial_s
        ya_conecto = False  # el PRIMER connect no es reconexion: no hubo hueco.
        while not self._parar.is_set():
            try:
                url = f"{_WS_BASE}?streams={'/'.join(streams)}"
                with ws_client.connect(url, ssl=self._ssl) as conexion:
                    self._conexiones[indice] = conexion
                    try:
                        espera = self._config.backoff_initial_s  # conecto: se resetea.
                        if ya_conecto:
                            # RECONEXION EXITOSA: hubo un hueco y se re-establecio la
                            # conexion. Aqui se cuenta y se marca (ver _registrar_
                            # reconexion). En el primer connect NO: no hubo hueco.
                            self._registrar_reconexion(streams)
                        ya_conecto = True
                        for mensaje in conexion:
                            if self._parar.is_set():
                                return
                            self._encolar(mensaje)
                    finally:
                        self._conexiones.pop(indice, None)
            except Exception:  # noqa: BLE001 - un lector NO puede matar el proceso.
                # El except es el DROP, no la reconexion: NO cuenta aqui (la reconexion
                # exitosa se cuenta arriba, al re-establecer). Solo aplica backoff.
                self._conexiones.pop(indice, None)
                # Backoff exponencial CON JITTER: sin el jitter, tras un corte todas
                # las conexiones reintentarian a la vez.
                jitter = self._jitter()
                self._parar.wait(
                    timeout=min(espera + jitter, self._config.backoff_max_s)
                )
                espera = min(espera * 2, self._config.backoff_max_s)

    def _jitter(self) -> float:
        """Ruido deterministico por hilo, sin random: basta con desincronizar."""
        return (threading.get_ident() % 1000) / 1000.0 * self._config.backoff_jitter_s

    def _key_for_stream_name(self, nombre: str) -> MarketStreamKey | None:
        """El MarketStreamKey deseado cuyo nombre de stream de Binance coincide con
        ``nombre`` (p.ej. 'btcusdt@kline_1m' o 'btcusdt@trade'). None si ninguno.
        PURO: sin red.

        Resuelve las DOS clases porque la reconexion afecta a las dos: una conexion
        multiplexada que se cae deja un hueco de velas Y un hueco de trades, y ambos
        streams tienen que quedar marcados para que sus motores rebootstrapeen.
        """
        for key in self._deseados.values():
            if self._nombre_de_stream(key) == nombre:
                return key
        return None

    def _registrar_reconexion(self, streams: tuple[str, ...]) -> None:
        """Contabiliza una reconexion EXITOSA y marca sus streams para el bootstrap.

        Se cuenta AQUI (al re-establecer la conexion), NO en el except del lector: el
        except es el DROP y puede dispararse varias veces por backoff antes de un
        reconnect exitoso, lo que inflaria la cuenta. Asi metrics.reconnections cuenta
        reconexiones REALES, sea por cierre limpio (force_reconnect_all, que sale del
        recv sin excepcion) o por error. Separado de _leer para probar el CONTADOR sin
        socket: el disparo real -- salir del recv y volver a conectar -- es el camino de
        red, que se valida en caliente (regla 5.18).
        """
        self.metrics.reconnections += 1
        self._marcar_reconectados(streams)

    def _marcar_reconectados(self, streams: tuple[str, ...]) -> None:
        """Marca (bajo lock) las claves canonicas de los streams que reconectaron.

        Los lectores (hilos) escriben aqui; el motor lee en drain_reconnected. Un stream
        cuyo nombre no resuelve a un deseado (raro) simplemente no se marca.
        """
        claves: set[str] = set()
        for nombre in streams:
            key = self._key_for_stream_name(nombre)
            if key is not None:
                claves.add(key.as_stream_key())
        if not claves:
            return
        with self._reconnected_lock:
            self._reconnected.update(claves)

    def drain_reconnected(self) -> AbstractSet[str]:
        """Devuelve (y limpia) las claves canonicas que reconectaron desde la ultima
        llamada. Bajo lock: los lectores escriben, el motor lee. Vacio en operacion
        normal.
        """
        with self._reconnected_lock:
            copia = set(self._reconnected)
            self._reconnected.clear()
        return copia

    def force_reconnect_all(self) -> int:
        """Cierra las conexiones vivas para FORZAR una reconexion (primitiva de
        operacion y de validacion en caliente). Los lectores saldran del recv,
        reconectaran con backoff y marcaran sus streams como reconectados -> el motor
        rebootstrapea. Cerrar es idempotente; el ``with`` tambien cerrara al salir. NO
        toca _deseados (no cambia lo deseado; solo fuerza el ciclo). Devuelve cuantas
        cerro.
        """
        cerradas = 0
        for conexion in list(self._conexiones.values()):
            try:
                conexion.close()
            except Exception:  # noqa: BLE001 - cerrar es best-effort; no debe lanzar.
                continue
            cerradas += 1
        return cerradas

    def _encolar(self, mensaje: str | bytes) -> None:
        """Traduce y encola, ENRUTANDO POR CLASE DE DATO. Cola llena: descarta y cuenta.

        Velas y trades llegan MEZCLADOS por la misma conexion y el campo 'e' dice cual
        es cual. Cualquier otro valor (serverShutdown y demas eventos de control) se
        ignora, exactamente como antes de que existieran los trades.
        """
        try:
            sobre = json.loads(mensaje)
        except json.JSONDecodeError:
            self.metrics.translation_errors += 1
            return
        if not isinstance(sobre, dict):
            self.metrics.translation_errors += 1
            return
        # El endpoint combinado envuelve el payload: {"stream": ..., "data": {...}}
        datos = sobre.get("data", sobre)
        if not isinstance(datos, dict):
            return
        evento = datos.get("e")
        if evento == "trade":
            self._encolar_trade(datos)
            return
        if evento == "depthUpdate":
            self._encolar_orderbook(datos)
            return
        if evento != "kline":
            return  # serverShutdown y demas eventos de control: no son velas.

        canonico = self._canonical_de(datos)
        if canonico is None:
            self.metrics.translation_errors += 1
            return
        try:
            vela = raw_candle_from_binance(datos, canonico, _MARKET_TYPE)
        except BinanceTranslationError:
            self.metrics.translation_errors += 1
            return

        try:
            self._cola.put_nowait(vela)
        except queue.Full:
            # BACKPRESSURE OBSERVABLE: se pierde el mensaje, pero NO en silencio. Una
            # cola que crece sin limite tumba el proceso; una que descarta sin contarlo
            # es un agujero invisible.
            self.metrics.dropped_full_queue += 1
            self.metrics.degraded_streams.add(canonico)

    def _encolar_trade(self, datos: dict[str, Any]) -> None:
        """Un mensaje @trade -> RawTrade en la cola de TRADES. Espejo del camino de
        velas: mismo canonico consultado (jamas deducido), misma conversion de la
        excepcion de traduccion en metrica observable, mismo backpressure con descarte
        contado.
        """
        canonico = self._canonical_de(datos)
        if canonico is None:
            self.metrics.translation_errors += 1
            return
        try:
            trade = raw_trade_from_binance(datos, canonico, _MARKET_TYPE)
        except BinanceTranslationError:
            self.metrics.translation_errors += 1
            return

        try:
            self._cola_trades.put_nowait(trade)
        except queue.Full:
            # BACKPRESSURE OBSERVABLE, con su contador propio (ver ConnectorMetrics).
            self.metrics.dropped_full_queue_trades += 1
            self.metrics.degraded_streams.add(canonico)

    def _encolar_orderbook(self, datos: dict[str, Any]) -> None:
        """Un depthUpdate -> RawOrderbookDelta a su cola. Espejo del camino de
        velas/trades: mismo canonico consultado (jamas deducido), misma conversion de la
        excepcion de traduccion en metrica observable, mismo backpressure con descarte
        contado. La foto de partida NO llega por aqui: la sirve seed() por REST.
        """
        canonico = self._canonical_de(datos)
        if canonico is None:
            self.metrics.translation_errors += 1
            return
        try:
            delta = raw_orderbook_delta_from_binance(datos, canonico, _MARKET_TYPE)
        except BinanceTranslationError:
            self.metrics.translation_errors += 1
            return
        try:
            self._cola_orderbook.put_nowait(delta)
        except queue.Full:
            self.metrics.dropped_full_queue_orderbook += 1
            self.metrics.degraded_streams.add(canonico)

    def _canonical_de(self, datos: dict[str, Any]) -> str | None:
        nativo = str(datos.get("s", ""))
        return self._native_to_canonical.get(nativo)

    def _kline_rest(self, fila: object, key: MarketStreamKey) -> RawCandle | None:
        """Una fila de /api/v3/klines (array) -> RawCandle."""
        if not isinstance(fila, list) or len(fila) < 7 or key.timeframe is None:
            self.metrics.translation_errors += 1
            return None
        try:
            return RawCandle(
                exchange="binance",
                market_type=_MARKET_TYPE,
                symbol=key.symbol,
                timeframe=key.timeframe.value,
                open_time_ms=int(fila[0]),
                close_time_ms=int(fila[6]),
                open=str(fila[1]),
                high=str(fila[2]),
                low=str(fila[3]),
                close=str(fila[4]),
                volume=str(fila[5]),
                # El REST solo devuelve velas YA CERRADAS del historico.
                is_closed=True,
                # El REST no trae event_time: el instante del hecho es el cierre de la
                # vela, que lo fija el ORIGEN igual (ADR-007). No se inventa con
                # nuestro reloj.
                event_time_ms=int(fila[6]),
            )
        except (TypeError, ValueError):
            self.metrics.translation_errors += 1
            return None

    def _get_json(self, path: str) -> object:
        peticion = urllib.request.Request(  # noqa: S310 - URL fija y https.
            f"{_REST_BASE}{path}",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(  # noqa: S310
            peticion, timeout=self._config.rest_timeout_s, context=self._ssl
        ) as respuesta:
            return json.loads(respuesta.read().decode())

    def shutdown(self) -> None:
        """Apagado ordenado: los lectores paran y se cierran las conexiones."""
        self._parar.set()
