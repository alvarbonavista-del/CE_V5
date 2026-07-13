"""Redis Streams adapter implementing the EventBus port (ADR-013).

The only module in P03 that knows the native Redis API (REST-15). Provides
at-least-once delivery, consumer groups, ordering per ``stream_key`` via
basic partitioning, stale-message reclaim, an observable DLQ and replay by
offset.
"""

from __future__ import annotations

import zlib
from typing import TYPE_CHECKING, Any

import redis
from redis.exceptions import RedisError, ResponseError

from ce_v5.core.bus import (
    BusMessage,
    ConsumeError,
    Delivery,
    DlqReason,
    EventBus,
    Offset,
    PublishError,
    ReceivedMessage,
    UnknownOffsetError,
)
from ce_v5.infra.bus_redis.config import RedisBusConfig
from ce_v5.infra.bus_redis.wire import RedisFields, from_fields, to_fields

_OFFSET_SEP = "|"

type RedisStreams = dict[
    bytes | str | memoryview[int],
    int | bytes | str | memoryview[int],
]
type RedisIds = list[int | bytes | str | memoryview[int]]


class RedisEventBus:
    """EventBus port backed by Redis Streams."""

    def __init__(self, client: redis.Redis, config: RedisBusConfig) -> None:
        self._client = client
        self._namespace = config.namespace
        self._partitions = config.partitions
        self._dlq_owner = config.dlq_owner
        self._dlq_procedure = config.dlq_procedure

    def _stream(self, topic: str, partition: int) -> str:
        return f"{self._namespace}:{topic}:{partition}"

    def _dlq_stream(self, topic: str) -> str:
        return f"{self._namespace}:{topic}:dlq"

    def _all_streams(self, topic: str) -> list[str]:
        return [self._stream(topic, p) for p in range(self._partitions)]

    def _partition_for(self, stream_key: str) -> int:
        return zlib.crc32(stream_key.encode("utf-8")) % self._partitions

    def _partition_of_stream(self, stream_name: bytes) -> int:
        return int(stream_name.decode("utf-8").rsplit(":", 1)[1])

    def _encode_offset(self, partition: int, entry_id: str) -> Offset:
        return Offset(f"{partition}{_OFFSET_SEP}{entry_id}")

    def _decode_offset(self, offset: Offset) -> tuple[int, str]:
        partition_text, _, entry_id = offset.value.partition(_OFFSET_SEP)
        if not entry_id:
            raise UnknownOffsetError(f"malformed offset {offset.value!r}")
        try:
            partition = int(partition_text)
        except ValueError:
            raise UnknownOffsetError(f"malformed offset {offset.value!r}") from None
        return partition, entry_id

    @staticmethod
    def _parse_id(raw: str) -> tuple[int, int]:
        ms_text, _, seq_text = raw.partition("-")
        return int(ms_text), int(seq_text or "0")

    def _to_received(
        self,
        topic: str,
        consumer_group: str,
        partition: int,
        entry_id: bytes,
        fields: dict[bytes, bytes],
        *,
        delivery_count: int,
    ) -> ReceivedMessage:
        return ReceivedMessage(
            message=from_fields(fields),
            delivery=Delivery(
                topic=topic,
                consumer_group=consumer_group,
                offset=self._encode_offset(partition, entry_id.decode("utf-8")),
                delivery_count=delivery_count,
            ),
        )

    def publish(self, topic: str, message: BusMessage) -> Offset:
        partition = self._partition_for(message.stream_key)
        stream = self._stream(topic, partition)
        try:
            raw_id: Any = self._client.xadd(stream, to_fields(message))
        except RedisError as exc:
            raise PublishError(f"xadd to {stream!r} failed: {exc}") from exc
        entry_id: str = raw_id.decode("utf-8")
        return self._encode_offset(partition, entry_id)

    def ensure_group(self, topic: str, consumer_group: str) -> None:
        for partition in range(self._partitions):
            stream = self._stream(topic, partition)
            try:
                self._client.xgroup_create(
                    stream, consumer_group, id="0", mkstream=True
                )
            except ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise ConsumeError(
                        f"xgroup_create on {stream!r} failed: {exc}"
                    ) from exc

    def poll(
        self,
        topic: str,
        consumer_group: str,
        consumer_name: str,
        *,
        max_messages: int,
        block_ms: int,
    ) -> tuple[ReceivedMessage, ...]:
        streams: RedisStreams = {}
        for stream in self._all_streams(topic):
            streams[stream] = ">"
        block = block_ms if block_ms > 0 else None
        try:
            raw: Any = self._client.xreadgroup(
                consumer_group, consumer_name, streams, count=max_messages, block=block
            )
        except RedisError as exc:
            raise ConsumeError(f"xreadgroup on {topic!r} failed: {exc}") from exc
        if raw is None:
            return ()
        received: list[ReceivedMessage] = []
        for stream_name, entries in raw:
            partition = self._partition_of_stream(stream_name)
            for entry_id, fields in entries:
                if len(received) >= max_messages:
                    break
                received.append(
                    self._to_received(
                        topic,
                        consumer_group,
                        partition,
                        entry_id,
                        fields,
                        delivery_count=1,
                    )
                )
        return tuple(received)

    def ack(self, delivery: Delivery) -> None:
        partition, entry_id = self._decode_offset(delivery.offset)
        stream = self._stream(delivery.topic, partition)
        try:
            self._client.xack(stream, delivery.consumer_group, entry_id)
        except RedisError as exc:
            raise ConsumeError(f"xack on {stream!r} failed: {exc}") from exc

    def claim_stale(
        self,
        topic: str,
        consumer_group: str,
        consumer_name: str,
        *,
        min_idle_ms: int,
        max_messages: int,
    ) -> tuple[ReceivedMessage, ...]:
        received: list[ReceivedMessage] = []
        for partition in range(self._partitions):
            if len(received) >= max_messages:
                break
            stream = self._stream(topic, partition)
            remaining = max_messages - len(received)
            try:
                pending: Any = self._client.xpending_range(
                    stream,
                    consumer_group,
                    min="-",
                    max="+",
                    count=remaining,
                    idle=min_idle_ms,
                )
            except RedisError as exc:
                raise ConsumeError(f"xpending on {stream!r} failed: {exc}") from exc
            if not pending:
                continue
            attempts_by_id: dict[bytes, int] = {}
            ids: RedisIds = []
            for item in pending:
                msg_id: Any = item["message_id"]
                ids.append(msg_id)
                attempts_by_id[msg_id] = int(item["times_delivered"])
            try:
                claimed: Any = self._client.xclaim(
                    stream, consumer_group, consumer_name, min_idle_ms, ids
                )
            except RedisError as exc:
                raise ConsumeError(f"xclaim on {stream!r} failed: {exc}") from exc
            for entry_id, fields in claimed:
                if not fields:
                    continue
                delivery_count = attempts_by_id.get(entry_id, 0) + 1
                received.append(
                    self._to_received(
                        topic,
                        consumer_group,
                        partition,
                        entry_id,
                        fields,
                        delivery_count=delivery_count,
                    )
                )
        return tuple(received)

    def dead_letter(self, received: ReceivedMessage, reason: DlqReason) -> None:
        topic = received.delivery.topic
        dlq = self._dlq_stream(topic)
        now_ms = self._server_now_ms()
        message = received.message
        entry: RedisFields = {
            b"event_id": message.event_id.encode("utf-8"),
            b"event_type": message.event_type.encode("utf-8"),
            b"stream_key": message.stream_key.encode("utf-8"),
            b"idempotency_key": message.idempotency_key.encode("utf-8"),
            b"envelope": message.envelope,
            b"reason_code": reason.reason_code.encode("utf-8"),
            b"attempts": str(reason.attempts).encode("utf-8"),
            b"detail": reason.detail.encode("utf-8"),
            b"owner": self._dlq_owner.encode("utf-8"),
            b"procedure": self._dlq_procedure.encode("utf-8"),
            b"first_seen_at": str(now_ms).encode("utf-8"),
            b"last_seen_at": str(now_ms).encode("utf-8"),
            b"origin_topic": topic.encode("utf-8"),
            b"origin_offset": received.delivery.offset.value.encode("utf-8"),
        }
        try:
            self._client.xadd(dlq, entry)
        except RedisError as exc:
            raise ConsumeError(f"xadd to DLQ {dlq!r} failed: {exc}") from exc
        self.ack(received.delivery)

    def latest_offset(self, topic: str) -> Offset | None:
        """El offset de la ULTIMA entrada del topic, o None si esta vacio.

        XREVRANGE con COUNT 1: coste O(1). NO recorre el historico, que es justo lo que
        esta primitiva viene a evitar.

        COHERENCIA CON replay (regla dura): un cursor de replay apunta a UNA particion
        (el offset la lleva codificada y replay solo lee ese stream). Con varias
        particiones, "el ultimo del topic" no existe como posicion unica: habria que
        elegir uno de N finales, y el cursor resultante saltaria las demas particiones
        en silencio. Antes que devolver un offset que no significa nada, se FALLA
        RUIDOSO: un cursor silenciosamente incorrecto entrega eventos antiguos como si
        fueran nuevos, que es la bomba que esta primitiva desactiva.
        """
        if self._partitions != 1:
            raise ConsumeError(
                "latest_offset no esta definido con varias particiones: el cursor de "
                f"replay apunta a UNA sola ({self._partitions} configuradas). Devolver "
                "el final de una de ellas saltaria las demas en silencio."
            )
        stream = self._stream(topic, 0)
        try:
            raw: Any = self._client.xrevrange(stream, max="+", min="-", count=1)
        except RedisError as exc:
            raise ConsumeError(f"xrevrange on {stream!r} failed: {exc}") from exc
        if not raw:
            return None
        entry_id = raw[0][0].decode("utf-8")
        return self._encode_offset(0, entry_id)

    def replay(
        self,
        topic: str,
        *,
        start: Offset | None,
        max_messages: int,
    ) -> tuple[ReceivedMessage, ...]:
        if start is None:
            return self._replay_all(topic, max_messages)
        partition, entry_id = self._decode_offset(start)
        stream = self._stream(topic, partition)
        self._ensure_offset_retained(stream, entry_id)
        try:
            raw: Any = self._client.xrange(
                stream, min=f"({entry_id}", max="+", count=max_messages
            )
        except RedisError as exc:
            raise ConsumeError(f"xrange on {stream!r} failed: {exc}") from exc
        received: list[ReceivedMessage] = []
        for raw_id, fields in raw:
            received.append(
                self._to_received(
                    topic, "", partition, raw_id, fields, delivery_count=0
                )
            )
        return tuple(received)

    def _replay_all(self, topic: str, max_messages: int) -> tuple[ReceivedMessage, ...]:
        collected: list[tuple[tuple[int, int], ReceivedMessage]] = []
        for partition in range(self._partitions):
            stream = self._stream(topic, partition)
            try:
                raw: Any = self._client.xrange(
                    stream, min="-", max="+", count=max_messages
                )
            except RedisError as exc:
                raise ConsumeError(f"xrange on {stream!r} failed: {exc}") from exc
            for raw_id, fields in raw:
                key = self._parse_id(raw_id.decode("utf-8"))
                collected.append(
                    (
                        key,
                        self._to_received(
                            topic, "", partition, raw_id, fields, delivery_count=0
                        ),
                    )
                )
        collected.sort(key=lambda item: item[0])
        return tuple(item[1] for item in collected[:max_messages])

    def _ensure_offset_retained(self, stream: str, entry_id: str) -> None:
        try:
            first: Any = self._client.xrange(stream, min="-", max="+", count=1)
        except RedisError as exc:
            raise ConsumeError(f"xrange on {stream!r} failed: {exc}") from exc
        if not first:
            raise UnknownOffsetError(
                f"stream {stream!r} has no retained history for replay"
            )
        first_id: str = first[0][0].decode("utf-8")
        if self._parse_id(entry_id) < self._parse_id(first_id):
            raise UnknownOffsetError(
                f"offset {entry_id!r} has been trimmed from {stream!r}"
            )

    def _server_now_ms(self) -> int:
        seconds, micros = self._client.time()
        return seconds * 1000 + micros // 1000


if TYPE_CHECKING:

    def _assert_is_event_bus(bus: RedisEventBus) -> EventBus:
        return bus
