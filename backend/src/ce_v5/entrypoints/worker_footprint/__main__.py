"""El worker de footprint es un proceso propio (ADR-002, P07b 3b-1, CE-14).

Arranque: python -m ce_v5.entrypoints.worker_footprint

EL BUCLE VIVE AQUI, NO EN build_context: como el worker de reglas, la construccion
cablea y punto; el bucle lo arranca el proceso. Un hilo de fondo escondido en la
construccion es un hilo que los tests no controlan. Y NADA se ejecuta en el import: este
modulo solo define main() y lo llama bajo __main__.

CE-14: es un CONSUMIDOR del bus; NO toca el nucleo de ingesta. Corre en ce_v5_ingestion.

GUARDIA 5.20: build_context usa IngestionDbConfig.from_env, que ABORTA si el entorno
trae el DSN de la app, el del operador o el de reglas. Este proceso solo porta la
credencial de ingesta.
"""

from __future__ import annotations

import os
import signal
import time
from types import FrameType

from ce_v5.entrypoints.worker_footprint.composition import (
    FootprintContext,
    build_context,
)

_DEFAULT_TICK_MS = 1000
_METRICS_EVERY = 10  # cada cuantos ciclos se imprime el resumen observable.
_CONSUMER_NAME_ENV = "CE_V5_FOOTPRINT_CONSUMER_NAME"
_TICK_MS_ENV = "CE_V5_FOOTPRINT_TICK_MS"


class _StopSignal:
    """Bandera de parada. SIGINT/SIGTERM la activan; el bucle la consulta."""

    def __init__(self) -> None:
        self._stop = False

    def request(self, _signum: int, _frame: FrameType | None) -> None:
        self._stop = True

    @property
    def requested(self) -> bool:
        return self._stop


def _print_metrics(context: FootprintContext, published: int) -> None:
    """Resumen OBSERVABLE. CELDAS-POR-BARRA (la metrica que Central pidio) sale del
    motor: sin cap, lo que se vigila es cuantas produce cada barra y el maximo visto.
    """
    m = context.engine.metrics
    print(
        f"[footprint] cerrados={m.footprints_closed} "
        f"corregidos={m.footprints_corrected} duplicados={m.duplicates_skipped} "
        f"incompletas={m.incomplete_bars} celdas_ultima={m.cells_last_bar} "
        f"celdas_max={m.max_cells_in_bar} eventos_publicados={published}",
        flush=True,
    )


def main() -> None:
    """Cablea el motor y entra en el bucle de consumo + drenado."""
    tick_ms = int(os.environ.get(_TICK_MS_ENV, str(_DEFAULT_TICK_MS)))
    consumer_name = (
        os.environ.get(_CONSUMER_NAME_ENV, "footprint-1").strip() or "footprint-1"
    )
    context: FootprintContext = build_context(consumer_name=consumer_name)

    stop = _StopSignal()
    signal.signal(signal.SIGINT, stop.request)
    signal.signal(signal.SIGTERM, stop.request)

    try:
        print(
            f"[footprint] motor en marcha (consumidor={consumer_name}). "
            "Ctrl-C para parar.",
            flush=True,
        )
        ciclos = 0
        publicados = 0
        while not stop.requested:
            # Un tick = consumir las velas cerradas/corregidas y drenar su outbox.
            # El bloqueo del poll ya marca el ritmo; el sleep solo evita girar en vacio.
            consumed, published = context.tick(block_ms=tick_ms)
            publicados += published
            ciclos += 1
            if ciclos % _METRICS_EVERY == 0:
                _print_metrics(context, publicados)
            if consumed.processed == 0 and consumed.skipped == 0 and published == 0:
                time.sleep(tick_ms / 1000.0)
    finally:
        # Apagado LIMPIO: soltar conexiones sin colgar.
        print("[footprint] parando: cerrando conexiones...", flush=True)
        context.close()
        print("[footprint] parado.", flush=True)


if __name__ == "__main__":
    main()
