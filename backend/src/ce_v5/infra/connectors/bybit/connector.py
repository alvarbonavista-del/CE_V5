"""Conector REAL de Bybit v5 Spot: feed publico (ADR-014). AQUI VIVE EL IO.

NO SE PRUEBA EN CI (regla 5.18): el CI es hermetico. Lo que el CI prueba a fondo es lo
separado de la red: translate.py, pool.py y symbols.py. Este fichero se valida EN
CALIENTE contra Bybit real.

CERO SECRETOS (feed publico, BYOC es P10a). TLS SIEMPRE VERIFICADO.

DIFERENCIAS DE IO FRENTE A OKX/BINANCE (verificadas contra la doc vigente de Bybit):
- WS publico spot: wss://stream.bybit.com/v5/public/spot.
- Simbolo PEGADO (BTCUSDT): la vuelta nativo->canonico se CONSULTA (set_symbol_map,
  como Binance). Por eso implementa SymbolMapSink.
- Suscripcion por mensaje JSON tras conectar, en tandas de <=10 topics (limite de spot).
- Keep-alive: el cliente envia JSON {"op":"ping"} cada ~18 s (< 20 s), SIEMPRE, aunque
  fluya dato (Bybit lo exige). El pong del servidor se ignora.
- La vela WS es un OBJETO con campos nombrados; el REST es un array. Dos traducciones.
- REST envuelto en {"retCode":0,"result":{"list":[...]}}, newest-first.
"""

from __future__ import annotations

import json
import queue
import ssl
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from typing import Any

import websockets.sync.client as ws_client

from ce_v5.infra.connectors.bybit.pool import BybitLimits, ConnectionPlanner
from ce_v5.infra.connectors.bybit.symbols import (
    TimeframeTranslationError,
    timeframe_from_interval,
    to_interval,
    to_native,
    to_topic,
    to_trade_topic,
)
from ce_v5.infra.connectors.bybit.translate import (
    BybitTranslationError,
    raw_candle_from_bybit_rest,
    raw_candle_from_bybit_ws,
    raw_trade_from_bybit_rest,
    raw_trade_from_bybit_ws,
    supported_bybit_timeframes,
)
from source.families.market import (
    Instrument,
    LastSeenTrade,
    MarketDataKind,
    MarketStreamKey,
    RawCandle,
    RawTrade,
    Timeframe,
    TradeBackfillResult,
)

_WS_BASE = "wss://stream.bybit.com/v5/public/spot"
_REST_BASE = "https://api.bybit.com"
_MARKET_TYPE = "spot"
_USER_AGENT = "Mozilla/5.0 (compatible; ce-v5-market-data/0.1)"
_PING = json.dumps({"op": "ping"})
# Bybit spot: hasta 10 args por peticion de suscripcion.
_MAX_ARGS_PER_SUB = 10
# Prefijo del topic de trades: velas y trades comparten conexion y se separan por aqui.
_TRADES_TOPIC_PREFIX = "publicTrade."
# Techo REAL de recent-trade SIN clave. NO es config: es lo que el endpoint da. El
# sondeo en vivo vio que Bybit spot lo CAPA A 60 EN SILENCIO (pides 1000 -> 60 con
# retCode=0). NO pagina: una sola llamada. Por eso su ventana de relleno es pequena y un
# hueco de mas de ~60 trades queda descubierto (fail-safe FRECUENTE, no un bug).
_REST_TRADES_MAX = 60


def _menor_event_time(trades: Sequence[RawTrade]) -> int | None:
    """El event_time mas antiguo del lote, o None si esta vacio."""
    return min((trade.event_time_ms for trade in trades), default=None)


def _coverage_bybit(
    last_seen: LastSeenTrade, backfill: Sequence[RawTrade]
) -> tuple[bool, int | None, int | None]:
    """Decide si el relleno REST alcanzo lo que ya teniamos. PURA: sin red, sin reloj.

    IGUAL EN FORMA a _coverage_binance/_coverage_okx: el tradeId de Bybit es un id
    ENTERO monotono y contiguo por instrumento (verificado en el sondeo), y el id del WS
    ('i') y el del REST ('execId') son el MISMO espacio, asi que se razona por
    CONTIGUIDAD. Si el trade mas antiguo del relleno es el SIGUIENTE al ultimo que
    teniamos (o anterior: hay solape), la serie es contigua. Si no, falta dato: hueco.

    EN BYBIT ESTE FAIL-SAFE ES FRECUENTE, no excepcional: recent-trade solo da ~60
    trades y no pagina, asi que un corte de mas de 60 trades deja hueco. Es lo esperado.

    FAIL-SAFE en los tres caminos inciertos (ids no numericos, relleno vacio, o lo que
    impida razonar): se declara hueco. Marcar barras incompletas de mas es feo; NO
    declarar un hueco real publica una barra a la que le faltan trades como completa.
    """
    if last_seen.trade_id is None:
        # PRIMERA CONEXION: no hay nada persistido, luego no hay hueco posible.
        return True, None, None

    try:
        ultimo_id = int(last_seen.trade_id)
        ids = [int(trade.trade_id) for trade in backfill]
    except (TypeError, ValueError):
        return False, last_seen.event_time_ms, _menor_event_time(backfill)

    if not ids:
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
class BybitConfig:
    """Parametros del conector. Ningun secreto: el feed publico no lleva credencial."""

    limits: BybitLimits = field(default_factory=BybitLimits)
    max_queue: int = 50_000
    rest_timeout_s: float = 10.0
    # Bybit exige un ping cada 20 s: se manda cada 18 s (SIEMPRE, aunque fluya dato).
    ping_interval_s: float = 18.0
    recv_timeout_s: float = 5.0
    backoff_initial_s: float = 1.0
    backoff_max_s: float = 60.0
    backoff_jitter_s: float = 0.5


@dataclass(slots=True)
class ConnectorMetrics:
    """Observabilidad: sin esto, una cola que descarta es un agujero invisible."""

    dropped_full_queue: int = 0
    # Contador PROPIO de los trades: sumarlo al de velas ocultaria CUAL de los dos
    # flujos pierde datos, y son de escalas muy distintas (miles de trades por minuto vs
    # vela por minuto).
    dropped_full_queue_trades: int = 0
    translation_errors: int = 0
    reconnections: int = 0
    degraded_streams: set[str] = field(default_factory=set)


class BybitSpotConnector:
    """Feed publico de Bybit v5 Spot. Cumple MarketDataSourcePort y TradeDataSourcePort
    por FORMA.

    Implementa SymbolMapSink (set_symbol_map): en Bybit el simbolo va PEGADO y la vuelta
    nativo->canonico se consulta al catalogo, como en Binance. El MISMO mapa sirve a
    velas y a trades.

    VELAS Y TRADES VIAJAN POR LA MISMA CONEXION spot y se separan por el PREFIJO del
    topic ('kline.' vs 'publicTrade.'). Lo SEPARADO es la COLA de cada clase:
    un pico de trades no puede desalojar velas, ni al reves.
    """

    def __init__(
        self,
        config: BybitConfig | None = None,
        native_to_canonical: dict[str, str] | None = None,
    ) -> None:
        self._config = config or BybitConfig()
        self._planner = ConnectionPlanner(self._config.limits)
        self._deseados: dict[str, MarketStreamKey] = {}
        self._native_to_canonical: dict[str, str] = native_to_canonical or {}
        self._cola: queue.Queue[RawCandle] = queue.Queue(maxsize=self._config.max_queue)
        # Cola SEPARADA para los trades, con el mismo tope y el mismo backpressure
        # observable. Compartirla con las velas dejaria que una avalancha de trades
        # expulsase las velas, que son el dato sobre el que se evaluan las reglas.
        self._cola_trades: queue.Queue[RawTrade] = queue.Queue(
            maxsize=self._config.max_queue
        )
        self._lectores: dict[int, threading.Thread] = {}
        self._parar = threading.Event()
        self._ssl = ssl.create_default_context()
        self.metrics = ConnectorMetrics()
        self._reconnected: set[str] = set()
        self._reconnected_lock = threading.Lock()
        self._conexiones: dict[int, Any] = {}

    # -- MarketDataSourcePort ------------------------------------------------

    def open(self, key: MarketStreamKey) -> None:
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
            lote.append(self._cola.get(timeout=timeout_ms / 1000.0))
        except queue.Empty:
            return lote
        while True:
            try:
                lote.append(self._cola.get_nowait())
            except queue.Empty:
                break
        return lote

    def fetch_recent(self, key: MarketStreamKey, limit: int) -> Sequence[RawCandle]:
        """BOOTSTRAP REST tras reconexion (ADR-014): rellena el hueco de cerradas.

        Bybit devuelve newest-first: se invierte. El REST son velas historicas
        (cerradas). Datos no validados: los valida la frontera de confianza.
        """
        if key.timeframe is None:
            return []
        params = urllib.parse.urlencode(
            {
                "category": "spot",
                "symbol": to_native(key.symbol),
                "interval": to_interval(key.timeframe.value),
                "limit": max(1, min(limit, 1000)),
            }
        )
        filas = self._lista(self._get_json(f"/v5/market/kline?{params}"))
        if filas is None:
            return []
        velas: list[RawCandle] = []
        for fila in reversed(filas):
            try:
                vela = raw_candle_from_bybit_rest(
                    fila, key.symbol, _MARKET_TYPE, key.timeframe.value
                )
            except BybitTranslationError:
                self.metrics.translation_errors += 1
                continue
            velas.append(vela)
        return velas

    def list_instruments(self, market_type: str) -> Sequence[Instrument]:
        """Catalogo publico (GET /v5/market/instruments-info?category=spot).

        De aqui sale native_symbol: hace posible la resolucion nativo->canonico sin
        adivinar (Bybit usa el simbolo pegado, como Binance).
        """
        if market_type != _MARKET_TYPE:
            return []
        filas = self._lista(self._get_json("/v5/market/instruments-info?category=spot"))
        if filas is None:
            return []
        instrumentos: list[Instrument] = []
        for entrada in filas:
            if not isinstance(entrada, dict):
                continue
            nativo = str(entrada.get("symbol", ""))
            base = str(entrada.get("baseCoin", ""))
            quote = str(entrada.get("quoteCoin", ""))
            if not nativo or not base or not quote:
                continue
            instrumentos.append(
                Instrument(
                    exchange="bybit",
                    market_type=_MARKET_TYPE,
                    symbol=f"{base}-{quote}",
                    native_symbol=nativo,
                    active=entrada.get("status") == "Trading",
                )
            )
        return instrumentos

    def supported_timeframes(self) -> frozenset[Timeframe]:
        return supported_bybit_timeframes()

    # -- TradeDataSourcePort -------------------------------------------------
    #
    # open/close/active/drain_reconnected los comparte con el puerto de velas: son las
    # MISMAS suscripciones sobre la MISMA conexion, distinguidas por el data_kind. Aqui
    # solo estan los dos metodos propios de trades.

    def poll_trades(self, timeout_ms: int) -> Sequence[RawTrade]:
        """DRENA la cola de trades. Espejo de poll(): PULL con tope, manda el motor y no
        el exchange. En trades importa aun mas: el caudal es de otro orden de magnitud.
        """
        lote: list[RawTrade] = []
        try:
            lote.append(self._cola_trades.get(timeout=timeout_ms / 1000.0))
        except queue.Empty:
            return lote
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

        UNA SOLA LLAMADA a recent-trade: Bybit NO pagina y capa a _REST_TRADES_MAX (60)
        EN SILENCIO. Se pide ese techo y no se asume nada mas alla de lo que la
        respuesta trajo. La ventana es pequena a proposito del exchange, no nuestra: por
        eso un corte de mas de ~60 trades queda descubierto (covered=False), lo cual
        es el camino COMUN, no un fallo.

        Datos NO validados, igual que en velas: el REST no es mas confiable que el
        socket y los valida la MISMA frontera. El solape ya persistido lo absorbe el
        dedup por identidad natural. La DECISION de cobertura vive en _coverage_bybit.
        """
        params = urllib.parse.urlencode(
            {
                "category": "spot",
                "symbol": to_native(key.symbol),
                "limit": _REST_TRADES_MAX,
            }
        )
        filas = self._lista(self._get_json(f"/v5/market/recent-trade?{params}"))
        trades: list[RawTrade] = []
        if filas is not None:
            for fila in filas:
                try:
                    trades.append(
                        raw_trade_from_bybit_rest(fila, key.symbol, _MARKET_TYPE)
                    )
                except BybitTranslationError:
                    self.metrics.translation_errors += 1
        covered, gap_from, gap_to = _coverage_bybit(last_seen, trades)
        return TradeBackfillResult(
            raw_trades=trades,
            covered=covered,
            gap_from_event_time_ms=gap_from,
            gap_to_event_time_ms=gap_to,
        )

    def drain_reconnected(self) -> AbstractSet[str]:
        """Devuelve (y limpia) las claves canonicas que reconectaron. Bajo lock."""
        with self._reconnected_lock:
            copia = set(self._reconnected)
            self._reconnected.clear()
        return copia

    # -- Cableado del catalogo (B6b): capacidad SymbolMapSink ----------------

    def set_symbol_map(self, instruments: Sequence[Instrument]) -> None:
        """Puebla la resolucion nativo->canonico desde el catalogo, como Binance.

        Bybit usa el simbolo pegado (BTCUSDT): de ahi NO se deduce donde parte. El
        arranque sincroniza el catalogo y aqui entrega esa resolucion. REEMPLAZA el
        mapa entero: un resync refleja el catalogo vigente.
        """
        self._native_to_canonical = {
            inst.native_symbol: inst.symbol for inst in instruments
        }

    # -- Operacion / validacion en caliente ----------------------------------

    def force_reconnect_all(self) -> int:
        """Cierra las conexiones vivas para forzar reconexion. Devuelve cuantas."""
        cerradas = 0
        for conexion in list(self._conexiones.values()):
            try:
                conexion.close()
            except Exception:  # noqa: BLE001 - cerrar es best-effort; no debe lanzar.
                continue
            cerradas += 1
        return cerradas

    def shutdown(self) -> None:
        """Apagado ordenado: los lectores paran en su proximo ciclo de recv."""
        self._parar.set()

    # -- IO interno ----------------------------------------------------------

    def _es_suscribible(self, key: MarketStreamKey) -> bool:
        """Una clave que ESTE connector suscribe: velas (con timeframe) o trades.

        FOOTPRINT queda fuera: tiene timeframe, pero es dato DERIVADO que agregamos
        NOSOTROS, no un flujo que Bybit publique. Es el UNICO sitio que decide que es
        suscribible; lo usan _replanificar, _leer y _registrar_reconexion sin divergir.
        """
        if key.data_kind is MarketDataKind.CANDLES:
            return key.timeframe is not None
        return key.data_kind is MarketDataKind.TRADES

    def _topics_de(self, keys: tuple[MarketStreamKey, ...]) -> list[str]:
        """Los topics de suscripcion de una conexion: velas (kline.) Y trades
        (publicTrade.), que comparten conexion y se separan por el prefijo del topic.
        """
        topics: list[str] = []
        for k in keys:
            if k.data_kind is MarketDataKind.TRADES:
                topics.append(to_trade_topic(k.symbol))
            elif k.timeframe is not None:
                topics.append(to_topic(k.symbol, k.timeframe.value))
        return topics

    def _replanificar(self) -> None:
        claves = {
            key.as_stream_key()
            for key in self._deseados.values()
            if self._es_suscribible(key)
        }
        plan = self._planner.assign(claves)
        for indice, stream_keys in plan.items():
            if indice not in self._lectores or not self._lectores[indice].is_alive():
                keys = tuple(
                    self._deseados[sk] for sk in stream_keys if sk in self._deseados
                )
                hilo = threading.Thread(
                    target=self._leer,
                    args=(indice, keys),
                    name=f"bybit-reader-{indice}",
                    daemon=True,
                )
                self._lectores[indice] = hilo
                hilo.start()

    def _leer(self, indice: int, keys: tuple[MarketStreamKey, ...]) -> None:
        """Lector de UNA conexion: suscribe al conectar y reconecta con backoff."""
        espera = self._config.backoff_initial_s
        ya_conecto = False
        topics = self._topics_de(keys)
        while not self._parar.is_set():
            try:
                with ws_client.connect(
                    _WS_BASE, ssl=self._ssl, user_agent_header=_USER_AGENT
                ) as conexion:
                    self._conexiones[indice] = conexion
                    try:
                        espera = self._config.backoff_initial_s
                        self._suscribir(conexion, topics)
                        if ya_conecto:
                            self._registrar_reconexion(keys)
                        ya_conecto = True
                        self._bucle_recv(conexion)
                    finally:
                        self._conexiones.pop(indice, None)
            except Exception:  # noqa: BLE001 - un lector NO puede matar el proceso.
                self._conexiones.pop(indice, None)
                jitter = self._jitter()
                self._parar.wait(
                    timeout=min(espera + jitter, self._config.backoff_max_s)
                )
                espera = min(espera * 2, self._config.backoff_max_s)

    def _suscribir(self, conexion: Any, topics: list[str]) -> None:
        """Suscribe en tandas de <=10 args (limite de spot de Bybit)."""
        for i in range(0, len(topics), _MAX_ARGS_PER_SUB):
            tanda = topics[i : i + _MAX_ARGS_PER_SUB]
            conexion.send(json.dumps({"op": "subscribe", "args": tanda}))

    def _bucle_recv(self, conexion: Any) -> None:
        """Lee mensajes y manda {"op":"ping"} cada ping_interval_s, SIEMPRE (Bybit)."""
        ultimo_ping = time.monotonic()
        while not self._parar.is_set():
            if time.monotonic() - ultimo_ping >= self._config.ping_interval_s:
                conexion.send(_PING)
                ultimo_ping = time.monotonic()
            try:
                mensaje = conexion.recv(timeout=self._config.recv_timeout_s)
            except TimeoutError:
                continue
            self._encolar(mensaje.decode() if isinstance(mensaje, bytes) else mensaje)

    def _encolar(self, mensaje: str) -> None:
        """Traduce y encola. Los mensajes de control (op/success) se ignoran."""
        try:
            sobre = json.loads(mensaje)
        except json.JSONDecodeError:
            self.metrics.translation_errors += 1
            return
        if not isinstance(sobre, dict):
            self.metrics.translation_errors += 1
            return
        if "op" in sobre or "success" in sobre:
            # Ack de subscribe, pong, etc. Un fallo declarado se cuenta.
            if sobre.get("success") is False:
                self.metrics.translation_errors += 1
            return
        topic = sobre.get("topic")
        datos = sobre.get("data")
        if not isinstance(topic, str) or not isinstance(datos, list):
            return
        if topic.startswith(_TRADES_TOPIC_PREFIX):
            self._encolar_trades(topic, datos)
            return
        if not topic.startswith("kline."):
            return
        partes = topic.split(".", 2)
        if len(partes) != 3:
            self.metrics.translation_errors += 1
            return
        canonico = self._native_to_canonical.get(partes[2])
        if canonico is None:
            self.metrics.translation_errors += 1
            return
        try:
            timeframe = timeframe_from_interval(partes[1])
        except TimeframeTranslationError:
            self.metrics.translation_errors += 1
            return
        for obj in datos:
            try:
                vela = raw_candle_from_bybit_ws(obj, canonico, _MARKET_TYPE, timeframe)
            except BybitTranslationError:
                self.metrics.translation_errors += 1
                continue
            try:
                self._cola.put_nowait(vela)
            except queue.Full:
                self.metrics.dropped_full_queue += 1
                self.metrics.degraded_streams.add(canonico)

    def _encolar_trades(self, topic: str, datos: list[Any]) -> None:
        """Un mensaje de 'publicTrade' -> RawTrade(s) en la cola de TRADES. Espejo del
        camino de velas: mismo canonico CONSULTADO al mapa (jamas deducido), misma
        conversion de la excepcion de traduccion en metrica, mismo backpressure contado.
        """
        native = topic[len(_TRADES_TOPIC_PREFIX) :]
        canonico = self._native_to_canonical.get(native)
        if canonico is None:
            self.metrics.translation_errors += 1
            return
        for obj in datos:
            try:
                trade = raw_trade_from_bybit_ws(obj, canonico, _MARKET_TYPE)
            except BybitTranslationError:
                self.metrics.translation_errors += 1
                continue
            try:
                self._cola_trades.put_nowait(trade)
            except queue.Full:
                # BACKPRESSURE OBSERVABLE, con su contador propio (ConnectorMetrics).
                self.metrics.dropped_full_queue_trades += 1
                self.metrics.degraded_streams.add(canonico)

    def _registrar_reconexion(self, keys: tuple[MarketStreamKey, ...]) -> None:
        """Cuenta una reconexion EXITOSA y marca sus claves para el bootstrap.

        Se cuenta al re-establecer (no en el except, que es el DROP y puede repetirse
        por backoff). Marca las claves canonicas directamente.

        Marca TODAS las claves suscribibles -- velas Y trades --: una conexion
        multiplexada que se cae deja hueco de las dos clases, y cada motor filtra de
        drain_reconnected lo que le toca.
        """
        self.metrics.reconnections += 1
        claves = {k.as_stream_key() for k in keys if self._es_suscribible(k)}
        if claves:
            with self._reconnected_lock:
                self._reconnected.update(claves)

    def _jitter(self) -> float:
        """Ruido deterministico por hilo, sin random: basta con desincronizar."""
        return (threading.get_ident() % 1000) / 1000.0 * self._config.backoff_jitter_s

    def _lista(self, respuesta: object) -> list[Any] | None:
        """Extrae result.list de una respuesta REST de Bybit si retCode==0."""
        if not isinstance(respuesta, dict) or respuesta.get("retCode") != 0:
            return None
        result = respuesta.get("result")
        if not isinstance(result, dict):
            return None
        lista = result.get("list")
        return lista if isinstance(lista, list) else None

    def _get_json(self, path: str) -> object:
        peticion = urllib.request.Request(  # noqa: S310 - URL fija y https.
            f"{_REST_BASE}{path}",
            headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
        )
        with urllib.request.urlopen(  # noqa: S310
            peticion, timeout=self._config.rest_timeout_s, context=self._ssl
        ) as respuesta:
            return json.loads(respuesta.read().decode())
