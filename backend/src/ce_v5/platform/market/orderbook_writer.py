"""Puerto de escritura de la persistencia de orderbook (P07c; ADR-013, regla 5.20).

El PUERTO pertenece a quien lo CONSUME (patron hexagonal): el engine de snapshot +
cableado (Tanda III) lo usara; infra lo cumple por FORMA (Protocol estructural). Se
declara aqui, en platform, para que infra NO importe platform (CE-14): el writer de
infra (market_orderbook.py) satisface esta forma sin verla.

DOS CAMINOS, como el dictamen de Central separa las dos variantes del snapshot:

- persist_and_enqueue: para lo que se PUBLICA -- el frontier (snapshot as-of close_time)
  y el resync. Persiste el hecho Y lo encola en LA MISMA transaccion (ADR-013): o estan
  las dos filas, o ninguna. Igual que el footprint y las velas.

- persist_sample: para la muestra intra-ventana. Se PERSISTE SIN outbox, como los trades
  (que no se publican al bus): sin publicacion no hay pareja persistida/publicada que
  pueda divergir, asi que meter una outbox aqui seria ceremonia sin invariante.
"""

from __future__ import annotations

from typing import Protocol

from source.families.orderbook import (
    OrderbookResyncedPayload,
    OrderbookSnapshotPayload,
)


class OrderbookWriterPort(Protocol):
    """Escritura de la persistencia de orderbook. La cumple infra (market_orderbook)."""

    def persist_and_enqueue(
        self,
        envelope_json: bytes,
        payload: OrderbookSnapshotPayload | OrderbookResyncedPayload,
        event_type: str,
        stream_key: str,
        idempotency_key: str,
        event_time: int,
    ) -> bool:
        """Persiste el hecho PUBLICADO (frontier o resync) Y lo encola, atomico
        (ADR-013).

        Un frontier va a market_orderbook_snapshot (kind='frontier'); un resync a
        market_orderbook_discontinuity. En ambos casos, el INSERT del hecho y el de la
        outbox comparten transaccion: encolar sin persistir publicaria algo que el
        historico no puede demostrar; persistir sin encolar dejaria un hecho que nadie
        publico. Devuelve False si ya existia (dedup por su clave): ni duplica ni
        reencola.
        """
        ...

    def persist_sample(
        self,
        payload: OrderbookSnapshotPayload,
        event_time: int,
    ) -> bool:
        """Persiste una MUESTRA intra-ventana (kind='sample') SIN outbox. False si ya
        estaba.

        Como los trades: no se publica, asi que no hay outbox. Idempotente por su
        idempotency_key (que incluye sample_time ademas de K, cadencia, ventana y
        formula_version): reprocesar la misma muestra no la duplica.
        """
        ...
