"""Consumidor idempotente sobre el inbox de P02b (ADR-013).

Recibe mensajes del bus por el puerto EventBus (nunca la API nativa;
REST-15) y garantiza idempotencia real de consumidor con efectos: registra
su procesamiento en el inbox (consumer_group/handler/idempotency_key) y
APLICA el efecto en la MISMA transaccion; solo hace ACK tras confirmar el
efecto (at-least-once + dedup). Un mensaje ya procesado se ACKea sin repetir
el efecto. Los mensajes cuyo efecto falla no se ACKean: se reintentan
(reclaim) y, superados max_attempts, se enrutan a la DLQ observable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from ce_v5.core.bus import BusMessage, DlqReason, EventBus, ReceivedMessage
from ce_v5.infra.db.ports import Database, Session

Handler = Callable[[Session, BusMessage], None]

_INSERT_INBOX = (
    "INSERT INTO inbox (consumer_group, handler, idempotency_key) "
    "VALUES (%(cg)s, %(h)s, %(ik)s) "
    "ON CONFLICT DO NOTHING RETURNING idempotency_key"
)


class _Outcome(Enum):
    PROCESSED = "processed"
    DEDUPLICATED = "deduplicated"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"


@dataclass(frozen=True, slots=True)
class ConsumeResult:
    """Recuento del resultado de una pasada del consumidor."""

    processed: int
    deduplicated: int
    failed: int
    dead_lettered: int


@dataclass(frozen=True, slots=True)
class InboxConsumer:
    """Consume del bus aplicando efectos idempotentes via el inbox (ADR-013)."""

    db: Database
    bus: EventBus
    handler: Handler
    consumer_group: str
    handler_name: str

    def run_once(
        self,
        topic: str,
        consumer_name: str,
        *,
        batch_size: int = 100,
        block_ms: int = 1000,
        min_idle_ms: int = 30_000,
        max_attempts: int = 5,
    ) -> ConsumeResult:
        """Reclama pendientes, consume nuevos y procesa el lote; da el recuento."""
        self.bus.ensure_group(topic, self.consumer_group)
        stale = self.bus.claim_stale(
            topic,
            self.consumer_group,
            consumer_name,
            min_idle_ms=min_idle_ms,
            max_messages=batch_size,
        )
        fresh = self.bus.poll(
            topic,
            self.consumer_group,
            consumer_name,
            max_messages=batch_size,
            block_ms=block_ms,
        )
        processed = 0
        deduplicated = 0
        failed = 0
        dead_lettered = 0
        for received in (*stale, *fresh):
            outcome = self._process(received, max_attempts=max_attempts)
            if outcome is _Outcome.PROCESSED:
                processed += 1
            elif outcome is _Outcome.DEDUPLICATED:
                deduplicated += 1
            elif outcome is _Outcome.FAILED:
                failed += 1
            else:
                dead_lettered += 1
        return ConsumeResult(
            processed=processed,
            deduplicated=deduplicated,
            failed=failed,
            dead_lettered=dead_lettered,
        )

    def _process(self, received: ReceivedMessage, *, max_attempts: int) -> _Outcome:
        if received.delivery.delivery_count > max_attempts:
            self.bus.dead_letter(
                received,
                DlqReason(
                    reason_code="max_attempts_exceeded",
                    attempts=received.delivery.delivery_count,
                    detail=f"handler={self.handler_name}",
                ),
            )
            return _Outcome.DEAD_LETTERED
        try:
            applied = self._apply(received.message)
        except Exception:
            # Fallo del efecto: no se ACKea. El mensaje queda pendiente y se
            # reintenta en la proxima pasada (reclaim); superado max_attempts
            # ira a la DLQ. La resiliencia del worker exige no tumbar el lote.
            return _Outcome.FAILED
        self.bus.ack(received.delivery)
        return _Outcome.PROCESSED if applied else _Outcome.DEDUPLICATED

    def _apply(self, message: BusMessage) -> bool:
        with self.db.transaction() as session:
            row = session.fetchone(
                _INSERT_INBOX,
                {
                    "cg": self.consumer_group,
                    "h": self.handler_name,
                    "ik": message.idempotency_key,
                },
            )
            if row is None:
                return False
            self.handler(session, message)
            return True
