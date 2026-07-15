"""Tests del FakeMarketDataSource: el simulador adversarial (ADR-006, ADR-014).

El fake es lo que permite que el CI sea HERMETICO (cero red) y lo que permite provocar
a voluntad lo que un exchange real hace y nadie puede pedirle: soltar una avalancha,
caerse a mitad, o mandar una vela de otro simbolo. Determinista: sin azar, sin hilos,
sin reloj.
"""

from __future__ import annotations

import pytest

from ce_v5.infra.connectors.fake_market import FakeMarketDataSource
from ce_v5.platform.market.normalize import (
    RawCandleRejected,
    RawCandleRejectionReason,
    candle_payload_from_raw,
)
from source.families.market import (
    Instrument,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    RawCandle,
    Timeframe,
)

_OPEN = 1_784_073_600_000
_CLOSE = _OPEN + 59_999

_BTC = MarketStreamKey(
    exchange="binance",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.CANDLES,
    timeframe=Timeframe.M1,
)
_ETH = MarketStreamKey(
    exchange="binance",
    market_type=MarketType.SPOT,
    symbol="ETH-USDT",
    data_kind=MarketDataKind.CANDLES,
    timeframe=Timeframe.M1,
)


def _vela(**overrides: object) -> RawCandle:
    base: dict[str, object] = {
        "exchange": "binance",
        "market_type": "spot",
        "symbol": "BTC-USDT",
        "timeframe": "1m",
        "open_time_ms": _OPEN,
        "close_time_ms": _CLOSE,
        "open": "100",
        "high": "110",
        "low": "95",
        "close": "105",
        "volume": "1",
        "is_closed": True,
        "event_time_ms": _CLOSE,
    }
    base.update(overrides)
    return RawCandle(**base)  # type: ignore[arg-type]


@pytest.fixture
def fake() -> FakeMarketDataSource:
    return FakeMarketDataSource(
        instruments=[
            Instrument("binance", "spot", "BTC-USDT", "BTCUSDT", active=True),
            Instrument("binance", "spot", "DOGE-USDT", "DOGEUSDT", active=False),
        ],
        timeframes=[Timeframe.M1, Timeframe.H1],
    )


class TestControlDeStreams:
    def test_open_close_active(self, fake: FakeMarketDataSource) -> None:
        assert fake.active() == set()

        fake.open(_BTC)
        assert fake.active() == {_BTC.as_stream_key()}
        assert fake.opened == [_BTC.as_stream_key()]

        fake.close(_BTC)
        assert fake.active() == set()
        assert fake.closed == [_BTC.as_stream_key()]

    def test_la_desconexion_se_refleja_en_active(
        self, fake: FakeMarketDataSource
    ) -> None:
        # Un feed que se cae en silencio y nadie reabre es un STREAM ZOMBI: vivo en el
        # codigo, muerto en la realidad. El ingestor tiene que poder DARSE CUENTA, y
        # active() es como se entera.
        fake.open(_BTC)
        fake.open(_ETH)
        assert len(fake.active()) == 2

        fake.disconnect()

        assert fake.active() == set()


class TestPollConTope:
    def test_la_avalancha_se_entrega_en_tandas_y_no_se_pierde_nada(
        self, fake: FakeMarketDataSource
    ) -> None:
        # El exchange suelta mil velas de golpe; el ingestor solo se lleva las que
        # puede digerir. Lo que no cabe ESPERA: no se pierde y no se acumula en una
        # cola infinita en memoria (backpressure).
        fake.max_batch = 2
        velas = [_vela(open_time_ms=_OPEN + i * 60_000) for i in range(5)]
        fake.emit(*velas)

        primera = fake.poll(timeout_ms=0)
        segunda = fake.poll(timeout_ms=0)
        tercera = fake.poll(timeout_ms=0)

        assert len(primera) == 2
        assert len(segunda) == 2
        assert len(tercera) == 1
        assert fake.pending_count() == 0
        # NADA se perdio y el ORDEN se respeta.
        assert [*primera, *segunda, *tercera] == velas

    def test_poll_sin_nada_pendiente_devuelve_vacio(
        self, fake: FakeMarketDataSource
    ) -> None:
        assert fake.poll(timeout_ms=0) == []


class TestCatalogoYTimeframes:
    def test_list_instruments_filtra_por_market_type(
        self, fake: FakeMarketDataSource
    ) -> None:
        instrumentos = fake.list_instruments("spot")
        assert [i.symbol for i in instrumentos] == ["BTC-USDT", "DOGE-USDT"]
        assert fake.list_instruments("futures") == []

    def test_supported_timeframes(self, fake: FakeMarketDataSource) -> None:
        # Cada exchange soporta intervalos distintos: aqui, 4h NO.
        assert fake.supported_timeframes() == frozenset({Timeframe.M1, Timeframe.H1})


class TestBootstrapRest:
    def test_fetch_recent_devuelve_el_historico_cargado(
        self, fake: FakeMarketDataSource
    ) -> None:
        # Tras una reconexion hay un HUECO: el bootstrap REST lo rellena.
        historico = [_vela(open_time_ms=_OPEN + i * 60_000) for i in range(3)]
        fake.load_history(*historico)

        assert fake.fetch_recent(_BTC, limit=2) == historico[-2:]
        assert fake.fetch_recent(_BTC, limit=10) == historico


class TestElFakeSabePortarseMal:
    """Lo que justifica que el fake exista: provocar lo que un exchange real hace."""

    def test_puede_emitir_una_vela_de_otro_simbolo_y_la_frontera_la_rechaza(
        self, fake: FakeMarketDataSource
    ) -> None:
        # El fake NO valida (es un exchange, y un exchange miente): quien dice que no
        # es la frontera de confianza. Aqui se ve el sistema entero funcionando: el
        # feed cuela basura, y normalize la para.
        fake.emit(_vela(symbol="ETH-USDT"))
        entregada = fake.poll(timeout_ms=0)[0]

        with pytest.raises(RawCandleRejected) as excinfo:
            candle_payload_from_raw(entregada, _BTC)
        assert excinfo.value.reason is RawCandleRejectionReason.SYMBOL_MISMATCH

    @pytest.mark.parametrize(
        ("roto", "motivo"),
        [
            ({"high": "NaN"}, RawCandleRejectionReason.CONTRACT_VIOLATION),
            ({"low": "-1"}, RawCandleRejectionReason.CONTRACT_VIOLATION),
            ({"high": "90", "low": "95"}, RawCandleRejectionReason.CONTRACT_VIOLATION),
            ({"close": "abc"}, RawCandleRejectionReason.MALFORMED_NUMBER),
            ({"open_time_ms": _OPEN + 7}, RawCandleRejectionReason.CONTRACT_VIOLATION),
        ],
    )
    def test_puede_emitir_datos_rotos_y_ninguno_entra(
        self,
        fake: FakeMarketDataSource,
        roto: dict[str, object],
        motivo: RawCandleRejectionReason,
    ) -> None:
        fake.emit(_vela(**roto))
        entregada = fake.poll(timeout_ms=0)[0]

        with pytest.raises(RawCandleRejected) as excinfo:
            candle_payload_from_raw(entregada, _BTC)
        assert excinfo.value.reason is motivo

    def test_puede_emitir_duplicados_fuera_de_orden_y_tardias(
        self, fake: FakeMarketDataSource
    ) -> None:
        # El fake las ENTREGA tal cual (un exchange real las manda). Quien decide que
        # hacer con ellas es el ingestor (B6: idempotencia por clave y politica de
        # tardias); aqui solo se demuestra que el guion puede producirlas.
        primera = _vela(open_time_ms=_OPEN + 60_000)
        duplicada = _vela(open_time_ms=_OPEN + 60_000)  # exactamente la misma
        fuera_de_orden = _vela(open_time_ms=_OPEN)  # anterior a la ya vista
        fake.emit(primera, duplicada, fuera_de_orden)

        entregadas = fake.poll(timeout_ms=0)

        assert entregadas == [primera, duplicada, fuera_de_orden]
        assert entregadas[0] == entregadas[1]  # el duplicado es EXACTO
        assert entregadas[2].open_time_ms < entregadas[1].open_time_ms  # va hacia atras

    def test_tras_la_desconexion_hay_que_volver_a_abrir(
        self, fake: FakeMarketDataSource
    ) -> None:
        fake.open(_BTC)
        fake.disconnect()
        assert fake.active() == set()

        fake.open(_BTC)  # reconexion

        assert fake.active() == {_BTC.as_stream_key()}
        assert fake.opened == [_BTC.as_stream_key(), _BTC.as_stream_key()]
