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
from ce_v5.infra.connectors.binance.symbols import to_native, to_stream_name
from ce_v5.infra.connectors.binance.translate import (
    BinanceTranslationError,
    raw_candle_from_binance,
    supported_binance_timeframes,
)
from source.families.market import (
    Instrument,
    MarketStreamKey,
    RawCandle,
    Timeframe,
)

_WS_BASE = "wss://stream.binance.com:9443/stream"
_REST_BASE = "https://api.binance.com"
_MARKET_TYPE = "spot"


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
    translation_errors: int = 0
    reconnections: int = 0
    degraded_streams: set[str] = field(default_factory=set)


class BinanceSpotConnector:
    """Feed publico de Binance Spot. Cumple MarketDataSourcePort por FORMA.

    NO importa platform: el puerto se satisface estructuralmente.
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

    def _replanificar(self) -> None:
        """Reparte los streams deseados y (re)arranca los lectores que hagan falta."""
        nombres = {
            to_stream_name(key.symbol, key.timeframe.value)
            for key in self._deseados.values()
            if key.timeframe is not None
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
        ``nombre`` (p.ej. 'btcusdt@kline_1m'). None si ninguno. PURO: sin red.
        """
        for key in self._deseados.values():
            if key.timeframe is None:
                continue
            if to_stream_name(key.symbol, key.timeframe.value) == nombre:
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
        """Traduce y encola. Si la cola esta llena, DESCARTA Y CUENTA."""
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
        if not isinstance(datos, dict) or datos.get("e") != "kline":
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
