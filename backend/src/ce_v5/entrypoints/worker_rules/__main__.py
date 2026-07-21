"""El worker de reglas es un proceso propio (ADR-002, P08 Bloque 7).

Arranque: python -m ce_v5.entrypoints.worker_rules

EL BUCLE VIVE AQUI, NO EN build_context: como la API y el ingestor, la construccion
cablea y punto; el bucle lo arranca el proceso. Un hilo de fondo escondido en la
construccion es un hilo que los tests no controlan. Y NADA se ejecuta en el import: este
modulo solo define main() y lo llama bajo __main__.

GUARDIA 5.20: build_context usa RulesDbConfig.from_env, que ABORTA si el entorno trae el
DSN de la aplicacion, el del operador o el de ingesta. Este proceso solo porta la
credencial de reglas.
"""

from __future__ import annotations

import os
import signal
import time
from types import FrameType

from ce_v5.entrypoints.worker_rules.composition import RulesContext, build_context

_DEFAULT_TICK_MS = 1000
_METRICS_EVERY = 10  # cada cuantos ciclos se imprime el resumen observable.
_CONSUMER_NAME_ENV = "CE_V5_RULES_CONSUMER_NAME"
_TICK_MS_ENV = "CE_V5_RULES_TICK_MS"


class _StopSignal:
    """Bandera de parada. SIGINT/SIGTERM la activan; el bucle la consulta."""

    def __init__(self) -> None:
        self._stop = False

    def request(self, _signum: int, _frame: FrameType | None) -> None:
        self._stop = True

    @property
    def requested(self) -> bool:
        return self._stop


def _print_metrics(consumed: int, deduplicated: int, published: int) -> None:
    """Resumen OBSERVABLE: sin esto, un motor que no evalua parece sano."""
    print(
        f"[reglas] velas_procesadas={consumed} deduplicadas={deduplicated} "
        f"eventos_publicados={published}",
        flush=True,
    )


def main() -> None:
    """Cablea el motor y entra en el bucle de consumo + drenado."""
    tick_ms = int(os.environ.get(_TICK_MS_ENV, str(_DEFAULT_TICK_MS)))
    consumer_name = os.environ.get(_CONSUMER_NAME_ENV, "rules-1").strip() or "rules-1"
    context: RulesContext = build_context(consumer_name=consumer_name)

    stop = _StopSignal()
    signal.signal(signal.SIGINT, stop.request)
    signal.signal(signal.SIGTERM, stop.request)

    try:
        print(
            f"[reglas] motor en marcha (consumidor={consumer_name}). "
            "Ctrl-C para parar.",
            flush=True,
        )
        ciclos = 0
        consumidas = 0
        dedup = 0
        publicados = 0
        while not stop.requested:
            # Un tick = consumir market.candle_closed y drenar la propia outbox. El
            # bloqueo del poll ya marca el ritmo; el sleep solo evita girar en vacio.
            consumed, published = context.tick(block_ms=tick_ms)
            consumidas += consumed.processed
            dedup += consumed.deduplicated
            publicados += published
            ciclos += 1
            if ciclos % _METRICS_EVERY == 0:
                _print_metrics(consumidas, dedup, publicados)
            if consumed.processed == 0 and published == 0:
                time.sleep(tick_ms / 1000.0)
    finally:
        # Apagado LIMPIO: soltar conexiones sin colgar.
        print("[reglas] parando: cerrando conexiones...", flush=True)
        context.close()
        print("[reglas] parado.", flush=True)


if __name__ == "__main__":
    main()
