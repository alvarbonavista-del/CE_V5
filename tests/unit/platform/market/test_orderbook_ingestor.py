"""Tests del motor de ingesta del LIBRO L2 con estado (P07c Tanda III). SIN RED NI BASE.

Con fakes del puerto de datos y del puerto de escritura (adversariales, deterministas):
cero red, cero base, cero reloj real. Aqui se demuestra lo propio del libro frente a los
trades -- CON ESTADO y ORDER-DEPENDIENTE --: los deltas construyen el libro; una
reconexion lo RE-SIEMBRA y apunta su discontinuidad; un hueco detectado por el Motor
PUBLICA un resync (su propio hecho); el backpressure no pierde nada; y un stream podrido
no tumba a los demas.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from decimal import Decimal

from ce_v5.platform.market.orderbook_ingestor import (
    OrderbookIngestionConfig,
    OrderbookIngestionEngine,
)
from source.families.market import (
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    RawOrderbookDelta,
    RawOrderbookSeed,
)
from source.families.orderbook import (
    MarketOrderbookEventType,
    OrderbookResyncedPayload,
    OrderbookSnapshotPayload,
)

_NOW = 1_784_073_600_000

_BTC = MarketStreamKey(
    exchange="binance",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.ORDERBOOK,
)
_ETH = MarketStreamKey(
    exchange="binance",
    market_type=MarketType.SPOT,
    symbol="ETH-USDT",
    data_kind=MarketDataKind.ORDERBOOK,
)


def _seed(
    symbol: str = "BTC-USDT", base_sequence: int = 100, **overrides: object
) -> RawOrderbookSeed:
    base: dict[str, object] = {
        "exchange": "binance",
        "market_type": "spot",
        "symbol": symbol,
        "bids": [("100.0", "2"), ("99.0", "1")],
        "asks": [("101.0", "1"), ("102.0", "3")],
        "base_sequence": base_sequence,
    }
    base.update(overrides)
    return RawOrderbookSeed(**base)  # type: ignore[arg-type]


def _delta(symbol: str = "BTC-USDT", **overrides: object) -> RawOrderbookDelta:
    base: dict[str, object] = {
        "exchange": "binance",
        "market_type": "spot",
        "symbol": symbol,
        "bids": [],
        "asks": [],
    }
    base.update(overrides)
    return RawOrderbookDelta(**base)  # type: ignore[arg-type]


class _Clock:
    def __init__(self, t: int = _NOW) -> None:
        self._t = t

    def now_ms(self) -> int:
        return self._t


class _Source:
    """Feed de mentira del libro: guion escrito por el test, entregado con tope."""

    def __init__(self) -> None:
        self._active: set[str] = set()
        self._seeds: dict[str, RawOrderbookSeed] = {}
        self._pending: deque[RawOrderbookDelta] = deque()
        self._reconnected: set[str] = set()
        self.seed_calls: list[str] = []
        self.max_batch = 100

    def load_seed(self, key: MarketStreamKey, seed: RawOrderbookSeed) -> None:
        self._seeds[key.as_stream_key()] = seed

    def emit(self, *deltas: RawOrderbookDelta) -> None:
        self._pending.extend(deltas)

    def simulate_reconnect(self, keys: Sequence[str]) -> None:
        self._reconnected.update(keys)

    def pending_count(self) -> int:
        return len(self._pending)

    def open(self, key: MarketStreamKey) -> None:
        self._active.add(key.as_stream_key())

    def close(self, key: MarketStreamKey) -> None:
        self._active.discard(key.as_stream_key())

    def active(self) -> set[str]:
        return set(self._active)

    def seed(self, key: MarketStreamKey) -> RawOrderbookSeed:
        self.seed_calls.append(key.as_stream_key())
        return self._seeds[key.as_stream_key()]

    def poll_deltas(self, timeout_ms: int) -> Sequence[RawOrderbookDelta]:
        lote: list[RawOrderbookDelta] = []
        while self._pending and len(lote) < self.max_batch:
            lote.append(self._pending.popleft())
        return lote

    def drain_reconnected(self) -> set[str]:
        copia = set(self._reconnected)
        self._reconnected.clear()
        return copia


class _Writer:
    """Writer de mentira: recuerda lo publicado, las muestras y TODAS las
    discontinuidades (las de record_discontinuity y las que persist_and_enqueue graba
    del resync), como la tabla real, para que overlapping_discontinuities las lea
    igual que en la base.
    """

    def __init__(self) -> None:
        self.published: list[tuple[str, str, object]] = []
        self.samples: list[OrderbookSnapshotPayload] = []
        self.discontinuities: list[tuple[str, str, str, int, int | None, int, str]] = []
        self._pub_keys: set[str] = set()
        self._sample_keys: set[str] = set()
        self._disc_keys: set[tuple[str, str, str, int, int | None]] = set()

    def _record(
        self, ex: str, mt: str, sy: str, fr: int, to: int | None, et: int, reason: str
    ) -> bool:
        clave = (ex, mt, sy, fr, to)
        if clave in self._disc_keys:
            return False
        self._disc_keys.add(clave)
        self.discontinuities.append((ex, mt, sy, fr, to, et, reason))
        return True

    def persist_and_enqueue(
        self,
        envelope_json: bytes,
        payload: object,
        event_type: str,
        stream_key: str,
        idempotency_key: str,
        event_time: int,
    ) -> bool:
        if idempotency_key in self._pub_keys:
            return False
        self._pub_keys.add(idempotency_key)
        self.published.append((event_type, idempotency_key, payload))
        if isinstance(payload, OrderbookResyncedPayload):
            self._record(
                payload.exchange,
                payload.market_type.value,
                payload.symbol,
                payload.from_sequence,
                payload.to_sequence,
                payload.event_time,
                payload.reason,
            )
        return True

    def persist_sample(
        self, payload: OrderbookSnapshotPayload, event_time: int
    ) -> bool:
        clave = payload.idempotency_key(payload.kind)
        if clave in self._sample_keys:
            return False
        self._sample_keys.add(clave)
        self.samples.append(payload)
        return True

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
        return self._record(
            exchange,
            market_type,
            symbol,
            from_sequence,
            to_sequence,
            event_time,
            reason,
        )

    def overlapping_discontinuities(
        self, exchange: str, market_type: str, symbol: str, ws: int, we: int
    ) -> tuple[tuple[int, int | None, int], ...]:
        return tuple(
            (fr, to, et)
            for (ex, mt, sy, fr, to, et, _r) in self.discontinuities
            if ex == exchange and mt == market_type and sy == symbol and ws <= et < we
        )


def _engine(
    source: _Source, writer: _Writer, max_batch: int = 500
) -> OrderbookIngestionEngine:
    return OrderbookIngestionEngine(
        source,
        writer,
        _Clock(),
        component_source="worker_orderbook",
        config=OrderbookIngestionConfig(max_batch=max_batch),
    )


class TestSiembraYAplicacion:
    def test_un_stream_suscrito_se_siembra_y_los_deltas_construyen_el_libro(
        self,
    ) -> None:
        source = _Source()
        source.open(_BTC)
        source.load_seed(_BTC, _seed(base_sequence=100))
        writer = _Writer()
        engine = _engine(source, writer)

        # U=101 encadena con lastUpdateId=100; sube el bid 100.0 a 5 y borra el 99.0.
        source.emit(
            _delta(
                first_update_id=101,
                final_update_id=101,
                bids=[("100.0", "5"), ("99.0", "0")],
            )
        )
        engine.drain_once()

        book = engine.book_for(_BTC.as_stream_key())
        assert book is not None
        assert book.seeded and book.is_complete
        assert book.sequence == 101
        assert book.bids() == {Decimal("100.0"): Decimal("5")}
        assert engine.metrics.deltas_applied == 1
        # La foto se pidio UNA vez (la siembra inicial).
        assert source.seed_calls == [_BTC.as_stream_key()]

    def test_un_delta_de_un_stream_no_suscrito_no_entra(self) -> None:
        source = _Source()
        source.open(_BTC)
        source.load_seed(_BTC, _seed())
        writer = _Writer()
        engine = _engine(source, writer)
        source.emit(_delta(symbol="SOL-USDT", first_update_id=101, final_update_id=101))
        engine.drain_once()
        assert engine.metrics.unsubscribed_dropped == 1


class TestResyncPublicado:
    def test_un_hueco_detectado_publica_orderbook_resynced(self) -> None:
        source = _Source()
        source.open(_BTC)
        source.load_seed(_BTC, _seed(base_sequence=100))
        writer = _Writer()
        engine = _engine(source, writer)

        # U=106 tras lastUpdateId=100: se salto 101..105 -> hueco.
        source.emit(_delta(first_update_id=106, final_update_id=110))
        engine.drain_once()

        book = engine.book_for(_BTC.as_stream_key())
        assert book is not None
        assert book.resync_required and not book.is_complete
        assert engine.metrics.resyncs == 1
        # Se publico UN market.orderbook_resynced (su propio hecho).
        assert len(writer.published) == 1
        event_type, _clave, payload = writer.published[0]
        assert event_type == MarketOrderbookEventType.ORDERBOOK_RESYNCED.value
        assert isinstance(payload, OrderbookResyncedPayload)
        assert payload.from_sequence == 100  # la ultima secuencia buena.
        assert payload.to_sequence is None  # extremo desconocido hasta re-sembrar.
        # persist_and_enqueue grabo la discontinuidad, legible por el frontier.
        assert writer.discontinuities[0][6] == "gap"

    def test_el_resync_se_publica_una_sola_vez_por_episodio(self) -> None:
        # Estando ya en resync, los deltas siguientes NO re-publican (el Motor los
        # ignora).
        source = _Source()
        source.open(_BTC)
        source.load_seed(_BTC, _seed(base_sequence=100))
        writer = _Writer()
        engine = _engine(source, writer)
        source.emit(
            _delta(first_update_id=106, final_update_id=110),
            _delta(first_update_id=111, final_update_id=112),
        )
        engine.drain_once()
        assert engine.metrics.resyncs == 1
        assert len(writer.published) == 1


class TestReconexion:
    def test_una_reconexion_re_siembra_y_apunta_la_discontinuidad(self) -> None:
        source = _Source()
        source.open(_BTC)
        source.load_seed(_BTC, _seed(base_sequence=100))
        writer = _Writer()
        engine = _engine(source, writer)

        source.emit(_delta(first_update_id=101, final_update_id=103))
        engine.drain_once()
        book = engine.book_for(_BTC.as_stream_key())
        assert book is not None and book.sequence == 103

        # El socket reconecto: se re-siembra desde una foto NUEVA (base 200).
        source.load_seed(_BTC, _seed(base_sequence=200, bids=[("50.0", "1")]))
        source.simulate_reconnect([_BTC.as_stream_key()])
        engine.drain_once()

        assert book.sequence == 200
        assert book.is_complete
        assert engine.metrics.reseeds == 1
        # La discontinuidad de la reconexion se APUNTO (from ultima buena, to nueva
        # base), SIN publicar un evento: reason='reconnect'.
        assert engine.metrics.discontinuities_recorded == 1
        assert writer.discontinuities[-1][:5] == (
            "binance",
            "spot",
            "BTC-USDT",
            103,
            200,
        )
        assert writer.discontinuities[-1][6] == "reconnect"
        assert writer.published == []  # una reconexion no publica orderbook_resynced.


class TestBackpressure:
    def test_una_avalancha_se_procesa_por_tandas_sin_perder_nada(self) -> None:
        source = _Source()
        source.open(_BTC)
        source.load_seed(_BTC, _seed(base_sequence=0))
        writer = _Writer()
        engine = _engine(source, writer, max_batch=500)
        source.max_batch = 100  # el feed entrega de cien en cien.

        # Deltas contiguos u=1..2000 (encadenan desde base 0).
        source.emit(
            *[
                _delta(first_update_id=i, final_update_id=i, bids=[("100.0", str(i))])
                for i in range(1, 2001)
            ]
        )
        engine.drain_once()
        assert engine.metrics.deltas_applied == 500  # digiere 500 por ciclo.
        assert source.pending_count() == 1_500  # el resto ESPERA, intacto.

        engine.drain_once()
        engine.drain_once()
        engine.drain_once()
        assert engine.metrics.deltas_applied == 2_000
        assert source.pending_count() == 0


class TestAislamientoPorStream:
    def test_un_delta_podrido_no_impide_los_demas(self) -> None:
        source = _Source()
        source.open(_BTC)
        source.open(_ETH)
        source.load_seed(_BTC, _seed(symbol="BTC-USDT", base_sequence=100))
        source.load_seed(_ETH, _seed(symbol="ETH-USDT", base_sequence=100))
        writer = _Writer()
        engine = _engine(source, writer)

        # BTC: un delta que encadena pero con un precio NO numerico -> rechazo tipado.
        # ETH: un delta bueno DETRAS -> debe entrar igualmente.
        source.emit(
            _delta(
                symbol="BTC-USDT",
                first_update_id=101,
                final_update_id=101,
                bids=[("abc", "1")],
            ),
            _delta(
                symbol="ETH-USDT",
                first_update_id=101,
                final_update_id=101,
                bids=[("100.0", "9")],
            ),
        )
        engine.drain_once()

        eth = engine.book_for(_ETH.as_stream_key())
        assert eth is not None and eth.bids()[Decimal("100.0")] == Decimal("9")
        assert engine.metrics.rejected == {"malformed_number": 1}
        assert _BTC.as_stream_key() in engine.metrics.degraded_streams

    def test_una_siembra_que_lanza_no_tumba_el_ciclo(self) -> None:
        source = _Source()
        source.open(_BTC)
        # No se carga la foto de BTC: source.seed lanzara KeyError -> fault isolation.
        writer = _Writer()
        engine = _engine(source, writer)

        metrics = engine.drain_once()  # NO revienta.

        assert metrics.seed_errors == 1
        assert engine.book_for(_BTC.as_stream_key()) is None
        assert _BTC.as_stream_key() in metrics.degraded_streams

    def test_una_foto_corrupta_se_cuenta_y_no_arranca_el_libro(self) -> None:
        source = _Source()
        source.open(_BTC)
        source.load_seed(_BTC, _seed(bids=[("abc", "1")]))  # precio no numerico
        writer = _Writer()
        engine = _engine(source, writer)

        engine.drain_once()

        assert engine.metrics.rejected.get("malformed_number") == 1
        assert engine.book_for(_BTC.as_stream_key()) is None
