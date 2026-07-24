"""El worker de ingesta es un proceso propio (ADR-002, P07).

Arranque: python -m ce_v5.entrypoints.worker_ingestion

EL BUCLE VIVE AQUI, NO EN build_context: como la API, la construccion cablea y punto;
el bucle lo arranca el proceso. Un hilo de fondo escondido en la construccion es un
hilo que los tests no controlan.

GUARDIA 5.20: build_context usa IngestionDbConfig.from_env, que ABORTA si el DSN es el
de la aplicacion o el del operador. Este proceso solo porta la credencial de ingesta.
"""

from __future__ import annotations

import os
import signal
import time
from collections.abc import Iterable
from types import FrameType

from ce_v5.entrypoints.worker_ingestion.catalog_sync import sync_catalog
from ce_v5.entrypoints.worker_ingestion.composition import (
    IngestionContext,
    build_context,
)
from ce_v5.platform.market.orderbook_book import OrderbookBook
from source.families.market import (
    MarketDataKind,
    MarketStreamKey,
    Timeframe,
)

_DEFAULT_TICK_MS = 1000
_METRICS_EVERY = 10  # cada cuantos ciclos se imprime el resumen observable.
# Cadencia por defecto del MUESTREO del libro (~1/s): entra en la idempotency_key del
# snapshot (cond.1). No depende de candle_closed; la FRONTERA se dispara aparte, por
# RELOJ DE BARRA (opcion 3), sin tocar el nucleo (ver _OrderbookFrontier).
_DEFAULT_ORDERBOOK_SAMPLE_MS = 1000
# Ventana de la muestra: la barra M1 que contiene el instante. Es el bucket mas fino; el
# sample_time real es el instante. Solo describe a que barra pertenece la muestra.
_SAMPLE_TIMEFRAME = Timeframe.M1


class _OrderbookSampler:
    """Decide cuando toca una muestra (a cadencia) y calcula su ventana.

    Sin reloj propio: el instante se lo pasa el bucle, para que el muestreo no dependa
    de un time.sleep exacto.
    """

    def __init__(self, cadence_ms: int) -> None:
        self._cadence_ms = cadence_ms
        self._last_ms: int | None = None

    def due(self, now_ms: int) -> bool:
        if self._last_ms is None or now_ms - self._last_ms >= self._cadence_ms:
            self._last_ms = now_ms
            return True
        return False


class _OrderbookFrontier:
    """Trigger de la FRONTERA por RELOJ DE BARRA (opcion 3 de Central; CE-14 intacto).

    SIN hook en el nucleo y sin depender de candle_closed: el disparo lo decide el Clock
    inyectado. Recuerda, por clave de vela ACTIVA, el ultimo bucket de barra visto.
    boundary(tf) = floor(now/tf_ms)*tf_ms; cuando el bucket de un (symbol, tf) activo
    AVANZA sobre el ultimo, la barra [bucket_anterior, bucket_anterior+tf) CERRO
    y se emite (open_time = boundary anterior). Un cruce de 5m es tambien cruce de 1m:
    cada (symbol, tf) se evalua por separado y ambos disparan. La PRIMERA vez que se ve
    una clave se registra su bucket SIN disparar (no hay barra anterior que cerrar sin
    inventarsela).

    Determinista y reproducible: mismas claves + mismo Clock -> mismos disparos. Las
    claves activas las da el llamador (la demanda de velas YA abierta); no las inventa.
    """

    def __init__(self) -> None:
        # clave_de_vela -> ultimo bucket de barra visto. Se poda cuando la clave deja de
        # estar activa: no crece sin limite ni dispara una barra rancia al reaparecer.
        self._last_bucket: dict[str, int] = {}

    def due_bars(
        self, active: Iterable[MarketStreamKey], now_ms: int
    ) -> list[tuple[MarketStreamKey, Timeframe, int, int]]:
        """(clave_vela, tf, open_time, close_time) de cada barra cerrada este tick."""
        cerradas: list[tuple[MarketStreamKey, Timeframe, int, int]] = []
        vistas: set[str] = set()
        for key in active:
            tf = key.timeframe
            if tf is None:  # una vela sin timeframe seria un dato de otro flujo.
                continue
            clave = key.as_stream_key()
            vistas.add(clave)
            dur = tf.duration_ms
            bucket = (now_ms // dur) * dur
            previo = self._last_bucket.get(clave)
            self._last_bucket[clave] = bucket
            if previo is not None and bucket > previo:
                cerradas.append((key, tf, previo, previo + dur))
        for clave in [c for c in self._last_bucket if c not in vistas]:
            del self._last_bucket[clave]
        return cerradas


def _active_candle_keys(context: IngestionContext) -> list[MarketStreamKey]:
    """Las (exchange, mkt, symbol, tf) de VELA realmente abiertas (demanda existente).

    Salen de datasource.active() -- los streams REALMENTE abiertos --, no de un catalogo
    inventado: la frontera fotografia el libro de los simbolos cuya vela el sistema ya
    sigue. Una clave corrupta se salta (fault isolation), como el SubscriptionManager.
    """
    claves: list[MarketStreamKey] = []
    for clave in context.datasource.active():
        try:
            key = MarketStreamKey.parse(clave)
        except ValueError:
            continue
        if key.data_kind is MarketDataKind.CANDLES and key.timeframe is not None:
            claves.append(key)
    return claves


class _StopSignal:
    """Bandera de parada. SIGINT/SIGTERM la activan; el bucle la consulta."""

    def __init__(self) -> None:
        self._stop = False

    def request(self, _signum: int, _frame: FrameType | None) -> None:
        self._stop = True

    @property
    def requested(self) -> bool:
        return self._stop


def _print_metrics(context: IngestionContext) -> None:
    """Resumen OBSERVABLE: sin esto, un worker que no ingiere parece sano."""
    m = context.engine.metrics
    print(
        f"[ingesta] cerradas={m.closed_persisted} provisionales="
        f"{m.provisional_published} correcciones={m.corrections_emitted} "
        f"duplicados={m.duplicates_skipped} fuera_de_orden={m.out_of_order_dropped} "
        f"sin_suscripcion={m.unsubscribed_dropped} rechazos={m.rejected} "
        f"degradados={sorted(m.degraded_streams)}",
        flush=True,
    )
    if context.trade_engine is None:
        return
    t = context.trade_engine.metrics
    print(
        f"[trades] persistidos={t.trades_persisted} duplicados={t.duplicates_skipped} "
        f"sin_suscripcion={t.unsubscribed_dropped} bootstrap={t.bootstrap_trades} "
        f"errores_bootstrap={t.bootstrap_errors} rechazos={t.rejected} "
        f"degradados={sorted(t.degraded_streams)}",
        flush=True,
    )
    if context.orderbook_engine is None or context.orderbook_snapshot is None:
        return
    o = context.orderbook_engine.metrics
    s = context.orderbook_snapshot.metrics
    print(
        f"[orderbook] deltas={o.deltas_applied} resyncs={o.resyncs} "
        f"reseeds={o.reseeds} "
        f"discontinuidades={o.discontinuities_recorded} muestras={s.samples_persisted} "
        f"rechazos={o.rejected} degradados={sorted(o.degraded_streams)}",
        flush=True,
    )
    print(
        f"[orderbook] fronteras={s.frontiers_published} "
        f"incompletas={s.incomplete_frontiers} "
        f"sin_semilla={s.frontiers_skipped_unseeded} "
        f"duplicadas={s.duplicates_skipped}",
        flush=True,
    )


def _drain_trades(context: IngestionContext) -> None:
    """Un ciclo del motor de TRADES, junto al tick del de velas y NO dentro de el.

    El componente de velas no conoce el motor de trades ni tiene por que: son dos
    motores independientes que comparten conector, proceso y credencial (Central Q3).

    CON SU PROPIA FAULT ISOLATION, por el mismo motivo que la del componente: un poll
    que falla, o una base que parpadea, degradan ESTE ciclo y el siguiente reintenta.
    Si la excepcion subiera al bucle, un fallo transitorio de TRADES tumbaria tambien
    la ingesta de VELAS, para todos los usuarios a la vez.
    """
    if context.trade_engine is None:
        return
    try:
        context.trade_engine.drain_once()
    except Exception as exc:  # noqa: BLE001 - la aislacion es el objetivo.
        print(
            f"[trades] ciclo degradado: {type(exc).__name__}: {exc}",
            flush=True,
        )


def _drain_orderbook(
    context: IngestionContext,
    sampler: _OrderbookSampler,
    frontier: _OrderbookFrontier,
    now_ms: int,
) -> None:
    """Un ciclo del motor del LIBRO, junto al tick de velas y NO dentro de el.

    Tres cosas, con el MISMO now_ms del Clock inyectado (disparo determinista):
    1. drena los deltas (aplica al libro, publica resync ante hueco);
    2. si toca por CADENCIA, toma una MUESTRA (kind='sample', sin outbox) de cada libro
       vivo;
    3. por cada barra que CERRO (reloj de barra, opcion 3) de una vela activa, toma su
       FRONTERA (kind='frontier', por outbox). Fire-anyway (cond.5): si el libro de ese
       simbolo aun no sembro, take_frontier lo cuenta y no publica (5.21), no fabrica.

    CON FAULT ISOLATION POR ITEM, como los trades: una muestra o una frontera que fallan
    degradan SOLO ese item; el resto del ciclo sigue. Si la excepcion subiera, un fallo
    del libro tumbaria tambien la ingesta de velas.
    """
    if context.orderbook_engine is None or context.orderbook_snapshot is None:
        return
    try:
        context.orderbook_engine.drain_once()
        _muestrear(context, sampler, now_ms)
        _fronterizar(context, frontier, now_ms)
    except Exception as exc:  # noqa: BLE001 - la aislacion es el objetivo.
        print(
            f"[orderbook] ciclo degradado: {type(exc).__name__}: {exc}",
            flush=True,
        )


def _muestrear(
    context: IngestionContext, sampler: _OrderbookSampler, now_ms: int
) -> None:
    """La MUESTRA a cadencia (~1/s): una foto intra-ventana de cada libro vivo."""
    engine = context.orderbook_engine
    snapshot = context.orderbook_snapshot
    if engine is None or snapshot is None or not sampler.due(now_ms):
        return
    dur = _SAMPLE_TIMEFRAME.duration_ms
    open_time = (now_ms // dur) * dur
    close_time = open_time + dur
    for book in engine.books().values():
        try:
            snapshot.take_sample(
                book,
                timeframe=_SAMPLE_TIMEFRAME,
                open_time=open_time,
                close_time=close_time,
                sample_time=now_ms,
            )
        except Exception as exc:  # noqa: BLE001 - aislar POR libro, no por ciclo.
            print(
                f"[orderbook] muestra degradada: {type(exc).__name__}: {exc}",
                flush=True,
            )


def _fronterizar(
    context: IngestionContext, frontier: _OrderbookFrontier, now_ms: int
) -> None:
    """La FRONTERA por reloj de barra: por cada barra de vela activa que cerro, la foto
    as-of de su libro. El simbolo sin libro sembrado dispara igual (take_frontier -> no
    publica, cond.5): fire-anyway honesto.
    """
    engine = context.orderbook_engine
    snapshot = context.orderbook_snapshot
    if engine is None or snapshot is None:
        return
    for key, tf, open_time, close_time in frontier.due_bars(
        _active_candle_keys(context), now_ms
    ):
        ob_stream_id = MarketStreamKey(
            exchange=key.exchange,
            market_type=key.market_type,
            symbol=key.symbol,
            data_kind=MarketDataKind.ORDERBOOK,
        ).as_stream_key()
        # 'or OrderbookBook()': un simbolo sin libro sembrado dispara igual -- el libro
        # vacio hace que take_frontier no publique (5.21). Fire-anyway sin fabricar.
        book = engine.book_for(ob_stream_id) or OrderbookBook()
        try:
            snapshot.take_frontier(
                book, timeframe=tf, open_time=open_time, close_time=close_time
            )
        except Exception as exc:  # noqa: BLE001 - aislar POR barra, no por ciclo.
            print(
                f"[orderbook] frontera degradada: {type(exc).__name__}: {exc}",
                flush=True,
            )


def main() -> None:
    """Cablea, sincroniza el catalogo, arranca el ingestor y entra en el bucle."""
    tick_ms = int(os.environ.get("CE_V5_INGESTION_TICK_MS", str(_DEFAULT_TICK_MS)))
    sample_ms = int(
        os.environ.get("CE_V5_ORDERBOOK_SAMPLE_MS", str(_DEFAULT_ORDERBOOK_SAMPLE_MS))
    )
    context = build_context()
    sampler = _OrderbookSampler(sample_ms)
    frontier = _OrderbookFrontier()

    stop = _StopSignal()
    signal.signal(signal.SIGINT, stop.request)
    signal.signal(signal.SIGTERM, stop.request)

    try:
        # ANTES del primer reconcile: si el catalogo esta vacio, el connector real
        # descartaria todo mensaje (no puede resolver el simbolo canonico).
        resultado = sync_catalog(context.datasource, context.catalog)
        print(
            f"[ingesta] catalogo sincronizado: {resultado.active} instrumentos "
            f"activos, {resultado.deactivated} delistados, "
            f"{resultado.not_representable} no representables.",
            flush=True,
        )

        # El ingestor es GLOBAL y sin capacidades sensibles: el gate no lo deniega.
        context.supervisor.initialize(context.instance_id)
        context.supervisor.start(context.instance_id)
        print("[ingesta] ingestor en RUNNING. Ctrl-C para parar.", flush=True)
        # DECLARADO, no supuesto: si el feed cableado no sirve trades, el worker corre
        # sin ese motor y hay que verlo en el arranque, no deducirlo de un contador que
        # nunca sube.
        print(
            "[trades] motor ACTIVO sobre el mismo conector."
            if context.trade_engine is not None
            else "[trades] motor AUSENTE: el feed cableado no sirve trades.",
            flush=True,
        )
        # DECLARADO como los trades: si el feed no sirve libro, el worker corre sin ese
        # motor y hay que verlo en el arranque, no deducirlo de un contador que no sube.
        print(
            "[orderbook] motor ACTIVO sobre el mismo conector (muestras a cadencia + "
            "frontera por reloj de barra)."
            if context.orderbook_engine is not None
            else "[orderbook] motor AUSENTE: el feed cableado no sirve libro.",
            flush=True,
        )

        ciclos = 0
        while not stop.requested:
            # UN solo now_ms por tick, del Clock inyectado: muestra y frontera comparten
            # instante (disparo determinista; un SimulatedClock reproduce el escenario).
            now_ms = context.clock.now_ms()
            context.component.tick()  # reconcile + drain, con fault isolation propia.
            _drain_trades(context)  # motor de trades, con la suya.
            _drain_orderbook(context, sampler, frontier, now_ms)  # libro, la suya.
            ciclos += 1
            if ciclos % _METRICS_EVERY == 0:
                _print_metrics(context)
            time.sleep(tick_ms / 1000.0)
    finally:
        # Apagado LIMPIO: cerrar streams, teardown y soltar conexiones. Sin colgar.
        print("[ingesta] parando: cerrando streams y conexiones...", flush=True)
        context.supervisor.stop(context.instance_id)
        context.supervisor.unload(context.instance_id)
        context.close()
        print("[ingesta] parado.", flush=True)


if __name__ == "__main__":
    main()
