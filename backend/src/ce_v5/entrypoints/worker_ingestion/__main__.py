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

_DEFAULT_TICK_MS = 1000
_METRICS_EVERY = 10  # cada cuantos ciclos se imprime el resumen observable.


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


def main() -> None:
    """Cablea, sincroniza el catalogo, arranca el ingestor y entra en el bucle."""
    tick_ms = int(os.environ.get("CE_V5_INGESTION_TICK_MS", str(_DEFAULT_TICK_MS)))
    context = build_context()

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

        ciclos = 0
        while not stop.requested:
            context.component.tick()  # reconcile + drain, con fault isolation propia.
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
