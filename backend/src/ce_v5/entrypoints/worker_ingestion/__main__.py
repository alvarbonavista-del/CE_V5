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
from types import FrameType

from ce_v5.entrypoints.worker_ingestion.catalog_sync import sync_catalog
from ce_v5.entrypoints.worker_ingestion.composition import (
    IngestionContext,
    build_context,
)
from source.families.market import Timeframe

_DEFAULT_TICK_MS = 1000
_METRICS_EVERY = 10  # cada cuantos ciclos se imprime el resumen observable.
# Cadencia por defecto del MUESTREO del libro (~1/s): entra en la idempotency_key del
# snapshot (cond.1). No depende de candle_closed; la FRONTERA (as-of cierre de barra)
# queda ELEVADA (su trigger no tiene enganche aditivo aqui, ver Tanda IV parcial).
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
    context: IngestionContext, sampler: _OrderbookSampler, now_ms: int
) -> None:
    """Un ciclo del motor del LIBRO, junto al tick de velas y NO dentro de el.

    Drena los deltas (aplica al libro, publica resync ante hueco) y, si toca por
    cadencia, toma una MUESTRA (kind='sample', sin outbox) de cada libro vivo. La
    FRONTERA (as-of el cierre de barra) NO se toma aqui: su trigger es candle_closed y
    el nucleo no lo expone de forma aditiva; queda ELEVADO a Central (Tanda IV parcial).
    take_frontier ya lo ejercitan los tests en frio de la Tanda III: no es
    codigo muerto.

    CON SU PROPIA FAULT ISOLATION, como el de trades: un poll o una muestra que fallan
    degradan ESTE ciclo; el siguiente reintenta. Si la excepcion subiera, un fallo del
    libro tumbaria tambien la ingesta de velas.
    """
    if context.orderbook_engine is None or context.orderbook_snapshot is None:
        return
    try:
        context.orderbook_engine.drain_once()
        if not sampler.due(now_ms):
            return
        open_time = (
            now_ms // _SAMPLE_TIMEFRAME.duration_ms
        ) * _SAMPLE_TIMEFRAME.duration_ms
        close_time = open_time + _SAMPLE_TIMEFRAME.duration_ms
        for book in context.orderbook_engine.books().values():
            try:
                context.orderbook_snapshot.take_sample(
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
    except Exception as exc:  # noqa: BLE001 - la aislacion es el objetivo.
        print(
            f"[orderbook] ciclo degradado: {type(exc).__name__}: {exc}",
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
            "[orderbook] motor ACTIVO sobre el mismo conector (frontera ELEVADA; "
            "solo muestras a cadencia)."
            if context.orderbook_engine is not None
            else "[orderbook] motor AUSENTE: el feed cableado no sirve libro.",
            flush=True,
        )

        ciclos = 0
        while not stop.requested:
            context.component.tick()  # reconcile + drain, con fault isolation propia.
            _drain_trades(context)  # motor de trades, con la suya.
            _drain_orderbook(
                context, sampler, int(time.time() * 1000)
            )  # libro, la suya.
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
