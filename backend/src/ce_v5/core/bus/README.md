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

## Dos modos de consumo, y no son intercambiables

**poll + ack (consumer group)**: consumo COORDINADO y COMPARTIDO entre varios workers. El
bus lleva el estado de quien ha confirmado que. Es lo que quieren los workers
(at-least-once, reparto de carga, DLQ).

**replay + cursor privado**: consumo INDIVIDUAL y reanudable. El estado (por donde voy) lo
lleva el CLIENTE, en su checkpoint. Es lo que quiere un canal realtime por usuario.

Un consumer group POR USUARIO seria un error de diseno: crearia miles de grupos con estado
de ACK que el bus tendria que mantener para siempre, incluso para clientes que no volveran.
El cursor privado no le cuesta nada al bus.

`latest_offset` existe para que una suscripcion SIN checkpoint arranque en el final REAL del
topic. Sin el, habria que recorrer el historico para saber donde termina. El offset que
devuelve significa "ya visto": `replay` desde el es EXCLUSIVO, asi que no reentrega nada
antiguo.

En el adapter de Redis, `latest_offset` FALLA RUIDOSO si hay mas de una particion: el cursor
de replay apunta a una sola, y devolver el final de una de N saltaria las demas en silencio.
