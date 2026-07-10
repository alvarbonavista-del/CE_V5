"""Wire format between BusMessage and Redis Streams entries (ADR-013)."""

from __future__ import annotations

from ce_v5.core.bus import BusMessage, ConsumeError

type RedisFields = dict[
    bytes | bytearray | memoryview[int] | str | int | float,
    bytes | bytearray | memoryview[int] | str | int | float,
]

_EVENT_ID = b"event_id"
_EVENT_TYPE = b"event_type"
_STREAM_KEY = b"stream_key"
_IDEMPOTENCY_KEY = b"idempotency_key"
_ENVELOPE = b"envelope"


def to_fields(message: BusMessage) -> RedisFields:
    """Flatten a BusMessage into Redis stream fields (all bytes)."""
    return {
        _EVENT_ID: message.event_id.encode("utf-8"),
        _EVENT_TYPE: message.event_type.encode("utf-8"),
        _STREAM_KEY: message.stream_key.encode("utf-8"),
        _IDEMPOTENCY_KEY: message.idempotency_key.encode("utf-8"),
        _ENVELOPE: message.envelope,
    }


def from_fields(fields: dict[bytes, bytes]) -> BusMessage:
    """Rebuild a BusMessage from Redis stream fields."""
    try:
        return BusMessage(
            event_id=fields[_EVENT_ID].decode("utf-8"),
            event_type=fields[_EVENT_TYPE].decode("utf-8"),
            stream_key=fields[_STREAM_KEY].decode("utf-8"),
            idempotency_key=fields[_IDEMPOTENCY_KEY].decode("utf-8"),
            envelope=fields[_ENVELOPE],
        )
    except KeyError as exc:
        raise ConsumeError(f"malformed bus entry, missing field {exc}") from exc
