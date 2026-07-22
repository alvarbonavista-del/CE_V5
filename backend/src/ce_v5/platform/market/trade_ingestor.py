"""Motor de ingesta de TRADES individuales (ADR-014, ADR-006, ADR-007).

Gemelo simplificado de ingestor.py (velas), y donde se PARECE importa tanto como donde
se DIFERENCIA. Las tres diferencias son de fondo, no de estilo:

- LOS TRADES NO SE PUBLICAN AL BUS. Un par liquido produce miles de trades por minuto:
  publicarlos uno a uno seria la avalancha que I-02 advirtio, y nadie los consume asi.
  Se PERSISTEN (y de ahi sale el footprint, que si se publica por barra). Por eso el
  puerto de escritura tiene UN metodo, persist(), y no hay outbox: sin publicacion no
  hay pareja persistida/publicada que pueda divergir, que es lo unico que el patron
  outbox existe para impedir.

- UN TRADE NO TIENE MADUREZ. No hay provisional, ni cerrado, ni corregido, ni
  watermark: un trade es un HECHO UNICO e inmutable que ya ocurrio. El exchange no lo
  "revisa"; a lo sumo lo reenvia. De ahi que no exista nada del aparato de correcciones
  de las velas: construirlo "por si acaso" seria codigo que ningun test puede alcanzar.

- EL ORDEN ES IRRELEVANTE. El dedup va por la CLAVE UNICA del trade (exchange,
  market_type, symbol, trade_id), que es su identidad natural. El trade_id es clave de
  DEDUP, no criterio de orden: la agregacion posterior a footprint es conmutativa, asi
  que los mismos trades en cualquier orden producen el MISMO conjunto persistido. Eso
  es lo que hace la ingesta reproducible, y esta cubierto por su test.

NO importa infra ni components. Sin hilos y sin sleep: el ritmo lo marca quien llama a
drain_once(). Y sin Clock: no se construye ningun sobre, asi que no hay ningun instante
NUESTRO que fechar (el del trade lo pone el exchange, ADR-007).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from ce_v5.platform.market.trade_normalize import (
    RawTradeRejected,
    trade_from_raw,
)
from ce_v5.platform.market.trade_source import (
    LastSeenTrade,
    TradeBackfillResult,
    TradeDataSourcePort,
)
from source.families.footprint import MarketTrade
from source.families.market import MarketStreamKey, RawTrade


class TradeWriterPort(Protocol):
    """Puerto del STORE de trades. Lo cumple infra/db por FORMA (estructural).

    Escribe y, ademas, RESPONDE POR LO QUE TIENE: last_seen es una lectura, y esta aqui
    a proposito. Quien sabe cual fue el ultimo trade contiguo antes de un corte es la
    tabla, no la memoria del proceso; preguntarselo a la base es lo que hace que un
    REINICIO con un hueco mayor que el techo REST tambien se detecte, en vez de
    arrancar creyendo que no habia nada que rellenar.
    """

    def persist(self, trade: MarketTrade) -> bool:
        """Guarda el trade. Devuelve False si YA ESTABA (dedup por su clave unica).

        Sin outbox: el trade no se publica (evita la avalancha, I-02). El booleano es
        dedup HONESTO: lo dice la base (ON CONFLICT ... RETURNING), no una consulta
        previa que otro proceso podria invalidar entre el SELECT y el INSERT. Distinguir
        "entro" de "ya estaba" es lo que permite que una reconexion reprocese su solape
        sin inflar las metricas ni el historico.
        """
        ...

    def last_seen(self, exchange: str, market_type: str, symbol: str) -> LastSeenTrade:
        """El trade persistido de mayor (event_time, trade_id) de ese flujo.

        Es el punto desde el que el conector tiene que rellenar. Campos a None si no hay
        ni una fila: primera conexion, no hay hueco posible.
        """
        ...

    def record_gap(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        gap_from_event_time_ms: int | None,
        gap_to_event_time_ms: int | None,
    ) -> bool:
        """Apunta un HUECO no cubierto. Devuelve True SOLO si la fila entro.

        IDEMPOTENTE: apuntar el mismo hueco dos veces no lo duplica (lo decide el UNIQUE
        de la tabla con ON CONFLICT DO NOTHING, no un SELECT previo). El booleano
        distingue "hueco nuevo" de "ya estaba apuntado", que es lo que permite contar
        huecos REALES en vez de reconexiones.

        Registrar la AUSENCIA de datos es tan importante como registrar los datos: sin
        esta fila, una barra de footprint a la que le faltan trades se publicaria como
        completa y nadie podria saberlo despues.
        """
        ...


@dataclass(frozen=True, slots=True)
class TradeIngestionConfig:
    """BACKPRESSURE: quien manda es el motor, no el exchange.

    max_batch acota lo que se procesa por ciclo. Sin tope, un pico de volatilidad se
    convierte en una cola infinita en memoria y tumba el proceso. Lo que no cabe no se
    pierde: espera en el feed al siguiente ciclo.
    """

    max_batch: int = 500
    poll_timeout_ms: int = 200
    # NO hay bootstrap_limit, y su ausencia es una decision: cuanto rellenar tras una
    # reconexion NO es un numero que el nucleo pueda elegir. Lo fija el techo del
    # endpoint publico de cada exchange, que es el unico limite real. Un numero de
    # config aqui daba una falsa sensacion de control -- no guarda ninguna relacion con
    # lo que duro el corte -- y ademas ocultaba el caso importante: que el hueco fuera
    # MAYOR que lo que el REST puede devolver. Eso ahora lo responde el conector
    # (TradeBackfillResult.covered) y se registra como hueco explicito.


DEFAULT_TRADE_INGESTION_CONFIG = TradeIngestionConfig()


@dataclass(slots=True)
class TradeIngestionMetrics:
    """Observabilidad. Sin esto, un stream zombi o un feed que solo manda basura son
    INVISIBLES: el proceso parece sano porque no falla.
    """

    trades_persisted: int = 0
    duplicates_skipped: int = 0
    unsubscribed_dropped: int = 0
    # Backfill REST tras reconexion: cuantos trades se reprocesaron y cuantos streams
    # fallaron su backfill (fault isolation: el fallo de uno no tumba a los demas).
    bootstrap_trades: int = 0
    bootstrap_errors: int = 0
    # HUECOS NUEVOS registrados: reconexiones cuyo relleno NO llego a cubrir el corte.
    # Cuenta filas REALMENTE insertadas, no reconexiones: re-apuntar un hueco ya
    # conocido no lo incrementa. Si este contador sube, hay barras de footprint que
    # NUNCA podran emitirse como completas, y eso es una perdida de dato permanente que
    # tiene que verse.
    uncovered_gaps: int = 0
    rejected: dict[str, int] = field(default_factory=dict)  # por reason code
    degraded_streams: set[str] = field(default_factory=set)


class TradeIngestionEngine:
    """Convierte el feed de trades de un exchange en hechos persistidos (ADR-014)."""

    def __init__(
        self,
        source: TradeDataSourcePort,
        writer: TradeWriterPort,
        *,
        config: TradeIngestionConfig = DEFAULT_TRADE_INGESTION_CONFIG,
    ) -> None:
        self._source = source
        self._writer = writer
        self._config = config
        self.metrics = TradeIngestionMetrics()

    def drain_once(self) -> TradeIngestionMetrics:
        """Un ciclo: procesa hasta max_batch trades y los convierte en hechos.

        BACKPRESSURE SIN PERDIDA: el motor deja de PEDIR cuando alcanza su tope, en vez
        de pedirlo todo y tirar lo que no le cabe. Lo que no se pide se queda en el
        feed, esperando al siguiente ciclo: nada se pierde y la memoria no crece.
        Tirar el sobrante seria "backpressure" solo de nombre; en realidad seria perder
        trades en silencio, y un trade perdido es una celda de footprint que miente.
        """
        suscritas = {
            clave: MarketStreamKey.parse(clave) for clave in self._source.active()
        }

        # EL BACKFILL VA ANTES DEL POLL, y el orden es la parte que importa. last_seen
        # tiene que ser el ultimo trade CONTIGUO previo al corte; si primero drenasemos
        # el socket ya reanudado, la base contendria trades POSTERIORES al hueco y
        # last_seen apuntaria al otro lado del agujero: el conector compararia contra un
        # trade que llego DESPUES y concluiria que no falta nada. El hueco se cerraria
        # solo, en silencio, en los libros.
        #
        # LIMITACION CONOCIDA Y ACEPTADA (Central): si el stream ya reanudo y persistio
        # antes de que este ciclo corra, un hueco interno puede quedar enmascarado. Este
        # orden lo hace improbable, no imposible. Se documenta en vez de fingir que no
        # existe.
        self._backfill_reconectados()

        procesados = 0
        while procesados < self._config.max_batch:
            crudos = self._source.poll_trades(self._config.poll_timeout_ms)
            if not crudos:
                break
            for raw in crudos:
                self._procesar(raw, suscritas)
                procesados += 1

        return self.metrics

    def _backfill_reconectados(self) -> None:
        """Rellena el hueco de cada stream que reconecto y REGISTRA lo que no se cubrio.

        El conector senala que streams reconectaron (drain_reconnected); por cada uno se
        le pide que rellene desde el ultimo trade que la BASE dice que teniamos
        (last_seen) y que responda si con eso basto. Los trades del relleno se procesan
        como uno mas, por el MISMO camino de normalizacion + dedup: el solape con lo ya
        persistido lo absorbe la PK (duplicates_skipped) y lo que falte SI entra.

        Y SI NO BASTO, SE ESCRIBE. Un hueco que el REST no alcanzo a cubrir es dato
        perdido para siempre; lo unico honesto es dejar constancia de DONDE falta, para
        que 3b marque como incompletas las barras que se solapen con el. Callarlo
        publicaria barras a las que les faltan trades como si estuvieran completas.

        FAULT ISOLATION POR STREAM: una clave corrupta, un backfill que lanza o un
        registro de hueco que falla se cuentan y se saltan; jamas tumban el ciclo ni a
        los demas streams.
        """
        for clave_texto in self._source.drain_reconnected():
            try:
                clave = MarketStreamKey.parse(clave_texto)
            except ValueError:
                # Clave corrupta: se cuenta y se salta (nada de raise).
                self.metrics.bootstrap_errors += 1
                continue
            try:
                ultimo = self._writer.last_seen(
                    clave.exchange, clave.market_type.value, clave.symbol
                )
                resultado = self._source.backfill_after_reconnect(clave, ultimo)
            except Exception:  # noqa: BLE001 - un backfill fallido no tumba el ciclo.
                self.metrics.bootstrap_errors += 1
                self.metrics.degraded_streams.add(clave_texto)
                continue

            for raw in resultado.raw_trades:
                self._procesar(raw, {clave_texto: clave})
                self.metrics.bootstrap_trades += 1

            if resultado.covered:
                continue
            self._registrar_hueco(clave, clave_texto, resultado)

    def _registrar_hueco(
        self,
        clave: MarketStreamKey,
        clave_texto: str,
        resultado: TradeBackfillResult,
    ) -> None:
        """Apunta un hueco no cubierto y lo cuenta SOLO si era nuevo.

        La metrica sigue a la BASE, no a la reconexion: si el mismo hueco se vuelve a
        detectar (otra reconexion antes de que nadie lo consuma), el UNIQUE de la tabla
        lo absorbe y record_gap devuelve False. Contar reconexiones en vez de huecos
        haria creer que se pierde dato nuevo cada vez.

        Un stream con un hueco queda marcado como DEGRADADO: no es un ciclo fallido,
        pero tampoco es normalidad, y quien mire las metricas tiene que verlo.
        """
        try:
            nuevo = self._writer.record_gap(
                clave.exchange,
                clave.market_type.value,
                clave.symbol,
                resultado.gap_from_event_time_ms,
                resultado.gap_to_event_time_ms,
            )
        except Exception:  # noqa: BLE001 - no poder apuntarlo no tumba el ciclo.
            self.metrics.bootstrap_errors += 1
            self.metrics.degraded_streams.add(clave_texto)
            return
        if nuevo:
            self.metrics.uncovered_gaps += 1
        self.metrics.degraded_streams.add(clave_texto)

    def _procesar(
        self, raw: RawTrade, suscritas: Mapping[str, MarketStreamKey]
    ) -> None:
        clave_texto = self._clave_declarada(raw)
        esperada = suscritas.get(clave_texto)
        if esperada is None:
            # Nadie pidio este flujo: no se procesa. Un dato que nadie quiere no entra
            # en el historico solo porque el exchange lo mande.
            self.metrics.unsubscribed_dropped += 1
            return

        try:
            trade = trade_from_raw(raw, esperada)
        except RawTradeRejected as rechazo:
            # AISLAMIENTO POR STREAM: un trade corrupto de BTC no puede impedir que se
            # procese el trade bueno de ETH que viene detras en el mismo lote.
            motivo = rechazo.reason.value
            self.metrics.rejected[motivo] = self.metrics.rejected.get(motivo, 0) + 1
            self.metrics.degraded_streams.add(clave_texto)
            return

        if self._writer.persist(trade):
            self.metrics.trades_persisted += 1
        else:
            # EL CASO NORMAL tras una reconexion, no un error: el bootstrap REST vuelve
            # a traer trades que ya teniamos. Su identidad natural los caza.
            self.metrics.duplicates_skipped += 1

    def _clave_declarada(self, raw: RawTrade) -> str:
        """La clave del flujo al que el trade DICE pertenecer.

        SIN timeframe: el flujo de trades no se bucketea a nivel de stream (ADR-014).
        Solo sirve para encontrar la suscripcion; que el trade pertenezca de verdad a
        ese flujo lo decide la frontera de confianza (anti-suplantacion), no esto.
        """
        return ":".join(["market", "trades", raw.exchange, raw.market_type, raw.symbol])
