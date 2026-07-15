"""Tests del motor de ingesta (ADR-007, ADR-013, ADR-014).

Con el FakeMarketDataSource (adversarial), un writer en memoria, un bus falso y un
SimulatedClock. Aqui se demuestra la REGLA DE ORO de la pieza: lo cerrado se persiste
Y se encola en la MISMA llamada (imposible una cosa sin la otra), y lo provisional va
directo al bus sin tocar el historico.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from decimal import Decimal

import pytest

from ce_v5.core.bus import BusMessage, Offset
from ce_v5.core.clock import SimulatedClock
from ce_v5.infra.connectors.fake_market import FakeMarketDataSource
from ce_v5.platform.market.ingestor import (
    IngestionConfig,
    IngestionEngine,
)
from source.families.market import (
    CandlePayload,
    Instrument,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    RawCandle,
    StoredCandle,
    Timeframe,
)

_AHORA = 1_800_000_000_000  # instante del reloj SIMULADO (nuestro)
_OPEN = 1_784_073_600_000  # ventana 1m alineada
_CLOSE = _OPEN + 59_999
_EVENT_TIME = _OPEN + 42  # instante que pone el EXCHANGE en su mensaje

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
        "event_time_ms": _EVENT_TIME,
    }
    base.update(overrides)
    return RawCandle(**base)  # type: ignore[arg-type]


class _WriterFalso:
    """Historico en memoria. Apunta CADA persist_and_enqueue: si una vela se guardara
    sin encolar, no habria forma de que apareciese aqui a medias.
    """

    def __init__(self) -> None:
        self.guardadas: list[tuple[str, str, CandlePayload]] = []
        self._por_ventana: dict[tuple[str, int], StoredCandle] = {}
        self._claves: set[str] = set()

    def existing(self, stream_key: str, open_time_ms: int) -> StoredCandle | None:
        return self._por_ventana.get((stream_key, open_time_ms))

    def persist_and_enqueue(
        self,
        envelope_json: bytes,
        payload: CandlePayload,
        event_type: str,
        stream_key: str,
        idempotency_key: str,
    ) -> bool:
        if idempotency_key in self._claves:
            return False  # dedup por clave.
        self._claves.add(idempotency_key)
        self.guardadas.append((event_type, idempotency_key, payload))
        if payload.correction_revision is None:
            self._por_ventana[(stream_key, payload.open_time)] = StoredCandle(
                idempotency_key=idempotency_key,
                open=payload.open,
                high=payload.high,
                low=payload.low,
                close=payload.close,
                volume=payload.volume,
                max_correction_revision=0,
            )
        else:
            anterior = self._por_ventana[(stream_key, payload.open_time)]
            self._por_ventana[(stream_key, payload.open_time)] = StoredCandle(
                idempotency_key=anterior.idempotency_key,
                open=anterior.open,
                high=anterior.high,
                low=anterior.low,
                close=anterior.close,
                volume=anterior.volume,
                max_correction_revision=payload.correction_revision,
            )
        return True


class _BusFalso:
    def __init__(self) -> None:
        self.publicados: list[BusMessage] = []

    def publish(self, topic: str, message: BusMessage) -> Offset:
        self.publicados.append(message)
        return Offset("0-0")


class _BusRoto:
    def publish(self, topic: str, message: BusMessage) -> Offset:
        msg = "el bus esta caido"
        raise RuntimeError(msg)


class _SourceReconectado:
    """Doble minimo de MarketDataSourcePort para el bootstrap: entrega un set de
    reconectados UNA vez; fetch_recent devuelve un historico fijo, o LANZA si se le pide
    (para probar fault isolation). Sin red, sin hilos.
    """

    def __init__(
        self,
        reconectados: Iterable[str],
        history: Sequence[RawCandle] = (),
        *,
        fetch_lanza: bool = False,
    ) -> None:
        self._reconectados = set(reconectados)
        self._history = list(history)
        self._fetch_lanza = fetch_lanza

    def open(self, key: MarketStreamKey) -> None:
        return None

    def close(self, key: MarketStreamKey) -> None:
        return None

    def active(self) -> set[str]:
        return set()

    def poll(self, timeout_ms: int) -> Sequence[RawCandle]:
        return []

    def fetch_recent(self, key: MarketStreamKey, limit: int) -> Sequence[RawCandle]:
        if self._fetch_lanza:
            msg = "REST caido"
            raise RuntimeError(msg)
        return list(self._history)

    def list_instruments(self, market_type: str) -> Sequence[Instrument]:
        return []

    def supported_timeframes(self) -> frozenset[Timeframe]:
        return frozenset()

    def drain_reconnected(self) -> set[str]:
        copia = set(self._reconectados)
        self._reconectados.clear()
        return copia


@pytest.fixture
def source() -> FakeMarketDataSource:
    fake = FakeMarketDataSource(timeframes=[Timeframe.M1])
    fake.open(_BTC)
    fake.open(_ETH)
    return fake


@pytest.fixture
def writer() -> _WriterFalso:
    return _WriterFalso()


@pytest.fixture
def bus() -> _BusFalso:
    return _BusFalso()


def _motor(
    source: FakeMarketDataSource,
    writer: _WriterFalso,
    bus: object,
    max_batch: int = 500,
) -> IngestionEngine:
    return IngestionEngine(
        source=source,
        writer=writer,
        bus=bus,  # type: ignore[arg-type]
        clock=SimulatedClock(start_ms=_AHORA),
        component_source="market-ingestor",
        config=IngestionConfig(max_batch=max_batch),
    )


def _envelope(bus: _BusFalso, indice: int = 0) -> dict[str, object]:
    parsed = json.loads(bus.publicados[indice].envelope.decode())
    assert isinstance(parsed, dict)
    return parsed


class TestProvisional:
    def test_va_directa_al_bus_y_no_toca_el_historico(
        self, source: FakeMarketDataSource, writer: _WriterFalso, bus: _BusFalso
    ) -> None:
        # Una provisional NO ES HISTORIA: es una vista viva. Persistirla la convertiria
        # en un hecho que luego contradiria a la vela cerrada de esa misma ventana.
        source.emit(_vela(is_closed=False))
        metrics = _motor(source, writer, bus).drain_once()

        assert metrics.provisional_published == 1
        assert len(bus.publicados) == 1
        assert bus.publicados[0].event_type == "market.candle_updated"
        assert writer.guardadas == []  # EL WRITER NO RECIBE NADA.

    def test_fallo_del_bus_es_fail_loud(
        self, source: FakeMarketDataSource, writer: _WriterFalso
    ) -> None:
        # Una vista viva que se pierde EN SILENCIO es un grafico que miente. Propaga.
        source.emit(_vela(is_closed=False))
        with pytest.raises(RuntimeError, match="bus esta caido"):
            _motor(source, writer, _BusRoto()).drain_once()

    def test_provisional_de_una_ventana_ya_cerrada_se_descarta(
        self, source: FakeMarketDataSource, writer: _WriterFalso, bus: _BusFalso
    ) -> None:
        # La ventana ya tiene su verdad definitiva publicada. Una provisional tardia de
        # esa ventana no aporta nada y CONTRADIRIA a la cerrada.
        motor = _motor(source, writer, bus)
        source.emit(_vela(is_closed=True))
        motor.drain_once()

        source.emit(_vela(is_closed=False))  # llega tarde, misma ventana.
        metrics = motor.drain_once()

        assert metrics.out_of_order_dropped == 1
        assert metrics.provisional_published == 0
        assert len(bus.publicados) == 0  # no se publico ninguna provisional.


class TestCerrada:
    def test_se_persiste_y_se_encola_en_la_misma_llamada(
        self, source: FakeMarketDataSource, writer: _WriterFalso, bus: _BusFalso
    ) -> None:
        # LA REGLA DE ORO: UNA sola llamada hace las dos cosas. No hay ninguna ruta que
        # guarde sin encolar ni que encole sin guardar, porque no hay dos metodos.
        source.emit(_vela())
        metrics = _motor(source, writer, bus).drain_once()

        assert metrics.closed_persisted == 1
        assert len(writer.guardadas) == 1
        event_type, _, payload = writer.guardadas[0]
        assert event_type == "market.candle_closed"
        assert payload.close == Decimal("105")
        # La cerrada NO va directa al bus: va por OUTBOX (la encola el writer).
        assert bus.publicados == []

    def test_repetida_e_identica_es_un_duplicado_no_una_correccion(
        self, source: FakeMarketDataSource, writer: _WriterFalso, bus: _BusFalso
    ) -> None:
        # ESTO PASA SIEMPRE tras una reconexion + bootstrap REST: es el caso NORMAL.
        # Tratarlo como correccion llenaria el historico de correcciones fantasma.
        motor = _motor(source, writer, bus)
        source.emit(_vela())
        motor.drain_once()

        source.emit(_vela())  # exactamente la misma
        metrics = motor.drain_once()

        assert metrics.duplicates_skipped == 1
        assert metrics.corrections_emitted == 0
        assert len(writer.guardadas) == 1  # no se duplico en el historico.

    def test_repetida_y_distinta_emite_una_correccion(
        self, source: FakeMarketDataSource, writer: _WriterFalso, bus: _BusFalso
    ) -> None:
        # El exchange cambio el pasado. El original NO SE TOCA (append-only): la
        # correccion es un hecho NUEVO que lo referencia.
        motor = _motor(source, writer, bus)
        source.emit(_vela())
        motor.drain_once()
        _, clave_original, _ = writer.guardadas[0]

        source.emit(_vela(close="106"))
        metrics = motor.drain_once()

        assert metrics.corrections_emitted == 1
        assert len(writer.guardadas) == 2  # el original SIGUE ahi.
        event_type, _, correccion = writer.guardadas[1]
        assert event_type == "market.candle_corrected"
        assert correccion.corrects_idempotency_key == clave_original
        assert correccion.correction_revision == 1

    def test_una_segunda_correccion_es_otro_hecho_distinto(
        self, source: FakeMarketDataSource, writer: _WriterFalso, bus: _BusFalso
    ) -> None:
        # Sin correction_revision, las dos correcciones compartirian idempotency_key y
        # la outbox se tragaria la segunda EN SILENCIO. Son DOS hechos.
        motor = _motor(source, writer, bus)
        source.emit(_vela())
        motor.drain_once()
        source.emit(_vela(close="106"))
        motor.drain_once()

        source.emit(_vela(close="107"))
        metrics = motor.drain_once()

        assert metrics.corrections_emitted == 2
        _, clave_1, primera = writer.guardadas[1]
        _, clave_2, segunda = writer.guardadas[2]
        assert primera.correction_revision == 1
        assert segunda.correction_revision == 2
        assert clave_1 != clave_2  # dos hechos, dos claves.


class TestEnvelope:
    def test_event_time_lo_pone_el_exchange_y_los_demas_el_clock(
        self, source: FakeMarketDataSource, writer: _WriterFalso, bus: _BusFalso
    ) -> None:
        # ADR-007: event_time LO FIJA EL ORIGEN DEL HECHO. Fecharlo con NUESTRO reloj
        # seria inventar cuando ocurrio, y una regla que evalue por tiempo estaria
        # razonando sobre una mentira.
        source.emit(_vela(is_closed=False))
        _motor(source, writer, bus).drain_once()

        envelope = _envelope(bus)
        assert envelope["event_time"] == _EVENT_TIME  # del EXCHANGE.
        assert envelope["event_time"] != _AHORA
        assert envelope["ingestion_time"] == _AHORA  # del Clock.
        assert envelope["processing_time"] == _AHORA  # del Clock.

    def test_los_publicos_no_llevan_tenant(
        self, source: FakeMarketDataSource, writer: _WriterFalso, bus: _BusFalso
    ) -> None:
        # ADR-011: el dato publico se comparte cross-tenant. Meterle tenant_id lo
        # duplicaria por cliente: la explosion N x M que ADR-014 existe para evitar.
        source.emit(_vela(is_closed=False))
        _motor(source, writer, bus).drain_once()

        envelope = _envelope(bus)
        assert envelope["scope"] == "public_market"
        assert envelope["tenant_id"] is None
        assert envelope["stream_key"] == _BTC.as_stream_key()


class TestAislamientoPorStream:
    def test_una_vela_corrupta_no_impide_procesar_las_siguientes(
        self, source: FakeMarketDataSource, writer: _WriterFalso, bus: _BusFalso
    ) -> None:
        # LA PRUEBA QUE IMPORTA: una vela corrupta de BTC NO puede dejar sin datos al
        # stream de ETH que viene detras en el mismo lote. Si el ciclo abortara, un
        # exchange que manda basura por un solo simbolo tumbaria los otros 200.
        #
        # La vela de ETH va con un OHLCV COHERENTE. La primera version de este test la
        # construia con close="200" pero heredaba high="110": un cierre POR ENCIMA del
        # maximo, es decir, una vela fisicamente imposible. El contrato la rechazo, con
        # razon, y el test se puso rojo. Queda anotado: la frontera de confianza cazo
        # basura que colamos NOSOTROS sin querer. No es decorativa.
        buena_eth = _vela(
            symbol="ETH-USDT", open="190", high="210", low="185", close="200"
        )
        source.emit(
            _vela(high="NaN"),  # basura: no finito
            _vela(open_time_ms=_OPEN + 7),  # basura: desalineada
            buena_eth,  # BUENA, y viene DETRAS de la basura
        )
        metrics = _motor(source, writer, bus).drain_once()

        assert metrics.closed_persisted == 1  # la de ETH SI entro.
        assert len(writer.guardadas) == 1
        assert writer.guardadas[0][2].symbol == "ETH-USDT"
        # Y las dos malas quedaron CONTADAS por su motivo, no perdidas en silencio.
        assert metrics.rejected == {"contract_violation": 2}
        assert metrics.degraded_streams == {_BTC.as_stream_key()}

    def test_una_vela_de_otro_simbolo_no_entra_ni_al_bus_ni_al_writer(
        self, source: FakeMarketDataSource, writer: _WriterFalso, bus: _BusFalso
    ) -> None:
        # Suplantacion: la vela dice ser de un flujo suscrito, pero el fake la emite
        # con un symbol que no coincide con la clave por la que llega.
        source.emit(_vela(symbol="BTC-USDT", timeframe="1m", exchange="binance"))
        # Se emite una vela cuyo contenido pertenece a otro flujo del que declara:
        # aqui se fuerza con un market_type ajeno.
        source.emit(_vela(market_type="futures"))
        metrics = _motor(source, writer, bus).drain_once()

        # La primera es legitima; la segunda declara un flujo NO suscrito.
        assert metrics.closed_persisted == 1
        assert metrics.unsubscribed_dropped == 1
        assert len(writer.guardadas) == 1
        assert bus.publicados == []


class TestBackpressure:
    def test_una_avalancha_se_procesa_por_tandas_sin_perder_nada(
        self, source: FakeMarketDataSource, writer: _WriterFalso, bus: _BusFalso
    ) -> None:
        # Quien manda es el INGESTOR, no el exchange. Sin tope, una avalancha se
        # convierte en una cola infinita en memoria y tumba el proceso.
        #
        # Lo que NO se procesa se queda ESPERANDO en el feed: no se pierde. Un motor
        # que pidiera las 2000 y tirase 1500 tendria "backpressure" solo de nombre;
        # en realidad estaria perdiendo velas en silencio.
        #
        # Cada vela lleva SU ventana completa: la primera version movia open_time_ms
        # pero dejaba close_time_ms fijo, asi que de la segunda en adelante la vela
        # CERRABA ANTES DE ABRIR. El contrato las rechazo, con razon. Otra vez: la
        # validacion del borde cazando basura nuestra.
        source.max_batch = 100  # el feed entrega de cien en cien.
        total = 2_000
        source.emit(
            *[
                _vela(
                    is_closed=False,
                    open_time_ms=_OPEN + i * 60_000,
                    close_time_ms=_OPEN + i * 60_000 + 59_999,
                )
                for i in range(total)
            ]
        )
        motor = _motor(source, writer, bus, max_batch=500)

        primera = motor.drain_once()
        assert primera.provisional_published == 500  # el motor digiere 500 por ciclo.
        assert len(bus.publicados) == 500
        assert source.pending_count() == 1_500  # el resto ESPERA, intacto.

        motor.drain_once()
        assert len(bus.publicados) == 1_000
        assert source.pending_count() == 1_000

        # Y hasta el final: las 2000 acaban entrando, ninguna se pierde.
        motor.drain_once()
        motor.drain_once()
        assert len(bus.publicados) == total
        assert source.pending_count() == 0


class TestBootstrapTrasReconexion:
    """El auto-bootstrap tras reconexion (ADR-014), orquestado por el motor. SIN RED: el
    conector senala la reconexion (drain_reconnected) y el motor rellena el hueco via
    fetch_recent por el MISMO camino de normalizacion+dedup.
    """

    def test_rellena_el_hueco_sin_perder_ni_duplicar(
        self, source: FakeMarketDataSource, writer: _WriterFalso, bus: _BusFalso
    ) -> None:
        motor = _motor(source, writer, bus)
        # 1) Una cerrada YA persistida (lo normal antes de una reconexion).
        source.emit(_vela())
        motor.drain_once()
        assert len(writer.guardadas) == 1

        # 2) El bootstrap devolvera DOS velas: la MISMA (duplicado, el caso normal) y
        #    una NUEVA (un hueco real que hubo mientras el socket estuvo caido).
        nueva = _vela(
            open_time_ms=_OPEN + 60_000,
            close_time_ms=_OPEN + 60_000 + 59_999,
        )
        source.load_history(_vela(), nueva)
        source.simulate_reconnect([_BTC.as_stream_key()])

        metrics = motor.drain_once()

        assert metrics.bootstrap_candles == 2  # se reprocesaron las dos del bootstrap.
        assert metrics.duplicates_skipped == 1  # la identica NO se re-persiste.
        assert metrics.closed_persisted == 2  # 1 del paso 1 + la NUEVA del hueco.
        assert len(writer.guardadas) == 2  # el historico NO tiene la duplicada 2 veces.

    def test_un_bootstrap_que_lanza_no_tumba_el_ciclo(
        self, writer: _WriterFalso, bus: _BusFalso
    ) -> None:
        # FAULT ISOLATION: un fetch_recent que LANZA para un stream se cuenta, marca el
        # stream degradado y NO revienta el ciclo (ni a los demas streams).
        source = _SourceReconectado({_BTC.as_stream_key()}, fetch_lanza=True)
        engine = IngestionEngine(
            source=source,
            writer=writer,
            bus=bus,  # type: ignore[arg-type]
            clock=SimulatedClock(start_ms=_AHORA),
            component_source="market-ingestor",
        )

        metrics = engine.drain_once()  # NO revienta.

        assert metrics.bootstrap_errors == 1
        assert _BTC.as_stream_key() in metrics.degraded_streams
        assert metrics.bootstrap_candles == 0

    def test_una_clave_reconectada_corrupta_se_cuenta_sin_reventar(
        self, writer: _WriterFalso, bus: _BusFalso
    ) -> None:
        # Una clave reconectada que NO parsea: se cuenta como error y se salta. Nada de
        # raise; fetch_recent ni se llega a llamar.
        source = _SourceReconectado({"no-es-una-clave-valida"})
        engine = IngestionEngine(
            source=source,
            writer=writer,
            bus=bus,  # type: ignore[arg-type]
            clock=SimulatedClock(start_ms=_AHORA),
            component_source="market-ingestor",
        )

        metrics = engine.drain_once()  # NO revienta.

        assert metrics.bootstrap_errors == 1
        assert metrics.bootstrap_candles == 0
