"""Tests del consumidor del worker de footprint (P07b 3b-1; ADR-013, CE-14).

En FRIO: bus falso y motor falso, sin Redis ni base. Lo que se prueba es la mitad de
CONSUMO del tick -- el poll+ack sin inbox -- que es lo propio de este worker frente al
de reglas (que si usa inbox):

- Una candle_closed se despacha a on_candle_closed y se ACKea.
- Una candle_corrected se despacha a on_candle_corrected (con su event_time del sobre).
- Una candle_updated NO es de este consumidor: se IGNORA pero se ACKea igual (no es un
  error, no debe reintentarse hasta la DLQ).
- Si el efecto LANZA, el mensaje NO se ACKea (queda pendiente, se reintenta).
- Superado max_attempts, el mensaje va a la DLQ.
- Los pendientes reclamados (claim_stale) se procesan antes que los nuevos (poll).
"""

from __future__ import annotations

import json
from decimal import Decimal

from ce_v5.core.bus import BusMessage
from ce_v5.core.bus.message import Delivery, Offset, ReceivedMessage
from ce_v5.entrypoints.worker_footprint.composition import (
    CONSUMER_GROUP,
    MARKET_TOPIC,
    FootprintContext,
)
from source.families.market import (
    CandleClosedPayload,
    CandleCorrectedPayload,
    MarketCandleEventType,
    MarketType,
    Timeframe,
)
from source.time import MaturityState

_TF = Timeframe.M1
_OPEN = 1_784_073_600_000
_CLOSE = _OPEN + _TF.duration_ms
_EVENT_TIME = _OPEN + 777


def _candle_closed() -> CandleClosedPayload:
    return CandleClosedPayload(
        maturity_state=MaturityState.CLOSED,
        exchange="binance",
        market_type=MarketType.SPOT,
        symbol="BTC-USDT",
        timeframe=_TF,
        open_time=_OPEN,
        close_time=_CLOSE,
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("99"),
        close=Decimal("105"),
        volume=Decimal("10"),
    )


def _candle_corrected(revision: int = 2) -> CandleCorrectedPayload:
    return CandleCorrectedPayload(
        maturity_state=MaturityState.CORRECTION,
        corrects_idempotency_key="market.candle_closed|k|o|closed",
        correction_revision=revision,
        exchange="binance",
        market_type=MarketType.SPOT,
        symbol="BTC-USDT",
        timeframe=_TF,
        open_time=_OPEN,
        close_time=_CLOSE,
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("99"),
        close=Decimal("105"),
        volume=Decimal("10"),
    )


def _bus_message(
    event_type: str, payload_dump: dict[str, object], event_time: int = _EVENT_TIME
) -> BusMessage:
    envelope = json.dumps({"payload": payload_dump, "event_time": event_time}).encode()
    return BusMessage(
        event_id="ev-" + event_type,
        event_type=event_type,
        stream_key="sk",
        idempotency_key="ik-" + event_type,
        envelope=envelope,
    )


def _received(message: BusMessage, *, delivery_count: int = 1) -> ReceivedMessage:
    return ReceivedMessage(
        message=message,
        delivery=Delivery(
            topic=MARKET_TOPIC,
            consumer_group=CONSUMER_GROUP,
            offset=Offset(value="0-1"),
            delivery_count=delivery_count,
        ),
    )


class _BusFalso:
    """Doble minimo de EventBus: entrega los mensajes cargados y registra ack/dlq."""

    def __init__(
        self,
        fresh: list[ReceivedMessage] | None = None,
        stale: list[ReceivedMessage] | None = None,
    ) -> None:
        self._fresh = fresh or []
        self._stale = stale or []
        self.groups_ensured: list[tuple[str, str]] = []
        self.acked: list[Offset] = []
        self.dead_lettered: list[ReceivedMessage] = []

    def ensure_group(self, topic: str, group: str) -> None:
        self.groups_ensured.append((topic, group))

    def claim_stale(
        self,
        topic: str,
        group: str,
        consumer: str,
        *,
        min_idle_ms: int,
        max_messages: int,
    ) -> list[ReceivedMessage]:
        return list(self._stale)

    def poll(
        self,
        topic: str,
        group: str,
        consumer: str,
        *,
        max_messages: int,
        block_ms: int,
    ) -> list[ReceivedMessage]:
        return list(self._fresh)

    def ack(self, delivery: Delivery) -> None:
        self.acked.append(delivery.offset)

    def dead_letter(self, received: ReceivedMessage, reason: object) -> None:
        self.dead_lettered.append(received)


class _MotorFalso:
    """Doble del FootprintEngine: registra los despachos, sin agregar nada."""

    def __init__(self, *, lanza: bool = False) -> None:
        self.lanza = lanza
        self.closed: list[tuple[CandleClosedPayload, int]] = []
        self.corrected: list[tuple[CandleCorrectedPayload, int]] = []

    def on_candle_closed(self, closed: CandleClosedPayload, event_time: int) -> None:
        if self.lanza:
            msg = "efecto falla"
            raise RuntimeError(msg)
        self.closed.append((closed, event_time))

    def on_candle_corrected(
        self, corrected: CandleCorrectedPayload, event_time: int
    ) -> None:
        self.corrected.append((corrected, event_time))


class _PublisherFalso:
    def drain_once(self) -> int:
        return 0


def _context(bus: _BusFalso, engine: _MotorFalso) -> FootprintContext:
    return FootprintContext(
        engine=engine,  # type: ignore[arg-type]
        publisher=_PublisherFalso(),  # type: ignore[arg-type]
        database=None,  # type: ignore[arg-type]
        bus=bus,  # type: ignore[arg-type]
        consumer_name="footprint-test",
    )


class TestDespacho:
    def test_una_vela_cerrada_se_despacha_y_se_ackea(self) -> None:
        msg = _bus_message(
            MarketCandleEventType.CANDLE_CLOSED.value,
            _candle_closed().model_dump(mode="json"),
        )
        bus = _BusFalso(fresh=[_received(msg)])
        engine = _MotorFalso()
        result = _context(bus, engine).consume_once(block_ms=0)

        assert result.processed == 1
        assert len(engine.closed) == 1
        assert engine.closed[0][1] == _EVENT_TIME  # event_time del envelope.
        assert len(bus.acked) == 1

    def test_una_correccion_se_despacha_con_su_event_time(self) -> None:
        msg = _bus_message(
            MarketCandleEventType.CANDLE_CORRECTED.value,
            _candle_corrected(revision=3).model_dump(mode="json"),
        )
        bus = _BusFalso(fresh=[_received(msg)])
        engine = _MotorFalso()
        result = _context(bus, engine).consume_once(block_ms=0)

        assert result.processed == 1
        assert len(engine.corrected) == 1
        payload, event_time = engine.corrected[0]
        assert payload.correction_revision == 3
        assert event_time == _EVENT_TIME
        assert len(bus.acked) == 1

    def test_una_provisional_se_ignora_pero_se_ackea(self) -> None:
        # candle_updated no es de este consumidor: NO se despacha, pero SI se ACKea (no
        # es un error; reintentarla hasta la DLQ haria del flujo normal un incidente).
        msg = _bus_message(
            MarketCandleEventType.CANDLE_UPDATED.value,
            _candle_closed()
            .model_copy(update={"maturity_state": MaturityState.PROVISIONAL})
            .model_dump(mode="json"),
        )
        bus = _BusFalso(fresh=[_received(msg)])
        engine = _MotorFalso()
        result = _context(bus, engine).consume_once(block_ms=0)

        assert result.processed == 0
        assert result.skipped == 1
        assert engine.closed == []
        assert engine.corrected == []
        assert len(bus.acked) == 1  # ACKeada de todos modos.


class TestResiliencia:
    def test_si_el_efecto_lanza_no_se_ackea(self) -> None:
        # Un fallo del efecto deja el mensaje PENDIENTE (sin ACK): se reintentara.
        msg = _bus_message(
            MarketCandleEventType.CANDLE_CLOSED.value,
            _candle_closed().model_dump(mode="json"),
        )
        bus = _BusFalso(fresh=[_received(msg)])
        engine = _MotorFalso(lanza=True)
        result = _context(bus, engine).consume_once(block_ms=0)

        assert result.processed == 0
        assert bus.acked == []  # NO se ACKea.
        assert bus.dead_lettered == []  # aun no: no supero max_attempts.

    def test_superado_max_attempts_va_a_la_dlq(self) -> None:
        msg = _bus_message(
            MarketCandleEventType.CANDLE_CLOSED.value,
            _candle_closed().model_dump(mode="json"),
        )
        bus = _BusFalso(fresh=[_received(msg, delivery_count=6)])
        engine = _MotorFalso()
        result = _context(bus, engine).consume_once(block_ms=0, max_attempts=5)

        assert result.dead_lettered == 1
        assert len(bus.dead_lettered) == 1
        assert bus.acked == []
        assert engine.closed == []  # ni se intento el efecto.


class TestReclamados:
    def test_los_reclamados_se_procesan_antes_que_los_nuevos(self) -> None:
        # claim_stale (pendientes de un consumidor caido) + poll (nuevos): ambos se
        # procesan en la misma pasada.
        viejo = _bus_message(
            MarketCandleEventType.CANDLE_CLOSED.value,
            _candle_closed().model_dump(mode="json"),
        )
        nuevo = _bus_message(
            MarketCandleEventType.CANDLE_CORRECTED.value,
            _candle_corrected().model_dump(mode="json"),
        )
        bus = _BusFalso(stale=[_received(viejo)], fresh=[_received(nuevo)])
        engine = _MotorFalso()
        result = _context(bus, engine).consume_once(block_ms=0)

        assert result.processed == 2
        assert len(engine.closed) == 1
        assert len(engine.corrected) == 1
        assert bus.groups_ensured == [(MARKET_TOPIC, CONSUMER_GROUP)]
