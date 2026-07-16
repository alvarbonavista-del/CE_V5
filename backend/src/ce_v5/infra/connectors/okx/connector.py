"""Conector REAL de OKX Spot: feed publico (ADR-014). AQUI VIVE EL IO.

NO SE PRUEBA EN CI (regla 5.18): el CI es hermetico, ningun test abre un socket. Lo que
el CI prueba a fondo es lo separado de la red: translate.py, pool.py y symbols.py. Este
fichero se valida EN CALIENTE contra OKX real.

CERO SECRETOS: el feed publico de OKX no lleva credenciales y este conector no acepta
ninguna (las claves BYOC son P10a). TLS SIEMPRE VERIFICADO (ssl.create_default_context).

DIFERENCIAS DE IO FRENTE A BINANCE (verificadas contra la doc vigente de OKX):
- Endpoint /ws/v5/business (las velas se movieron ahi el 20-jun-2023), no /public.
- La suscripcion NO va en la URL: tras conectar se ENVIA
  {"op":"subscribe","args":[{"channel":"candle1m","instId":"BTC-USDT"},...]}.
- Keep-alive de aplicacion: OKX corta si no hay dato en 30 s. El cliente, si no recibe
  nada en N<30 s, ENVIA el texto 'ping' y espera 'pong'. Tambien respondemos 'pong' a
  un 'ping' que llegue. No es un ping de protocolo.
- El instId ya es canonico (identidad): sin mapa nativo->canonico, sin set_symbol_map.
- El array REST de velas es IGUAL al de WS: se reutiliza raw_candle_from_okx. El REST
  llega NEWEST-FIRST (se invierte) y envuelto en {"code","data":[...]}; la vela viva
  (confirm='0') se descarta en el bootstrap (llega por WS).
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

from ce_v5.infra.connectors.okx.pool import ConnectionPlanner, OkxLimits
from ce_v5.infra.connectors.okx.symbols import (
    SymbolTranslationError,
    TimeframeTranslationError,
    timeframe_from_channel,
    to_bar,
    to_channel,
    to_native,
)
from ce_v5.infra.connectors.okx.translate import (
    OkxTranslationError,
    raw_candle_from_okx,
    supported_okx_timeframes,
)
from source.families.market import Instrument, MarketStreamKey, RawCandle, Timeframe

_WS_BASE = "wss://ws.okx.com:8443/ws/v5/business"
_REST_BASE = "https://www.okx.com"
_MARKET_TYPE = "spot"
_PING = "ping"
_PONG = "pong"
# OKX esta tras Cloudflare y rechaza con 403 el User-Agent por defecto de urllib.
_USER_AGENT = "Mozilla/5.0 (compatible; ce-v5-market-data/0.1)"


@dataclass(frozen=True, slots=True)
class OkxConfig:
    """Parametros del conector. Ningun secreto: el feed publico no lleva credencial."""

    limits: OkxLimits = field(default_factory=OkxLimits)
    max_queue: int = 50_000
    rest_timeout_s: float = 10.0
    # Ping de inactividad: si en idle_ping_s no llega nada, mandamos 'ping'. Debe ser
    # < 30 s (el corte de OKX). El backoff de reconexion es exponencial con jitter.
    idle_ping_s: float = 20.0
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


class OkxSpotConnector:
    """Feed publico de OKX Spot. Cumple MarketDataSourcePort por FORMA.

    NO importa platform: el puerto se satisface estructuralmente. NO implementa
    SymbolMapSink: en OKX el instId ya es canonico, no hace falta mapa.
    """

    def __init__(self, config: OkxConfig | None = None) -> None:
        self._config = config or OkxConfig()
        self._planner = ConnectionPlanner(self._config.limits)
        self._deseados: dict[str, MarketStreamKey] = {}
        self._cola: queue.Queue[RawCandle] = queue.Queue(maxsize=self._config.max_queue)
        self._lectores: dict[int, threading.Thread] = {}
        self._parar = threading.Event()
        self._ssl = ssl.create_default_context()  # verificacion ON. No se toca.
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
        """BOOTSTRAP REST tras reconexion (ADR-014): rellena el hueco de CERRADAS.

        OKX devuelve newest-first y puede incluir la vela viva (confirm='0'): se
        invierte y se descarta la viva (esa llega por WS). Datos no validados: los
        valida la misma frontera de confianza.
        """
        if key.timeframe is None:
            return []
        params = urllib.parse.urlencode(
            {
                "instId": to_native(key.symbol),
                "bar": to_bar(key.timeframe.value),
                "limit": max(1, min(limit, 300)),
            }
        )
        filas = self._data_de(self._get_json(f"/api/v5/market/candles?{params}"))
        if filas is None:
            return []
        velas: list[RawCandle] = []
        for fila in reversed(filas):  # OKX: newest-first -> oldest-first.
            try:
                vela = raw_candle_from_okx(
                    fila, key.symbol, _MARKET_TYPE, key.timeframe.value
                )
            except OkxTranslationError:
                self.metrics.translation_errors += 1
                continue
            if vela.is_closed:  # el bootstrap solo rellena cerradas.
                velas.append(vela)
        return velas

    def list_instruments(self, market_type: str) -> Sequence[Instrument]:
        """Catalogo publico (GET /api/v5/public/instruments?instType=SPOT).

        En OKX el instId ya es la forma canonica BASE-QUOTE, asi que symbol y
        native_symbol coinciden: por eso este conector no necesita set_symbol_map.
        """
        if market_type != _MARKET_TYPE:
            return []
        filas = self._data_de(
            self._get_json("/api/v5/public/instruments?instType=SPOT")
        )
        if filas is None:
            return []
        instrumentos: list[Instrument] = []
        for entrada in filas:
            if not isinstance(entrada, dict):
                continue
            inst_id = str(entrada.get("instId", ""))
            base = str(entrada.get("baseCcy", ""))
            quote = str(entrada.get("quoteCcy", ""))
            if not inst_id or not base or not quote:
                continue
            instrumentos.append(
                Instrument(
                    exchange="okx",
                    market_type=_MARKET_TYPE,
                    symbol=inst_id,
                    native_symbol=inst_id,
                    active=entrada.get("state") == "live",
                )
            )
        return instrumentos

    def supported_timeframes(self) -> frozenset[Timeframe]:
        return supported_okx_timeframes()

    def drain_reconnected(self) -> AbstractSet[str]:
        """Devuelve (y limpia) las claves canonicas que reconectaron. Bajo lock."""
        with self._reconnected_lock:
            copia = set(self._reconnected)
            self._reconnected.clear()
        return copia

    # -- Operacion / validacion en caliente ----------------------------------

    def force_reconnect_all(self) -> int:
        """Cierra las conexiones vivas para FORZAR reconexion. Devuelve cuantas."""
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
                    name=f"okx-reader-{indice}",
                    daemon=True,
                )
                self._lectores[indice] = hilo
                hilo.start()

    def _leer(self, indice: int, keys: tuple[MarketStreamKey, ...]) -> None:
        """Lector de UNA conexion: suscribe al conectar y reconecta con backoff."""
        espera = self._config.backoff_initial_s
        ya_conecto = False
        args = [self._sub_arg(k) for k in keys if k.timeframe is not None]
        while not self._parar.is_set():
            try:
                with ws_client.connect(
                    _WS_BASE, ssl=self._ssl, user_agent_header=_USER_AGENT
                ) as conexion:
                    self._conexiones[indice] = conexion
                    try:
                        espera = self._config.backoff_initial_s
                        self._suscribir(conexion, args)
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

    def _bucle_recv(self, conexion: Any) -> None:
        """Lee mensajes. Si en idle_ping_s no llega nada, manda 'ping' (keep-alive)."""
        while not self._parar.is_set():
            try:
                mensaje = conexion.recv(timeout=self._config.idle_ping_s)
            except TimeoutError:
                conexion.send(_PING)
                continue
            self._procesar(conexion, mensaje)

    def _procesar(self, conexion: Any, mensaje: str | bytes) -> None:
        texto = mensaje.decode() if isinstance(mensaje, bytes) else mensaje
        if texto == _PONG:
            return  # respuesta a nuestro ping.
        if texto == _PING:
            conexion.send(_PONG)  # OKX nos pinguea: respondemos.
            return
        self._encolar(texto)

    def _sub_arg(self, key: MarketStreamKey) -> dict[str, str]:
        assert key.timeframe is not None
        return {
            "channel": to_channel(key.timeframe.value),
            "instId": to_native(key.symbol),
        }

    def _suscribir(self, conexion: Any, args: list[dict[str, str]]) -> None:
        if args:
            conexion.send(json.dumps({"op": "subscribe", "args": args}))

    def _encolar(self, mensaje: str) -> None:
        """Traduce y encola una vela. Control (event/error) se ignora o se cuenta."""
        try:
            sobre = json.loads(mensaje)
        except json.JSONDecodeError:
            self.metrics.translation_errors += 1
            return
        if not isinstance(sobre, dict):
            self.metrics.translation_errors += 1
            return
        if "event" in sobre:
            if sobre.get("event") == "error":
                self.metrics.translation_errors += 1
            return  # subscribe/unsubscribe/channel-conn-count: no son velas.
        arg = sobre.get("arg")
        datos = sobre.get("data")
        if not isinstance(arg, dict) or not isinstance(datos, list):
            return
        channel = str(arg.get("channel", ""))
        if not channel.startswith("candle"):
            return
        try:
            timeframe = timeframe_from_channel(channel)
            # OKX: instId ya es canonico; to_native valida la forma y lo devuelve igual.
            canonico = to_native(str(arg.get("instId", "")))
        except (SymbolTranslationError, TimeframeTranslationError):
            self.metrics.translation_errors += 1
            return
        for fila in datos:
            try:
                vela = raw_candle_from_okx(fila, canonico, _MARKET_TYPE, timeframe)
            except OkxTranslationError:
                self.metrics.translation_errors += 1
                continue
            try:
                self._cola.put_nowait(vela)
            except queue.Full:
                self.metrics.dropped_full_queue += 1
                self.metrics.degraded_streams.add(canonico)

    def _registrar_reconexion(self, keys: tuple[MarketStreamKey, ...]) -> None:
        """Cuenta una reconexion EXITOSA y marca sus claves para el bootstrap.

        Se cuenta al re-establecer (no en el except del lector, que es el DROP y puede
        dispararse varias veces por backoff). Marca las claves canonicas directamente:
        en OKX no hay que revertir desde un nombre de stream (identidad).
        """
        self.metrics.reconnections += 1
        claves = {k.as_stream_key() for k in keys if k.timeframe is not None}
        if claves:
            with self._reconnected_lock:
                self._reconnected.update(claves)

    def _jitter(self) -> float:
        """Ruido deterministico por hilo, sin random: basta con desincronizar."""
        return (threading.get_ident() % 1000) / 1000.0 * self._config.backoff_jitter_s

    def _data_de(self, respuesta: object) -> list[Any] | None:
        """Extrae 'data' de una respuesta REST de OKX si code=='0'. None si no."""
        if not isinstance(respuesta, dict) or respuesta.get("code") != "0":
            return None
        data = respuesta.get("data")
        return data if isinstance(data, list) else None

    def _get_json(self, path: str) -> object:
        peticion = urllib.request.Request(  # noqa: S310 - URL fija y https.
            f"{_REST_BASE}{path}",
            headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
        )
        with urllib.request.urlopen(  # noqa: S310
            peticion, timeout=self._config.rest_timeout_s, context=self._ssl
        ) as respuesta:
            return json.loads(respuesta.read().decode())
