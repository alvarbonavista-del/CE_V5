"""Behaviour tests for the in-memory EventBus reference double (ADR-013)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from ce_v5.core.bus import BusMessage, DlqReason, EventBus, Offset, UnknownOffsetError


def _message(seq: int, stream_key: str) -> BusMessage:
    return BusMessage(
        event_id=f"evt-{seq}",
        event_type="market.candle_closed",
        stream_key=stream_key,
        idempotency_key=f"{stream_key}:{seq}",
        envelope=b"{}",
    )


def test_publish_returns_incrementing_offsets(in_memory_bus: EventBus) -> None:
    first = in_memory_bus.publish("market", _message(1, "A"))
    second = in_memory_bus.publish("market", _message(2, "A"))
    assert first == Offset("1")
    assert second == Offset("2")


def test_ordering_preserved_per_stream_key(in_memory_bus: EventBus) -> None:
    in_memory_bus.ensure_group("market", "rules")
    in_memory_bus.publish("market", _message(1, "A"))
    in_memory_bus.publish("market", _message(2, "B"))
    in_memory_bus.publish("market", _message(3, "A"))
    received = in_memory_bus.poll("market", "rules", "c1", max_messages=10, block_ms=0)
    a_events = [r.message.event_id for r in received if r.message.stream_key == "A"]
    assert a_events == ["evt-1", "evt-3"]


def test_new_messages_delivered_once(in_memory_bus: EventBus) -> None:
    in_memory_bus.ensure_group("market", "rules")
    in_memory_bus.publish("market", _message(1, "A"))
    first = in_memory_bus.poll("market", "rules", "c1", max_messages=10, block_ms=0)
    second = in_memory_bus.poll("market", "rules", "c1", max_messages=10, block_ms=0)
    assert len(first) == 1
    assert second == ()


def test_ack_prevents_reclaim(
    in_memory_bus: EventBus, advance_time: Callable[[int], None]
) -> None:
    in_memory_bus.ensure_group("market", "rules")
    in_memory_bus.publish("market", _message(1, "A"))
    received = in_memory_bus.poll("market", "rules", "c1", max_messages=10, block_ms=0)
    in_memory_bus.ack(received[0].delivery)
    advance_time(60_000)
    reclaimed = in_memory_bus.claim_stale(
        "market", "rules", "c2", min_idle_ms=30_000, max_messages=10
    )
    assert reclaimed == ()


def test_unacked_message_reclaimed_after_idle(
    in_memory_bus: EventBus, advance_time: Callable[[int], None]
) -> None:
    in_memory_bus.ensure_group("market", "rules")
    in_memory_bus.publish("market", _message(1, "A"))
    in_memory_bus.poll("market", "rules", "c1", max_messages=10, block_ms=0)
    before = in_memory_bus.claim_stale(
        "market", "rules", "c2", min_idle_ms=30_000, max_messages=10
    )
    advance_time(60_000)
    after = in_memory_bus.claim_stale(
        "market", "rules", "c2", min_idle_ms=30_000, max_messages=10
    )
    assert before == ()
    assert len(after) == 1
    assert after[0].delivery.delivery_count == 2


def test_replay_from_beginning_and_offset(in_memory_bus: EventBus) -> None:
    in_memory_bus.publish("market", _message(1, "A"))
    in_memory_bus.publish("market", _message(2, "A"))
    in_memory_bus.publish("market", _message(3, "A"))
    all_msgs = in_memory_bus.replay("market", start=None, max_messages=10)
    tail = in_memory_bus.replay("market", start=Offset("1"), max_messages=10)
    assert [r.message.event_id for r in all_msgs] == ["evt-1", "evt-2", "evt-3"]
    assert [r.message.event_id for r in tail] == ["evt-2", "evt-3"]


def test_replay_unknown_offset_raises(in_memory_bus: EventBus) -> None:
    in_memory_bus.publish("market", _message(1, "A"))
    with pytest.raises(UnknownOffsetError):
        in_memory_bus.replay("market", start=Offset("99"), max_messages=10)


def test_dead_letter_removes_from_pending(
    in_memory_bus: EventBus, advance_time: Callable[[int], None]
) -> None:
    in_memory_bus.ensure_group("market", "rules")
    in_memory_bus.publish("market", _message(1, "A"))
    received = in_memory_bus.poll("market", "rules", "c1", max_messages=10, block_ms=0)
    in_memory_bus.dead_letter(
        received[0], DlqReason(reason_code="handler_error", attempts=5, detail="boom")
    )
    advance_time(60_000)
    reclaimed = in_memory_bus.claim_stale(
        "market", "rules", "c2", min_idle_ms=30_000, max_messages=10
    )
    assert reclaimed == ()


# --- latest_offset (CA-12) ----------------------------------------------------------


def test_latest_offset_de_un_topic_vacio_es_none(in_memory_bus: EventBus) -> None:
    assert in_memory_bus.latest_offset("market") is None


def test_latest_offset_apunta_al_ultimo_publicado(in_memory_bus: EventBus) -> None:
    offsets = [in_memory_bus.publish("market", _message(i, "A")) for i in range(1, 6)]
    assert in_memory_bus.latest_offset("market") == offsets[-1]


def test_el_cursor_es_exclusivo_y_solo_trae_lo_nuevo(in_memory_bus: EventBus) -> None:
    # latest_offset significa "ya visto": replay desde el NO reentrega el ultimo...
    for seq in range(1, 4):
        in_memory_bus.publish("market", _message(seq, "A"))
    cursor = in_memory_bus.latest_offset("market")
    assert cursor is not None
    assert in_memory_bus.replay("market", start=cursor, max_messages=10) == ()

    # ...pero SI trae el siguiente que se publique.
    in_memory_bus.publish("market", _message(99, "A"))
    nuevos = in_memory_bus.replay("market", start=cursor, max_messages=10)
    assert [r.message.idempotency_key for r in nuevos] == ["A:99"]
