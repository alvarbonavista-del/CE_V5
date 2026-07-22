"""Tests del motor de ingesta de TRADES (ADR-014, ADR-006, ADR-007).

Con el FakeTradeSource (adversarial) y un writer en memoria que deduplica por la MISMA
identidad natural que la PK de la tabla. Cero red, cero reloj, cero hilos.

Aqui se demuestran las tres cosas propias de trades frente a velas: que NADA se publica
(no hay bus en este motor), que el dedup va por la identidad natural del trade, y que el
ORDEN ES IRRELEVANTE -- el invariante de reproducibilidad: los mismos trades en
cualquier orden producen EL MISMO conjunto persistido.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import pytest

from ce_v5.infra.connectors.fake_trades import FakeTradeSource
from ce_v5.platform.market.trade_ingestor import (
    TradeIngestionConfig,
    TradeIngestionEngine,
)
from source.families.footprint import MarketTrade
from source.families.market import (
    LastSeenTrade,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    RawTrade,
    TradeBackfillResult,
)

_EVENT_TIME = 1_784_073_600_042

_BTC = MarketStreamKey(
    exchange="binance",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.TRADES,
)
_ETH = MarketStreamKey(
    exchange="binance",
    market_type=MarketType.SPOT,
    symbol="ETH-USDT",
    data_kind=MarketDataKind.TRADES,
)


def _trade(**overrides: object) -> RawTrade:
    base: dict[str, object] = {
        "exchange": "binance",
        "market_type": "spot",
        "symbol": "BTC-USDT",
        "trade_id": "77001",
        "price": "104.25",
        "qty": "0.13",
        "aggressor_side": "buy",
        "event_time_ms": _EVENT_TIME,
    }
    base.update(overrides)
    return RawTrade(**base)  # type: ignore[arg-type]


class _WriterFalso:
    """Trades en memoria, deduplicados por la MISMA identidad natural que la PK de
    market_trade: (exchange, market_type, symbol, trade_id).

    Devuelve False cuando la clave ya estaba, exactamente como el ON CONFLICT DO NOTHING
    ... RETURNING del writer real. Si aqui se dedujera por otra cosa, el test verde no
    diria nada sobre la tabla de verdad.
    """

    def __init__(self) -> None:
        self.guardados: list[MarketTrade] = []
        self._claves: set[tuple[str, str, str, str]] = set()
        # Huecos apuntados, deduplicados por la MISMA identidad que el UNIQUE de
        # market_trade_gap. Si aqui se dedujera por otra cosa, el test verde no diria
        # nada sobre la tabla de verdad.
        self.huecos: list[tuple[str, str, str, int | None, int | None]] = []
        self._huecos: set[tuple[str, str, str, int | None, int | None]] = set()

    def persist(self, trade: MarketTrade) -> bool:
        clave = (
            trade.exchange,
            trade.market_type.value,
            trade.symbol,
            trade.trade_id,
        )
        if clave in self._claves:
            return False
        self._claves.add(clave)
        self.guardados.append(trade)
        return True

    def last_seen(self, exchange: str, market_type: str, symbol: str) -> LastSeenTrade:
        """El de mayor (event_time, trade_id) del flujo, como el ORDER BY del writer."""
        propios = [
            t
            for t in self.guardados
            if (t.exchange, t.market_type.value, t.symbol)
            == (exchange, market_type, symbol)
        ]
        if not propios:
            return LastSeenTrade(trade_id=None, event_time_ms=None)
        ultimo = max(propios, key=lambda t: (t.event_time, t.trade_id))
        return LastSeenTrade(
            trade_id=ultimo.trade_id, event_time_ms=int(ultimo.event_time)
        )

    def record_gap(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        gap_from_event_time_ms: int | None,
        gap_to_event_time_ms: int | None,
    ) -> bool:
        clave = (
            exchange,
            market_type,
            symbol,
            gap_from_event_time_ms,
            gap_to_event_time_ms,
        )
        if clave in self._huecos:
            return False
        self._huecos.add(clave)
        self.huecos.append(clave)
        return True

    def claves(self) -> set[tuple[str, str, str, str]]:
        return set(self._claves)


class _WriterQueLanza:
    """Writer que revienta. Solo se usa para demostrar que el motor NO lo llama cuando
    el trade viene de un stream no suscrito o no cruza la frontera de confianza.
    """

    def persist(self, trade: MarketTrade) -> bool:
        msg = "el writer no deberia haberse llamado"
        raise AssertionError(msg)

    def last_seen(self, exchange: str, market_type: str, symbol: str) -> LastSeenTrade:
        return LastSeenTrade(trade_id=None, event_time_ms=None)

    def record_gap(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        gap_from_event_time_ms: int | None,
        gap_to_event_time_ms: int | None,
    ) -> bool:
        msg = "no deberia haberse apuntado ningun hueco"
        raise AssertionError(msg)


class _SourceReconectado:
    """Doble minimo de TradeDataSourcePort para el backfill: entrega un set de
    reconectados UNA vez; backfill_after_reconnect devuelve un resultado fijo, o LANZA
    (para probar fault isolation). Sin red, sin hilos.
    """

    def __init__(
        self,
        reconectados: Iterable[str],
        resultado: TradeBackfillResult | None = None,
        *,
        backfill_lanza: bool = False,
    ) -> None:
        self._reconectados = set(reconectados)
        self._resultado = resultado or TradeBackfillResult(
            raw_trades=(),
            covered=True,
            gap_from_event_time_ms=None,
            gap_to_event_time_ms=None,
        )
        self._backfill_lanza = backfill_lanza

    def open(self, key: MarketStreamKey) -> None:
        return None

    def close(self, key: MarketStreamKey) -> None:
        return None

    def active(self) -> set[str]:
        return set()

    def poll_trades(self, timeout_ms: int) -> Sequence[RawTrade]:
        return []

    def backfill_after_reconnect(
        self, key: MarketStreamKey, last_seen: LastSeenTrade
    ) -> TradeBackfillResult:
        if self._backfill_lanza:
            msg = "REST caido"
            raise RuntimeError(msg)
        return self._resultado

    def drain_reconnected(self) -> set[str]:
        copia = set(self._reconectados)
        self._reconectados.clear()
        return copia


@pytest.fixture
def source() -> FakeTradeSource:
    fake = FakeTradeSource()
    fake.open(_BTC)
    fake.open(_ETH)
    return fake


@pytest.fixture
def writer() -> _WriterFalso:
    return _WriterFalso()


def _motor(
    source: FakeTradeSource | _SourceReconectado,
    writer: _WriterFalso | _WriterQueLanza,
    max_batch: int = 500,
) -> TradeIngestionEngine:
    return TradeIngestionEngine(
        source=source,
        writer=writer,
        config=TradeIngestionConfig(max_batch=max_batch),
    )


class TestPersistencia:
    def test_los_trades_de_un_stream_suscrito_se_persisten(
        self, source: FakeTradeSource, writer: _WriterFalso
    ) -> None:
        source.emit(
            _trade(trade_id="1"),
            _trade(trade_id="2", aggressor_side="sell", qty="0.4"),
        )
        metrics = _motor(source, writer).drain_once()

        assert metrics.trades_persisted == 2
        assert [t.trade_id for t in writer.guardados] == ["1", "2"]

    def test_los_trades_no_se_publican_a_ningun_bus(
        self, source: FakeTradeSource, writer: _WriterFalso
    ) -> None:
        # LA DIFERENCIA DE FONDO CON LAS VELAS: el motor de trades NO recibe bus. Un par
        # liquido produce miles de trades por minuto; publicarlos uno a uno seria la
        # avalancha de I-02. Lo que se publica es el FOOTPRINT por barra.
        #
        # No se comprueba con un mock: se comprueba con la FIRMA. Si alguien anadiera
        # una publicacion, tendria que anadir antes un bus al constructor, y este test
        # dejaria de compilar.
        engine = TradeIngestionEngine(source=source, writer=writer)
        assert not hasattr(engine, "_bus")
        source.emit(_trade())
        assert engine.drain_once().trades_persisted == 1


class TestDedup:
    def test_el_mismo_trade_dos_veces_entra_una_sola(
        self, source: FakeTradeSource, writer: _WriterFalso
    ) -> None:
        # El dedup va por la identidad NATURAL del trade (su trade_id del exchange). Es
        # el caso NORMAL tras una reconexion, no un error.
        motor = _motor(source, writer)
        source.emit(_trade(trade_id="77001"))
        motor.drain_once()

        source.emit(_trade(trade_id="77001"))
        metrics = motor.drain_once()

        assert metrics.trades_persisted == 1
        assert metrics.duplicates_skipped == 1
        assert len(writer.guardados) == 1

    def test_dos_trades_del_mismo_ms_y_precio_son_hechos_distintos(
        self, source: FakeTradeSource, writer: _WriterFalso
    ) -> None:
        # DOS trades identicos salvo el trade_id son DOS hechos: dos personas compraron
        # lo mismo en el mismo milisegundo. Deduplicarlos por (tiempo, precio) perderia
        # volumen real del footprint. Por eso la identidad es el trade_id, y solo el.
        source.emit(_trade(trade_id="A"), _trade(trade_id="B"))
        metrics = _motor(source, writer).drain_once()

        assert metrics.trades_persisted == 2
        assert metrics.duplicates_skipped == 0


class TestAislamientoPorStream:
    def test_un_trade_corrupto_no_impide_procesar_los_siguientes(
        self, source: FakeTradeSource, writer: _WriterFalso
    ) -> None:
        # LA PRUEBA QUE IMPORTA: un trade corrupto de BTC NO puede dejar sin datos al
        # stream de ETH que viene detras en el mismo lote. Si el ciclo abortara, un
        # exchange que manda basura por un solo simbolo tumbaria los otros 200.
        bueno_eth = _trade(symbol="ETH-USDT", trade_id="99")
        source.emit(
            _trade(trade_id="malo-1", price="NaN"),  # basura: no finito
            _trade(trade_id="malo-2", qty="0"),  # basura: tamano cero
            _trade(trade_id="malo-3", aggressor_side="taker"),  # basura: lado
            bueno_eth,  # BUENO, y viene DETRAS de la basura
        )
        metrics = _motor(source, writer).drain_once()

        assert metrics.trades_persisted == 1  # el de ETH SI entro.
        assert [t.symbol for t in writer.guardados] == ["ETH-USDT"]
        # Y los tres malos quedaron CONTADOS por su motivo, no perdidos en silencio.
        assert metrics.rejected == {"contract_violation": 3}
        assert metrics.degraded_streams == {_BTC.as_stream_key()}

    def test_un_trade_ilegible_se_cuenta_por_su_propio_motivo(
        self, source: FakeTradeSource, writer: _WriterFalso
    ) -> None:
        source.emit(_trade(trade_id="malo", price="abc"), _trade(trade_id="bueno"))
        metrics = _motor(source, writer).drain_once()

        assert metrics.rejected == {"malformed_number": 1}
        assert metrics.trades_persisted == 1


class TestFlujoNoSuscrito:
    def test_un_trade_de_un_stream_no_suscrito_no_entra(
        self, source: FakeTradeSource
    ) -> None:
        # Nadie pidio ese flujo: un dato que nadie quiere no entra en el historico solo
        # porque el exchange lo mande. El writer que LANZA lo demuestra: si el motor lo
        # llamara, el test reventaria.
        source.emit(_trade(symbol="SOL-USDT"))
        metrics = _motor(source, _WriterQueLanza()).drain_once()

        assert metrics.unsubscribed_dropped == 1
        assert metrics.trades_persisted == 0

    def test_un_trade_de_otro_exchange_tampoco(self, source: FakeTradeSource) -> None:
        source.emit(_trade(exchange="okx"))
        metrics = _motor(source, _WriterQueLanza()).drain_once()

        assert metrics.unsubscribed_dropped == 1


class TestBackpressure:
    def test_una_avalancha_se_procesa_por_tandas_sin_perder_nada(
        self, source: FakeTradeSource, writer: _WriterFalso
    ) -> None:
        # Quien manda es el MOTOR, no el exchange. Lo que NO se procesa se queda
        # ESPERANDO en el feed: no se pierde. Un motor que pidiera los 2000 y tirase
        # 1500 tendria "backpressure" solo de nombre; en realidad estaria perdiendo
        # trades en silencio, y un trade perdido es una celda de footprint que miente.
        source.max_batch = 100  # el feed entrega de cien en cien.
        total = 2_000
        source.emit(*[_trade(trade_id=str(i)) for i in range(total)])
        motor = _motor(source, writer, max_batch=500)

        primera = motor.drain_once()
        assert primera.trades_persisted == 500  # el motor digiere 500 por ciclo.
        assert source.pending_count() == 1_500  # el resto ESPERA, intacto.

        motor.drain_once()
        assert source.pending_count() == 1_000

        motor.drain_once()
        motor.drain_once()
        assert motor.metrics.trades_persisted == total
        assert source.pending_count() == 0


class TestBackfillTrasReconexion:
    """El backfill acotado tras reconexion (ADR-014), orquestado por el motor. SIN RED:
    el conector senala la reconexion (drain_reconnected), el motor le pide que rellene
    desde lo ultimo que la BASE recuerda (last_seen) y procesa lo que vuelva por el
    MISMO camino de normalizacion + dedup.

    Y lo nuevo del modelo honesto: si el conector dice que NO cubrio el hueco, el motor
    lo APUNTA. Un hueco callado se convierte en una barra de footprint que se publica
    como completa sin serlo.
    """

    def test_rellena_el_hueco_sin_perder_ni_duplicar(
        self, source: FakeTradeSource, writer: _WriterFalso
    ) -> None:
        motor = _motor(source, writer)
        # 1) Un trade YA persistido (lo normal antes de una reconexion).
        source.emit(_trade(trade_id="1"))
        motor.drain_once()
        assert len(writer.guardados) == 1

        # 2) El relleno devuelve DOS trades: el MISMO (solape, el caso normal) y uno
        #    NUEVO (el hueco que hubo mientras el socket estuvo caido). CUBIERTO.
        source.load_backfill([_trade(trade_id="1"), _trade(trade_id="2")], covered=True)
        source.simulate_reconnect([_BTC.as_stream_key()])

        metrics = motor.drain_once()

        assert metrics.bootstrap_trades == 2  # se reprocesaron los dos del relleno.
        assert metrics.duplicates_skipped == 1  # el solape NO se re-persiste.
        assert metrics.trades_persisted == 2  # el del paso 1 + el NUEVO del hueco.
        assert [t.trade_id for t in writer.guardados] == ["1", "2"]
        # CUBIERTO: no se apunta ningun hueco.
        assert metrics.uncovered_gaps == 0
        assert writer.huecos == []

    def test_el_backfill_se_pide_desde_lo_que_la_BASE_recuerda(
        self, source: FakeTradeSource, writer: _WriterFalso
    ) -> None:
        # last_seen NO sale de la memoria del motor: sale del store. Es lo que hace que
        # un proceso recien reiniciado, que no recuerda nada, detecte igual su hueco.
        visto: list[LastSeenTrade] = []
        motor = _motor(source, writer)
        source.emit(_trade(trade_id="7", event_time_ms=_EVENT_TIME + 5))
        motor.drain_once()

        original = source.backfill_after_reconnect

        def _espiar(
            key: MarketStreamKey, last_seen: LastSeenTrade
        ) -> TradeBackfillResult:
            visto.append(last_seen)
            return original(key, last_seen)

        source.backfill_after_reconnect = _espiar  # type: ignore[method-assign]
        source.simulate_reconnect([_BTC.as_stream_key()])
        motor.drain_once()

        assert visto == [LastSeenTrade(trade_id="7", event_time_ms=_EVENT_TIME + 5)]

    def test_un_hueco_no_cubierto_se_APUNTA_una_sola_vez(
        self, source: FakeTradeSource, writer: _WriterFalso
    ) -> None:
        # EL CASO QUE JUSTIFICA TODA LA TANDA: el corte duro mas de lo que el REST del
        # exchange puede devolver, asi que parte del hueco NO se recupera JAMAS. Se
        # registra DONDE falta para que 3b marque incompletas las barras solapadas.
        motor = _motor(source, writer)
        source.load_backfill(
            [_trade(trade_id="9")],
            covered=False,
            gap_from_event_time_ms=_EVENT_TIME,
            gap_to_event_time_ms=_EVENT_TIME + 900,
        )
        source.simulate_reconnect([_BTC.as_stream_key()])
        metrics = motor.drain_once()

        assert metrics.uncovered_gaps == 1
        assert writer.huecos == [
            ("binance", "spot", "BTC-USDT", _EVENT_TIME, _EVENT_TIME + 900)
        ]
        # El stream queda marcado DEGRADADO: no es un ciclo fallido, pero tampoco es
        # normalidad, y quien mire las metricas tiene que verlo.
        assert _BTC.as_stream_key() in metrics.degraded_streams

        # IDEMPOTENCIA: el MISMO hueco detectado otra vez no duplica la fila ni vuelve a
        # contar. La metrica sigue a la BASE, no a la reconexion: si contase
        # reconexiones, pareceria que se pierde dato nuevo cada vez.
        source.load_backfill(
            [_trade(trade_id="9")],
            covered=False,
            gap_from_event_time_ms=_EVENT_TIME,
            gap_to_event_time_ms=_EVENT_TIME + 900,
        )
        source.simulate_reconnect([_BTC.as_stream_key()])
        metrics = motor.drain_once()

        assert metrics.uncovered_gaps == 1  # sigue siendo UNO.
        assert len(writer.huecos) == 1

    def test_un_backfill_que_lanza_no_tumba_el_ciclo(
        self, writer: _WriterFalso
    ) -> None:
        # FAULT ISOLATION: un backfill_after_reconnect que LANZA para un stream se
        # cuenta, marca el stream degradado y NO revienta el ciclo (ni a los demas).
        source = _SourceReconectado({_BTC.as_stream_key()}, backfill_lanza=True)

        metrics = _motor(source, writer).drain_once()  # NO revienta.

        assert metrics.bootstrap_errors == 1
        assert _BTC.as_stream_key() in metrics.degraded_streams
        assert metrics.bootstrap_trades == 0
        assert metrics.uncovered_gaps == 0

    def test_una_clave_reconectada_corrupta_se_cuenta_sin_reventar(
        self, writer: _WriterFalso
    ) -> None:
        # Una clave reconectada que NO parsea: se cuenta como error y se salta. Nada de
        # raise; backfill_after_reconnect ni se llega a llamar.
        source = _SourceReconectado({"no-es-una-clave-valida"})

        metrics = _motor(source, writer).drain_once()  # NO revienta.

        assert metrics.bootstrap_errors == 1
        assert metrics.bootstrap_trades == 0


class TestReproducibilidad:
    """EL INVARIANTE RATIFICADO POR CENTRAL: el orden es IRRELEVANTE.

    El trade_id es clave de DEDUP, no criterio de orden, y la agregacion posterior a
    footprint es CONMUTATIVA. Por tanto los mismos trades entregados en cualquier orden
    tienen que producir EL MISMO conjunto persistido. Si no fuera asi, dos replicas del
    ingestor -- o la misma tras una reconexion, que reordena lo que reenvia -- llegarian
    a historicos distintos a partir del mismo mercado, y el footprint dejaria de ser
    reproducible bit a bit.
    """

    def test_el_mismo_lote_en_distinto_orden_persiste_el_mismo_conjunto(self) -> None:
        lote = [
            _trade(trade_id="1", price="100", qty="1", aggressor_side="buy"),
            _trade(trade_id="2", price="101", qty="2", aggressor_side="sell"),
            _trade(trade_id="3", price="100", qty="3", aggressor_side="buy"),
            _trade(trade_id="4", price="102", qty="4", aggressor_side="sell"),
        ]

        def _ingerir(trades: Sequence[RawTrade]) -> _WriterFalso:
            source = FakeTradeSource()
            source.open(_BTC)
            source.emit(*trades)
            writer = _WriterFalso()
            _motor(source, writer).drain_once()
            return writer

        directo = _ingerir(lote)
        inverso = _ingerir(list(reversed(lote)))
        barajado = _ingerir([lote[2], lote[0], lote[3], lote[1]])

        # MISMO CONJUNTO por identidad natural, no misma lista: el orden de llegada no
        # forma parte del hecho.
        assert directo.claves() == inverso.claves() == barajado.claves()
        # Y el contenido tambien coincide, trade a trade, mirado por su identidad.
        por_id = {t.trade_id: t for t in directo.guardados}
        for otro in (inverso, barajado):
            assert {t.trade_id: t for t in otro.guardados} == por_id

    def test_el_solape_reordenado_de_una_reconexion_no_duplica(self) -> None:
        # Caso REAL: tras reconectar, el REST devuelve los mismos trades en otro orden y
        # con un hueco relleno. Ni se duplica lo que ya estaba ni se pierde lo nuevo.
        source = FakeTradeSource()
        source.open(_BTC)
        writer = _WriterFalso()
        motor = _motor(source, writer)

        source.emit(_trade(trade_id="1"), _trade(trade_id="2"))
        motor.drain_once()

        source.load_backfill(
            [
                _trade(trade_id="3"),  # el hueco, y llega el PRIMERO
                _trade(trade_id="2"),
                _trade(trade_id="1"),
            ],
            covered=True,
        )
        source.simulate_reconnect([_BTC.as_stream_key()])
        metrics = motor.drain_once()

        assert metrics.trades_persisted == 3
        assert metrics.duplicates_skipped == 2
        assert writer.claves() == {
            ("binance", "spot", "BTC-USDT", tid) for tid in ("1", "2", "3")
        }
