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

    def record_discontinuity(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        from_sequence: int,
        to_sequence: int | None,
        event_time: int,
        reason: str,
    ) -> bool:
        """Apunta una discontinuidad SIN publicarla (espejo de record_gap). True si
        entro.

        Registra la AUSENCIA de continuidad de una RECONEXION -- que se resuelve
        re-sembrando, no encadenando, asi que el motor no ve el hueco por un delta --
        para que el frontier de las barras solapadas se marque incompleto (fail-safe,
        cond.3). El resync PUBLICADO (hueco detectado por el motor) va por
        persist_and_enqueue, que persiste la discontinuidad Y la encola atomico.
        Idempotente por el UNIQUE.
        """
        ...


class OrderbookReaderPort(Protocol):
    """Lectura de discontinuidades del libro. La cumple infra (market_orderbook).

    Espejo de la parte de lectura del reader del footprint (overlapping_gaps): el motor
    de snapshot la usa para el is_complete del frontier -- si una discontinuidad solapa
    la barra, la foto de cierre no es de fiar (cond.3).
    """

    def overlapping_discontinuities(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        window_start: int,
        window_end: int,
    ) -> tuple[tuple[int, int | None, int], ...]:
        """Las discontinuidades cuyo event_time cae en [window_start, window_end).

        Vacia = ningun resync en la ventana (el frontier puede ser completo si el libro
        lo estaba). No vacia = hubo un resync dentro de la barra -> is_complete=False.
        Cada fila es (from_sequence, to_sequence, event_time).
        """
        ...
