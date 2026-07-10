# core/bus - EventBus port (ADR-013)

Our own abstraction over any message broker. Producers and consumers
depend only on the `EventBus` Protocol defined here, never on the native
broker API (REST-15).

- `message.py` - transport DTOs (`BusMessage`, `Offset`, `Delivery`,
  `ReceivedMessage`, `DlqReason`). The canonical envelope travels
  serialized and opaque; the bus does not import the event contract.
- `ports.py` - the `EventBus` Protocol: publish, consumer groups, poll,
  ack, claim of stale messages, dead-letter and replay by offset.
- `errors.py` - `BusError` and subclasses, incl. `UnknownOffsetError`
  for replay from a trimmed offset (never advance in silence, ADR-013).

The Redis Streams adapter lives in `ce_v5.infra.bus_redis` and is wired
at the composition root (`entrypoints/`).
