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
from ce_v5.platform.market.trade_source import TradeDataSourcePort
from source.families.footprint import MarketTrade
from source.families.market import MarketStreamKey, RawTrade


class TradeWriterPort(Protocol):
    """Puerto de escritura de trades. Lo cumple infra/db por FORMA (estructural)."""

    def persist(self, trade: MarketTrade) -> bool:
        """Guarda el trade. Devuelve False si YA ESTABA (dedup por su clave unica).

        UN SOLO METODO, y sin outbox: el trade no se publica (evita la avalancha, I-02).
        El booleano es dedup HONESTO: lo dice la base (ON CONFLICT ... RETURNING), no
        una consulta previa que otro proceso podria invalidar entre el SELECT y el
        INSERT. Distinguir "entro" de "ya estaba" es lo que permite que una reconexion
        reprocese su solape sin inflar las metricas ni el historico.
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
    # Cuantos trades recientes pedir por stream tras reconectar. MUY por encima del
    # bootstrap de velas (10) a proposito: en el mismo hueco de tiempo caben ordenes de
    # magnitud mas trades que velas. El dedup absorbe el solape con lo ya persistido.
    bootstrap_limit: int = 100


DEFAULT_TRADE_INGESTION_CONFIG = TradeIngestionConfig()


@dataclass(slots=True)
class TradeIngestionMetrics:
    """Observabilidad. Sin esto, un stream zombi o un feed que solo manda basura son
    INVISIBLES: el proceso parece sano porque no falla.
    """

    trades_persisted: int = 0
    duplicates_skipped: int = 0
    unsubscribed_dropped: int = 0
    # Bootstrap REST tras reconexion: cuantos trades se reprocesaron y cuantos streams
    # fallaron su bootstrap (fault isolation: el fallo de uno no tumba a los demas).
    bootstrap_trades: int = 0
    bootstrap_errors: int = 0
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

        procesados = 0
        while procesados < self._config.max_batch:
            crudos = self._source.poll_trades(self._config.poll_timeout_ms)
            if not crudos:
                break
            for raw in crudos:
                self._procesar(raw, suscritas)
                procesados += 1

        self._bootstrap_reconectados()
        return self.metrics

    def _bootstrap_reconectados(self) -> None:
        """Rellena el hueco de cada stream que reconecto, por el MISMO camino de
        normalizacion+dedup que los trades del poll (ADR-014).

        El conector senala que streams reconectaron (drain_reconnected); por cada uno se
        pide su historico reciente por REST (fetch_recent_trades) y se procesa como uno
        mas. El bootstrap REexpone trades que probablemente YA estan persistidos: el
        dedup por PK los absorbe (duplicates_skipped), y si hubo un hueco real, los que
        falten SI entran. Asi la reconexion no pierde ni duplica.

        FAULT ISOLATION POR STREAM: un bootstrap fallido de UN stream (un
        fetch_recent_trades que lanza, o una clave corrupta) se cuenta y se salta; jamas
        tumba el ciclo ni a los demas streams.
        """
        for clave_texto in self._source.drain_reconnected():
            try:
                clave = MarketStreamKey.parse(clave_texto)
            except ValueError:
                # Clave corrupta: se cuenta y se salta (nada de raise).
                self.metrics.bootstrap_errors += 1
                continue
            try:
                trades = self._source.fetch_recent_trades(
                    clave, self._config.bootstrap_limit
                )
            except Exception:  # noqa: BLE001 - un bootstrap fallido no tumba el ciclo.
                self.metrics.bootstrap_errors += 1
                self.metrics.degraded_streams.add(clave_texto)
                continue
            for raw in trades:
                self._procesar(raw, {clave_texto: clave})
                self.metrics.bootstrap_trades += 1

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
