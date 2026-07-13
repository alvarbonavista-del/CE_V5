"""La API es un proceso propio (ADR-002).

Arranque: python -m ce_v5.entrypoints.api

LOS BUCLES DE FONDO SE ARRANCAN AQUI, NO EN create_app: los tests construyen la
aplicacion cientos de veces y no queremos un hilo de fondo en cada una. Los tests de
integracion llaman a drain_once() explicitamente, que ademas es mas determinista que
esperar a que un bucle pase por ahi.

La API publica y consume eventos, pero NO EVALUA REGLAS NI EJECUTA ORDENES: eso es de
otras piezas, para siempre (DOC_ROADMAP, ficha P06b).
"""

from __future__ import annotations

import os

import uvicorn

from ce_v5.entrypoints.api.app import create_app
from ce_v5.entrypoints.api.background import (
    OutboxDrainer,
    PolicyInvalidationSubscriber,
)
from ce_v5.entrypoints.api.composition import build_context

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8000


def main() -> None:
    """Cablea el contexto, arranca los bucles de fondo y sirve."""
    host = os.environ.get("CE_V5_API_HOST", _DEFAULT_HOST)
    port = int(os.environ.get("CE_V5_API_PORT", str(_DEFAULT_PORT)))
    context = build_context()
    app = create_app(context)

    drainer = OutboxDrainer(context.publisher)
    subscriber = PolicyInvalidationSubscriber(context.bus, context.invalidator)
    # Parada LIMPIA: los dos bucles se detienen al salir, pase lo que pase.
    with drainer, subscriber:
        uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
