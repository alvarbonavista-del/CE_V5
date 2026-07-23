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
    is_trade_channel,
    timeframe_from_channel,
    to_bar,
    to_channel,
    to_native,
    to_trade_channel,
)
from ce_v5.infra.connectors.okx.translate import (
    OkxTranslationError,
    raw_candle_from_okx,
    raw_trade_from_okx,
    supported_okx_timeframes,
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

_WS_BASE = "wss://ws.okx.com:8443/ws/v5/business"
_REST_BASE = "https://www.okx.com"
_MARKET_TYPE = "spot"
_PING = "ping"
_PONG = "pong"
# OKX esta tras Cloudflare y rechaza con 403 el User-Agent por defecto de urllib.
_USER_AGENT = "Mozilla/5.0 (compatible; ce-v5-market-data/0.1)"

# Techo REAL de history-trades SIN clave. NO es configuracion: es lo que el endpoint da.
# El sondeo en vivo (condicion de Central) descubrio que OKX lo CAPA A 300 EN SILENCIO
# (pides 500/1000 y devuelve 300 con code=0, sin error). Por eso se pide EXACTAMENTE 300
# y se pagina: nunca se asume haber recibido mas de lo que vino.
_REST_TRADES_PAGE = 300
# Tope de ESFUERZO del relleno: cuantas paginas de history-trades como mucho antes de
# rendirse y declarar el hueco (fail-safe). 40 paginas x 300 = 12000 trades de margen.
# Justificacion: (1) rate limit publico de OKX (20 peticiones/2s por IP); con la pausa
# de abajo, 40 peticiones caben de sobra bajo ese limite; (2) una ventana de reconexion
# REALISTA es de segundos a pocos minutos, y en BTC-USDT spot (baja cadencia) 12000
# trades cubren minutos. Un hueco que exija mas es un corte tan largo que su parte
# antigua OKX probablemente ya no sirve: ahi lo honesto es marcar hueco, no seguir
# pidiendo indefinidamente.
_BACKFILL_MAX_PAGES = 40


def _menor_event_time(trades: Sequence[RawTrade]) -> int | None:
    """El event_time mas antiguo del lote, o None si esta vacio."""
    return min((trade.event_time_ms for trade in trades), default=None)


def _coverage_okx(
    last_seen: LastSeenTrade, backfill: Sequence[RawTrade]
) -> tuple[bool, int | None, int | None]:
    """Decide si el relleno REST alcanzo lo que ya teniamos. PURA: sin red, sin reloj.

    IGUAL EN FORMA a _coverage_binance, y no por copiar sino porque el tradeId de OKX
    es, como el de Binance, un id ENTERO monotono y contiguo por instrumento (verificado
    en el sondeo en vivo): eso permite responder por CONTIGUIDAD en vez de por una
    estimacion temporal. Si el trade mas antiguo del relleno es el SIGUIENTE al ultimo
    que ya teniamos (o anterior: hay solape), la serie quedo contigua y no falta nada.
    Si es posterior, entre medias hay trades que este relleno no alcanzo: hueco real.

    Devuelve (covered, gap_from_event_time_ms, gap_to_event_time_ms). Con covered=True
    los limites van a None.

    FAIL-SAFE EN LOS TRES CAMINOS INCIERTOS -- ids no numericos, relleno vacio, o
    cualquier cosa que impida razonar sobre contiguidad -- se declara hueco. Declarar un
    hueco que no existe marca barras como incompletas sin motivo; NO declararlo publica
    una barra a la que le faltan trades como si estuviera completa.
    """
    if last_seen.trade_id is None:
        # PRIMERA CONEXION: no hay nada persistido, luego no hay hueco posible.
        return True, None, None

    try:
        ultimo_id = int(last_seen.trade_id)
        ids = [int(trade.trade_id) for trade in backfill]
    except (TypeError, ValueError):
        # Un id no entero rompe el razonamiento por contiguidad. No se improvisa otro
        # criterio: se declara hueco y se acota por event_time.
        return False, last_seen.event_time_ms, _menor_event_time(backfill)

    if not ids:
        # El REST no devolvio nada con lo que acotar el extremo superior: hueco con ese
        # extremo DESCONOCIDO (None), en vez de inventarle un limite.
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
    # Pausa entre paginas del relleno de trades: ESPACIA las peticiones para respetar el
    # rate limit publico de OKX (20 peticiones/2s por IP) en un backfill de varias
    # paginas. Los tests en frio la ponen a 0 (no hay red que proteger).
    backfill_page_pause_s: float = 0.2


@dataclass(slots=True)
class ConnectorMetrics:
    """Observabilidad: sin esto, una cola que descarta es un agujero invisible."""

    dropped_full_queue: int = 0
    # Contador PROPIO de los trades: sumarlo al de velas ocultaria CUAL de los dos
    # flujos pierde datos, y son de escalas muy distintas (un par liquido publica miles
    # de trades por minuto y una vela por minuto).
    dropped_full_queue_trades: int = 0
    translation_errors: int = 0
    reconnections: int = 0
    degraded_streams: set[str] = field(default_factory=set)


class OkxSpotConnector:
    """Feed publico de OKX Spot. Cumple MarketDataSourcePort y TradeDataSourcePort por
    FORMA.

    NO importa platform: los dos puertos se satisfacen estructuralmente. NO implementa
    SymbolMapSink: en OKX el instId ya es canonico, no hace falta mapa.

    VELAS Y TRADES VIAJAN POR LA MISMA CONEXION (endpoint business), multiplexados y
    separados por el 'channel' del 'arg': candle<bar> son velas, 'trades-all' son
    trades. Abrir un socket aparte para los trades del mismo par gastaria el doble
    contra el limite de conexiones sin ganar nada. Lo que SI esta separado es la COLA de
    cada clase: un pico de trades no puede desalojar velas, ni al reves.
    """

    def __init__(self, config: OkxConfig | None = None) -> None:
        self._config = config or OkxConfig()
        self._planner = ConnectionPlanner(self._config.limits)
        self._deseados: dict[str, MarketStreamKey] = {}
        self._cola: queue.Queue[RawCandle] = queue.Queue(maxsize=self._config.max_queue)
        # Cola SEPARADA para los trades, con el mismo tope y el mismo backpressure
        # observable. Compartirla con las velas dejaria que una avalancha de trades
        # expulsase las velas, que son el dato sobre el que se evaluan las reglas.
        self._cola_trades: queue.Queue[RawTrade] = queue.Queue(
            maxsize=self._config.max_queue
        )
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

    # -- TradeDataSourcePort -------------------------------------------------
    #
    # open/close/active/drain_reconnected los comparte con el puerto de velas: son las
    # MISMAS suscripciones sobre la MISMA conexion, distinguidas por el data_kind de la
    # clave. Aqui solo estan los dos metodos propios de trades.

    def poll_trades(self, timeout_ms: int) -> Sequence[RawTrade]:
        """DRENA la cola de trades. Espejo de poll(): PULL con tope, manda el motor y no
        el exchange. En trades importa aun mas, porque el caudal es de otro orden.
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

        A DIFERENCIA DE BINANCE, aqui el relleno PAGINA: OKX sirve history-trades por id
        (type=1) y con &after=<tradeId> se camina hacia atras, asi que el hueco se puede
        tapar ENTERO en vez de con una sola ventana. El bucle acumula paginas hasta que
        el mas antiguo empalma con lo que teniamos (cubierto) o hasta el TOPE de
        esfuerzo (_BACKFILL_MAX_PAGES), donde se rinde y el fail-safe declara el hueco.

        OJO AL CAP SILENCIOSO: cada pagina se pide con limit=_REST_TRADES_PAGE (300), el
        techo real que OKX aplica sin avisar. NUNCA se asume haber recibido mas de lo
        que la pagina trajo: el avance es por el id mas antiguo REALMENTE devuelto.

        Datos NO validados, igual que en velas: el REST no es mas confiable que el
        socket y los valida la MISMA frontera. El solape ya persistido lo absorbe el
        dedup por identidad natural. El IO vive aqui; la DECISION de cobertura vive en
        _coverage_okx, que es pura y se prueba en frio.
        """
        objetivo = self._objetivo_backfill(last_seen)
        trades: list[RawTrade] = []
        after: str | None = None
        for pagina_num in range(_BACKFILL_MAX_PAGES):
            pagina = self._history_trades_page(key.symbol, after)
            if not pagina:
                break  # OKX no dio mas: el fail-safe decidira si eso deja hueco.
            trades.extend(pagina)
            mas_antiguo = self._id_mas_antiguo(pagina)
            if mas_antiguo is None:
                break  # ids no numericos: no se pagina por id (lo vera el fail-safe)
            if objetivo is None:
                # Primera conexion (o last_seen no razonable): una pagina basta. No hay
                # hueco que perseguir; seguir pidiendo seria martillear el REST sin fin.
                break
            if mas_antiguo <= objetivo + 1:
                break  # CUBIERTO: el relleno empalmo con lo que ya teniamos.
            after = str(mas_antiguo)  # siguiente pagina: mas antigua que esta.
            if pagina_num + 1 < _BACKFILL_MAX_PAGES:
                # Espaciar las peticiones respeta el rate limit de OKX. Se usa el Event
                # de parada como espera para que un shutdown la interrumpa en el acto.
                self._parar.wait(self._config.backfill_page_pause_s)
        covered, gap_from, gap_to = _coverage_okx(last_seen, trades)
        return TradeBackfillResult(
            raw_trades=trades,
            covered=covered,
            gap_from_event_time_ms=gap_from,
            gap_to_event_time_ms=gap_to,
        )

    def _objetivo_backfill(self, last_seen: LastSeenTrade) -> int | None:
        """El id hasta el que paginar hacia atras, o None si no hay que perseguir nada.

        None en DOS casos que el bucle trata igual (una pagina y parar) pero que
        _coverage_okx distingue: primera conexion (trade_id None -> cubierto, no hay
        hueco) y last_seen con un id no numerico (-> hueco, fail-safe). En ambos, seguir
        paginando no aportaria nada.
        """
        if last_seen.trade_id is None:
            return None
        try:
            return int(last_seen.trade_id)
        except (TypeError, ValueError):
            return None

    def _id_mas_antiguo(self, pagina: Sequence[RawTrade]) -> int | None:
        """El menor tradeId (entero) de una pagina, o None si algun id no es numerico.

        None frena la paginacion: sin ids enteros no se puede avanzar por &after con
        garantia, y forzarlo abriria la puerta a un bucle que no converge.
        """
        try:
            return min(int(trade.trade_id) for trade in pagina)
        except (TypeError, ValueError):
            return None

    def _history_trades_page(self, symbol: str, after: str | None) -> list[RawTrade]:
        """UNA pagina de GET /api/v5/market/history-trades (type=1, por id). SIN clave.

        after=None es la primera pagina (los mas recientes); con valor, OKX devuelve los
        ANTERIORES a ese tradeId (mas antiguos): asi se camina el hueco hacia atras.

        FAULT ISOLATION POR FILA: una fila mala se cuenta y se salta; perder un trade
        del relleno es menos grave que perder los otros 299 por un campo raro.
        """
        params: dict[str, str] = {
            "instId": to_native(symbol),
            "type": "1",
            "limit": str(_REST_TRADES_PAGE),
        }
        if after is not None:
            params["after"] = after
        filas = self._data_de(
            self._get_json(
                f"/api/v5/market/history-trades?{urllib.parse.urlencode(params)}"
            )
        )
        if filas is None:
            return []
        trades: list[RawTrade] = []
        for fila in filas:
            try:
                trades.append(raw_trade_from_okx(fila, symbol, _MARKET_TYPE))
            except OkxTranslationError:
                self.metrics.translation_errors += 1
        return trades

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

    def _es_suscribible(self, key: MarketStreamKey) -> bool:
        """Una clave que ESTE connector suscribe en OKX: velas (con timeframe) o trades.

        FOOTPRINT queda fuera a proposito: tiene timeframe, pero es dato DERIVADO que
        agregamos NOSOTROS, no un flujo que OKX publique. Suscribirse a el seria pedirle
        al exchange algo que no existe. Es el UNICO sitio que decide que es suscribible,
        y por eso lo usan _replanificar, _leer y _registrar_reconexion: si cada uno lo
        decidiera por su cuenta, un dia dejarian de coincidir.
        """
        if key.data_kind is MarketDataKind.CANDLES:
            return key.timeframe is not None
        return key.data_kind is MarketDataKind.TRADES

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
                    name=f"okx-reader-{indice}",
                    daemon=True,
                )
                self._lectores[indice] = hilo
                hilo.start()

    def _leer(self, indice: int, keys: tuple[MarketStreamKey, ...]) -> None:
        """Lector de UNA conexion: suscribe al conectar y reconecta con backoff."""
        espera = self._config.backoff_initial_s
        ya_conecto = False
        args = [self._sub_arg(k) for k in keys if self._es_suscribible(k)]
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
        """El arg de suscripcion (channel, instId) de una clave suscribible.

        Velas y trades comparten forma -- (channel, instId) -- y solo difieren en el
        canal: candle<bar> para velas, 'trades-all' para trades. En OKX el instId ya es
        canonico (to_native lo valida y lo devuelve igual).
        """
        if key.data_kind is MarketDataKind.TRADES:
            return {"channel": to_trade_channel(), "instId": to_native(key.symbol)}
        assert key.timeframe is not None
        return {
            "channel": to_channel(key.timeframe.value),
            "instId": to_native(key.symbol),
        }

    def _suscribir(self, conexion: Any, args: list[dict[str, str]]) -> None:
        if args:
            conexion.send(json.dumps({"op": "subscribe", "args": args}))

    def _encolar(self, mensaje: str) -> None:
        """Traduce y encola ENRUTANDO POR CANAL. El control (event/error) se cuenta.

        Velas y trades llegan MEZCLADOS por la misma conexion y el 'channel' del 'arg'
        dice cual es cual: candle<bar> son velas, 'trades-all' son trades. Cualquier
        otro canal se ignora, igual que antes de que existieran los trades.
        """
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
            return  # subscribe/unsubscribe/channel-conn-count: no son datos.
        arg = sobre.get("arg")
        datos = sobre.get("data")
        if not isinstance(arg, dict) or not isinstance(datos, list):
            return
        channel = str(arg.get("channel", ""))
        if is_trade_channel(channel):
            self._encolar_trades(arg, datos)
            return
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

    def _encolar_trades(self, arg: dict[str, Any], datos: list[Any]) -> None:
        """Un mensaje de 'trades-all' -> RawTrade(s) en la cola de TRADES. Espejo del
        camino de velas: mismo canonico consultado (jamas deducido), misma conversion de
        la excepcion de traduccion en metrica observable, mismo backpressure contado.
        """
        try:
            canonico = to_native(str(arg.get("instId", "")))
        except SymbolTranslationError:
            self.metrics.translation_errors += 1
            return
        for fila in datos:
            try:
                trade = raw_trade_from_okx(fila, canonico, _MARKET_TYPE)
            except OkxTranslationError:
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

        Se cuenta al re-establecer (no en el except del lector, que es el DROP y puede
        dispararse varias veces por backoff). Marca las claves canonicas directamente:
        en OKX no hay que revertir desde un nombre de stream (identidad).

        Marca TODAS las claves suscribibles de la conexion -- velas Y trades --, porque
        una conexion multiplexada que se cae deja hueco de las dos clases: el motor de
        velas rebootstrapea las suyas y el de trades hace su backfill de las suyas, cada
        uno filtrando de drain_reconnected lo que le toca.
        """
        self.metrics.reconnections += 1
        claves = {k.as_stream_key() for k in keys if self._es_suscribible(k)}
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
