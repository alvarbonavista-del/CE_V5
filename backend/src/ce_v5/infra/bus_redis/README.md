# infra/bus_redis - Redis Streams adapter (ADR-013)

Implements the `ce_v5.core.bus.EventBus` port on Redis Streams. This is the
only place that touches the native Redis API (REST-15); producers and
consumers depend on the port, not on this module.

## Streams and partitioning
Each topic maps to `N` partition streams named `<namespace>:<topic>:<p>`
(`partitions` in `RedisBusConfig`, default 1). A message is routed by
`crc32(stream_key) % partitions`, so ordering is preserved per `stream_key`.
Advanced partitioning is out of scope in v5.0.

## Delivery
`ensure_group` creates a consumer group per partition (from id 0). `poll`
reads new messages with `XREADGROUP` (`>`), `ack` calls `XACK`. `claim_stale`
uses `XPENDING`+`XCLAIM` to reclaim messages a crashed or slow consumer left
unacked, reporting the delivery count so the caller can route to the DLQ
after N attempts. Delivery is at-least-once: consumers must be idempotent
(they ack only after persisting their effect).

## Replay
`replay(start=None)` reads the retained history; `replay(start=offset)` reads
strictly after `offset`. If `offset` was trimmed from the stream the adapter
raises `UnknownOffsetError` instead of silently skipping data.

## DLQ reprocess
`dead_letter` appends the message to `<namespace>:<topic>:dlq` with the
mandatory fields `owner`, `reason_code`, `attempts`, `detail`,
`first_seen_at`, `last_seen_at`, `procedure`, `origin_topic` and
`origin_offset`, then acks the original. To reprocess: inspect the entry with
`XRANGE <namespace>:<topic>:dlq`, fix the operational cause, and re-publish
the `envelope` to the origin topic through the `EventBus` port. DLQ entries
are append-only and removed only after a successful reprocess.
