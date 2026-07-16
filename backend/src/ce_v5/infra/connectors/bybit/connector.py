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
)
from ce_v5.infra.connectors.bybit.translate import (
    BybitTranslationError,
    raw_candle_from_bybit_rest,
    raw_candle_from_bybit_ws,
    supported_bybit_timeframes,
)
from source.families.market import Instrument, MarketStreamKey, RawCandle, Timeframe

_WS_BASE = "wss://stream.bybit.com/v5/public/spot"
_REST_BASE = "https://api.bybit.com"
_MARKET_TYPE = "spot"
_USER_AGENT = "Mozilla/5.0 (compatible; ce-v5-market-data/0.1)"
_PING = json.dumps({"op": "ping"})
# Bybit spot: hasta 10 args por peticion de suscripcion.
_MAX_ARGS_PER_SUB = 10


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
    translation_errors: int = 0
    reconnections: int = 0
    degraded_streams: set[str] = field(default_factory=set)


class BybitSpotConnector:
    """Feed publico de Bybit v5 Spot. Cumple MarketDataSourcePort por FORMA.

    Implementa SymbolMapSink (set_symbol_map): en Bybit el simbolo va PEGADO y la vuelta
    nativo->canonico se consulta al catalogo, como en Binance.
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

    def _replanificar(self) -> None:
        claves = {
            key.as_stream_key()
            for key in self._deseados.values()
            if key.timeframe is not None
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
        topics = [
            to_topic(k.symbol, k.timeframe.value)
            for k in keys
            if k.timeframe is not None
        ]
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

    def _registrar_reconexion(self, keys: tuple[MarketStreamKey, ...]) -> None:
        """Cuenta una reconexion EXITOSA y marca sus claves para el bootstrap.

        Se cuenta al re-establecer (no en el except, que es el DROP y puede repetirse
        por backoff). Marca las claves canonicas directamente.
        """
        self.metrics.reconnections += 1
        claves = {k.as_stream_key() for k in keys if k.timeframe is not None}
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
