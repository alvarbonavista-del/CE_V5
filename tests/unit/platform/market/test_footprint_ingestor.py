"""Tests del motor de agregacion del footprint (P07b 3b-1; ADR-007/013, CE-14).

Con fakes de los dos puertos (lectura de ventana + escritura atomica) y un reloj
simulado. Cero red, cero base, cero hilos. Aqui se demuestra lo propio del MOTOR (la
funcion pura ya se prueba en test_footprint_aggregate):

- La VENTANA que pide al store es [open_time, open_time + tf_ms): el mismo bucketing
  semiabierto de la barra. Ni un ms de mas ni de menos.
- Una vela cerrada -> footprint_closed persistido+encolado; el event_time del footprint
  es EL DE LA VELA, no el reloj del motor.
- Una correccion -> footprint_corrected con la MISMA revision de la vela (lockstep).
- IDEMPOTENCIA: si el writer dice "ya estaba" (False), no se cuenta como nuevo.
- CELDAS-POR-BARRA: la metrica que Central pidio (sin cap, se observa cuantas produce
  cada barra y el maximo visto).
- is_complete se propaga: un hueco solapado incrementa incomplete_bars.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from ce_v5.core.clock.simulated import SimulatedClock
from ce_v5.platform.market.footprint_aggregate import TradeGap
from ce_v5.platform.market.footprint_ingestor import FootprintEngine
from source.families.footprint import (
    FootprintPayload,
    MarketFootprintEventType,
    MarketTrade,
)
from source.families.market import (
    AggressorSide,
    CandleClosedPayload,
    CandleCorrectedPayload,
    MarketType,
    Timeframe,
)
from source.time import MaturityState

_TF = Timeframe.M1
_OPEN = 1_784_073_600_000
_CLOSE = _OPEN + _TF.duration_ms
_CANDLE_EVENT_TIME = _OPEN + 12_345  # el instante de origen de la barra (ADR-007).
_CLOCK_MS = 9_999_999_999_999  # el reloj del motor, DISTINTO del de la vela.


def _trade(**overrides: object) -> MarketTrade:
    base: dict[str, object] = {
        "exchange": "binance",
        "market_type": MarketType.SPOT,
        "symbol": "BTC-USDT",
        "trade_id": "1",
        "price": Decimal("100"),
        "qty": Decimal("1"),
        "aggressor_side": AggressorSide.BUY,
        "event_time": _OPEN + 10,
    }
    base.update(overrides)
    return MarketTrade(**base)


def _candle_closed(**overrides: object) -> CandleClosedPayload:
    base: dict[str, object] = {
        "maturity_state": MaturityState.CLOSED,
        "exchange": "binance",
        "market_type": MarketType.SPOT,
        "symbol": "BTC-USDT",
        "timeframe": _TF,
        "open_time": _OPEN,
        "close_time": _CLOSE,
        "open": Decimal("100"),
        "high": Decimal("110"),
        "low": Decimal("99"),
        "close": Decimal("105"),
        "volume": Decimal("10"),
    }
    base.update(overrides)
    return CandleClosedPayload(**base)


def _candle_corrected(revision: int = 1, **overrides: object) -> CandleCorrectedPayload:
    base: dict[str, object] = {
        "maturity_state": MaturityState.CORRECTION,
        "corrects_idempotency_key": "market.candle_closed|k|open|closed",
        "correction_revision": revision,
        "exchange": "binance",
        "market_type": MarketType.SPOT,
        "symbol": "BTC-USDT",
        "timeframe": _TF,
        "open_time": _OPEN,
        "close_time": _CLOSE,
        "open": Decimal("100"),
        "high": Decimal("110"),
        "low": Decimal("99"),
        "close": Decimal("105"),
        "volume": Decimal("10"),
    }
    base.update(overrides)
    return CandleCorrectedPayload(**base)


class _ReaderFalso:
    """Doble del puerto de lectura. Guarda la ULTIMA ventana pedida para asertar sobre
    ella, y devuelve los trades/huecos que se le carguen.
    """

    def __init__(
        self,
        trades: Sequence[MarketTrade] = (),
        gaps: Sequence[TradeGap] = (),
    ) -> None:
        self._trades = tuple(trades)
        self._gaps = tuple(gaps)
        self.ventanas: list[tuple[int, int]] = []

    def trades_in_window(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        window_start: int,
        window_end: int,
    ) -> Sequence[MarketTrade]:
        self.ventanas.append((window_start, window_end))
        return self._trades

    def overlapping_gaps(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        window_start: int,
        window_end: int,
    ) -> Sequence[TradeGap]:
        return self._gaps


class _WriterFalso:
    """Doble del puerto de escritura. Guarda lo emitido y deduplica por idempotency_key,
    como el ON CONFLICT DO NOTHING RETURNING del writer real.
    """

    def __init__(self) -> None:
        self.emitidos: list[tuple[str, FootprintPayload, bytes]] = []
        self._claves: set[str] = set()

    def persist_and_enqueue(
        self,
        envelope_json: bytes,
        payload: FootprintPayload,
        event_type: str,
        stream_key: str,
        idempotency_key: str,
    ) -> bool:
        if idempotency_key in self._claves:
            return False
        self._claves.add(idempotency_key)
        self.emitidos.append((event_type, payload, envelope_json))
        return True


def _motor(reader: _ReaderFalso, writer: _WriterFalso) -> FootprintEngine:
    return FootprintEngine(
        reader=reader,
        writer=writer,
        clock=SimulatedClock(start_ms=_CLOCK_MS),
        component_source="test_footprint",
    )


class TestVentana:
    def test_pide_al_store_la_ventana_semiabierta_de_la_barra(self) -> None:
        reader = _ReaderFalso(trades=[_trade()])
        writer = _WriterFalso()
        _motor(reader, writer).on_candle_closed(_candle_closed(), _CANDLE_EVENT_TIME)

        # [open_time, open_time + tf_ms): ni un ms de mas ni de menos.
        assert reader.ventanas == [(_OPEN, _OPEN + _TF.duration_ms)]


class TestCierre:
    def test_una_vela_cerrada_emite_footprint_closed(self) -> None:
        reader = _ReaderFalso(
            trades=[
                _trade(trade_id="1", price=Decimal("100"), qty=Decimal("2")),
                _trade(
                    trade_id="2",
                    price=Decimal("101"),
                    qty=Decimal("3"),
                    aggressor_side=AggressorSide.SELL,
                ),
            ]
        )
        writer = _WriterFalso()
        motor = _motor(reader, writer)
        motor.on_candle_closed(_candle_closed(), _CANDLE_EVENT_TIME)

        assert len(writer.emitidos) == 1
        event_type, payload, _ = writer.emitidos[0]
        assert event_type == MarketFootprintEventType.FOOTPRINT_CLOSED.value
        assert payload.maturity_state is MaturityState.CLOSED
        assert payload.bar_delta == Decimal("-1")
        assert motor.metrics.footprints_closed == 1

    def test_el_event_time_del_footprint_es_el_de_la_vela_no_el_reloj(self) -> None:
        # ADR-007: el instante de origen de la barra sale de la VELA que dispara, no del
        # reloj del motor. El envelope lo lleva en event_time.
        reader = _ReaderFalso(trades=[_trade()])
        writer = _WriterFalso()
        _motor(reader, writer).on_candle_closed(_candle_closed(), _CANDLE_EVENT_TIME)

        import json

        envelope = json.loads(writer.emitidos[0][2])
        assert envelope["event_time"] == _CANDLE_EVENT_TIME
        assert envelope["event_time"] != _CLOCK_MS


class TestCorreccion:
    def test_una_correccion_emite_footprint_corrected_con_su_revision(self) -> None:
        reader = _ReaderFalso(trades=[_trade()])
        writer = _WriterFalso()
        motor = _motor(reader, writer)
        motor.on_candle_corrected(_candle_corrected(revision=3), _CANDLE_EVENT_TIME)

        assert len(writer.emitidos) == 1
        event_type, payload, _ = writer.emitidos[0]
        assert event_type == MarketFootprintEventType.FOOTPRINT_CORRECTED.value
        # LOCKSTEP: la revision del footprint es la de la vela corregida.
        assert payload.correction_revision == 3
        assert payload.maturity_state is MaturityState.CORRECTION
        assert motor.metrics.footprints_corrected == 1


class TestIdempotencia:
    def test_reprocesar_la_misma_vela_no_duplica(self) -> None:
        # El writer dice "ya estaba" (dedup por idempotency_key): no cuenta como nuevo.
        reader = _ReaderFalso(trades=[_trade()])
        writer = _WriterFalso()
        motor = _motor(reader, writer)
        motor.on_candle_closed(_candle_closed(), _CANDLE_EVENT_TIME)
        motor.on_candle_closed(_candle_closed(), _CANDLE_EVENT_TIME)

        assert len(writer.emitidos) == 1
        assert motor.metrics.footprints_closed == 1
        assert motor.metrics.duplicates_skipped == 1


class TestMetricaCeldasPorBarra:
    def test_cuenta_celdas_por_barra_y_el_maximo(self) -> None:
        # Primera barra: 3 niveles de precio. Segunda barra (otra ventana): 1 nivel.
        # celdas_ultima refleja la ULTIMA; celdas_max se queda con el pico.
        reader3 = _ReaderFalso(
            trades=[
                _trade(trade_id="1", price=Decimal("100")),
                _trade(trade_id="2", price=Decimal("101")),
                _trade(trade_id="3", price=Decimal("102")),
            ]
        )
        writer = _WriterFalso()
        motor = _motor(reader3, writer)
        motor.on_candle_closed(_candle_closed(), _CANDLE_EVENT_TIME)
        assert motor.metrics.cells_last_bar == 3
        assert motor.metrics.max_cells_in_bar == 3
        assert motor.metrics.total_cells == 3

        # Otra barra (otro open_time) con una sola celda.
        otra_open = _OPEN + _TF.duration_ms
        motor._reader = _ReaderFalso(  # noqa: SLF001 - sustituir el doble en el test.
            trades=[_trade(trade_id="9", price=Decimal("100"))]
        )
        motor.on_candle_closed(
            _candle_closed(open_time=otra_open, close_time=otra_open + _TF.duration_ms),
            _CANDLE_EVENT_TIME,
        )
        assert motor.metrics.cells_last_bar == 1
        assert motor.metrics.max_cells_in_bar == 3  # el pico se mantiene.
        assert motor.metrics.total_cells == 4


class TestIsComplete:
    def test_un_hueco_solapado_marca_la_barra_incompleta(self) -> None:
        gap: TradeGap = (_OPEN + 10, _OPEN + 20)
        reader = _ReaderFalso(trades=[_trade()], gaps=[gap])
        writer = _WriterFalso()
        motor = _motor(reader, writer)
        motor.on_candle_closed(_candle_closed(), _CANDLE_EVENT_TIME)

        assert writer.emitidos[0][1].is_complete is False
        assert motor.metrics.incomplete_bars == 1

    def test_sin_huecos_la_barra_es_completa(self) -> None:
        reader = _ReaderFalso(trades=[_trade()], gaps=[])
        writer = _WriterFalso()
        motor = _motor(reader, writer)
        motor.on_candle_closed(_candle_closed(), _CANDLE_EVENT_TIME)

        assert writer.emitidos[0][1].is_complete is True
        assert motor.metrics.incomplete_bars == 0
