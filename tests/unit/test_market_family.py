"""Tests de la familia market.* (ADR-014, ADR-007, CA-06).

Demuestran que el contrato DEFIENDE el borde: los datos de un exchange son
entrada NO confiable y no pueden propagarse al bus si son incoherentes.
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from source.families.market import (
    CandleClosedPayload,
    CandleCorrectedPayload,
    CandleUpdatedPayload,
    MarketCandleEventType,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    Timeframe,
    candle_idempotency_key,
)
from source.families.registry import (
    DEFERRED_EVENT_TYPES,
    expected_event_schema_version,
    payload_class_for,
)
from source.time import MaturityState

# Ventana 1m alineada: 2026-07-14T00:00:00Z.
OPEN_TIME = 1_784_073_600_000
CLOSE_TIME = OPEN_TIME + 60_000 - 1


def _key() -> MarketStreamKey:
    return MarketStreamKey(
        exchange="binance",
        market_type=MarketType.SPOT,
        symbol="BTC-USDT",
        data_kind=MarketDataKind.CANDLES,
        timeframe=Timeframe.M1,
    )


def _ohlcv(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "exchange": "binance",
        "market_type": MarketType.SPOT,
        "symbol": "BTC-USDT",
        "timeframe": Timeframe.M1,
        "open_time": OPEN_TIME,
        "close_time": CLOSE_TIME,
        "open": Decimal("100.00"),
        "high": Decimal("110.00"),
        "low": Decimal("95.00"),
        "close": Decimal("105.00"),
        "volume": Decimal("12.5"),
    }
    base.update(overrides)
    return base


class TestMarketStreamKey:
    def test_stream_key_determinista(self) -> None:
        assert _key().as_stream_key() == "market:candles:binance:spot:BTC-USDT:1m"

    def test_dos_sujetos_mismo_flujo_misma_clave(self) -> None:
        # El corazon de ADR-014: si la clave no fuese identica, cada tenant
        # abriria su propio stream y volveria la explosion N x M.
        assert _key().as_stream_key() == _key().as_stream_key()

    def test_candles_exige_timeframe(self) -> None:
        with pytest.raises(ValidationError):
            MarketStreamKey(
                exchange="binance",
                market_type=MarketType.SPOT,
                symbol="BTC-USDT",
                data_kind=MarketDataKind.CANDLES,
            )

    def test_simbolo_nativo_rechazado(self) -> None:
        # BTCUSDT es la forma NATIVA de Binance, no la canonica.
        with pytest.raises(ValidationError):
            MarketStreamKey(
                exchange="binance",
                market_type=MarketType.SPOT,
                symbol="BTCUSDT",
                data_kind=MarketDataKind.CANDLES,
                timeframe=Timeframe.M1,
            )

    def test_ticker_de_un_caracter_es_valido(self) -> None:
        # REGRESION del hallazgo en caliente (B12b): Binance tiene el ticker 'T'
        # (Threshold), par nativo TUSDT -> canonico T-USDT. El patron {2,15} original
        # lo rechazaba por una suposicion sin verificar; con {1,20} se construye y su
        # clave hace ida y vuelta exacta.
        clave = MarketStreamKey(
            exchange="binance",
            market_type=MarketType.SPOT,
            symbol="T-USDT",
            data_kind=MarketDataKind.CANDLES,
            timeframe=Timeframe.M1,
        )
        assert clave.as_stream_key() == "market:candles:binance:spot:T-USDT:1m"
        assert MarketStreamKey.parse(clave.as_stream_key()) == clave

    @pytest.mark.parametrize("simbolo", ["BTCUSDT", "-USDT", "BTC-"])
    def test_simbolos_sin_guion_o_con_parte_vacia_siguen_rechazados(
        self, simbolo: str
    ) -> None:
        # El {1,20} exige >=1 caracter a cada lado, NO admite vacio: 'BTCUSDT' (sin
        # guion), '-USDT' (base vacia) y 'BTC-' (quote vacia) siguen rechazados.
        with pytest.raises(ValidationError):
            MarketStreamKey(
                exchange="binance",
                market_type=MarketType.SPOT,
                symbol=simbolo,
                data_kind=MarketDataKind.CANDLES,
                timeframe=Timeframe.M1,
            )

    def test_exchange_en_mayusculas_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            MarketStreamKey(
                exchange="Binance",
                market_type=MarketType.SPOT,
                symbol="BTC-USDT",
                data_kind=MarketDataKind.CANDLES,
                timeframe=Timeframe.M1,
            )


class TestParseDeLaClave:
    """parse() es el inverso EXACTO de as_stream_key() (ADR-014)."""

    @pytest.mark.parametrize(
        "clave",
        [
            _key(),
            MarketStreamKey(
                exchange="okx",
                market_type=MarketType.SPOT,
                symbol="ETH-EUR",
                data_kind=MarketDataKind.CANDLES,
                timeframe=Timeframe.H4,
            ),
            MarketStreamKey(
                exchange="binance",
                market_type=MarketType.SPOT,
                symbol="DOGE-USDT",
                data_kind=MarketDataKind.CANDLES,
                timeframe=Timeframe.D1,
            ),
        ],
    )
    def test_ida_y_vuelta(self, clave: MarketStreamKey) -> None:
        # LA PROPIEDAD QUE IMPORTA: garantiza que el manager se suscribe a LO QUE SE
        # PIDIO. La ventanilla solo devuelve la CLAVE (no puede devolver mas sin
        # revelar mas), asi que si el parser no fuese el inverso exacto, el worker
        # abriria un stream distinto del que alguien pidio.
        assert MarketStreamKey.parse(clave.as_stream_key()) == clave

    @pytest.mark.parametrize(
        ("clave", "motivo"),
        [
            ("bolsa:candles:binance:spot:BTC-USDT:1m", "prefijo distinto de market"),
            ("market:candles:binance:spot", "faltan partes"),
            ("market:candles:binance:spot:BTC-USDT:1m:extra", "sobran partes"),
            ("market:candles:BINANCE:spot:BTC-USDT:1m", "exchange en mayusculas"),
            ("market:candles:binance:spot:BTCUSDT:1m", "simbolo nativo, no canonico"),
            ("market:candles:binance:spot:BTC-USDT:2m", "timeframe inexistente"),
            ("market:orderbook:binance:spot:BTC-USDT:1m", "data_kind desconocido"),
            ("market:candles:binance:spot:BTC-USDT", "candles sin timeframe"),
        ],
    )
    def test_clave_malformada_rechazada(self, clave: str, motivo: str) -> None:
        # Un parser permisivo es un parser que un dia acepta basura y abre un stream
        # que nadie pidio. Pydantic lanza ValidationError (subclase de ValueError) en
        # los patrones; el parser lanza ValueError en la forma y en los enums.
        with pytest.raises(ValueError):
            MarketStreamKey.parse(clave)


class TestEntradaNoConfiable:
    def test_precio_nan_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            CandleClosedPayload(
                maturity_state=MaturityState.CLOSED,
                **_ohlcv(high=Decimal("NaN")),
            )

    def test_precio_infinito_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            CandleClosedPayload(
                maturity_state=MaturityState.CLOSED,
                **_ohlcv(close=Decimal("Infinity")),
            )

    def test_precio_negativo_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            CandleClosedPayload(
                maturity_state=MaturityState.CLOSED,
                **_ohlcv(low=Decimal("-1")),
            )

    def test_volumen_negativo_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            CandleClosedPayload(
                maturity_state=MaturityState.CLOSED,
                **_ohlcv(volume=Decimal("-0.1")),
            )

    def test_rango_incoherente_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            CandleClosedPayload(
                maturity_state=MaturityState.CLOSED,
                **_ohlcv(high=Decimal("90"), low=Decimal("95")),
            )

    def test_vela_desalineada_rechazada(self) -> None:
        with pytest.raises(ValidationError):
            CandleClosedPayload(
                maturity_state=MaturityState.CLOSED,
                **_ohlcv(open_time=OPEN_TIME + 1, close_time=CLOSE_TIME + 1),
            )

    def test_close_time_fuera_de_ventana_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            CandleClosedPayload(
                maturity_state=MaturityState.CLOSED,
                **_ohlcv(close_time=OPEN_TIME + 60_001),
            )

    def test_campo_extra_del_exchange_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            CandleClosedPayload(
                maturity_state=MaturityState.CLOSED,
                **_ohlcv(campo_desconocido="lo que sea"),
            )


class TestMadurezPorTipo:
    def test_vela_cerrada_marcada_provisional_rechazada(self) -> None:
        with pytest.raises(ValidationError):
            CandleClosedPayload(maturity_state=MaturityState.PROVISIONAL, **_ohlcv())

    def test_vela_provisional_marcada_cerrada_rechazada(self) -> None:
        with pytest.raises(ValidationError):
            CandleUpdatedPayload(maturity_state=MaturityState.CLOSED, **_ohlcv())

    def test_correccion_sin_referencia_al_original_rechazada(self) -> None:
        # Regla heredada de MaturityAwarePayload (ADR-007).
        with pytest.raises(ValidationError):
            CandleCorrectedPayload(
                maturity_state=MaturityState.CORRECTION,
                correction_revision=1,
                **_ohlcv(),
            )

    def test_correccion_sin_revision_rechazada(self) -> None:
        with pytest.raises(ValidationError):
            CandleCorrectedPayload(
                maturity_state=MaturityState.CORRECTION,
                corrects_idempotency_key="market.candle_closed|x|0|closed",
                **_ohlcv(),
            )

    def test_correccion_con_revision_none_rechazada(self) -> None:
        # CA-P08-09: correction_revision es int OBLIGATORIO en este tipo. None se
        # rechaza AL CONSTRUIR el payload (el TIPO del campo, no un validador aparte ni
        # una guarda en el worker): antes de esto el worker tenia una barrera manual
        # (7.3-c) que ahora es innecesaria. Un None jamas llega al motor.
        with pytest.raises(ValidationError):
            CandleCorrectedPayload(
                maturity_state=MaturityState.CORRECTION,
                corrects_idempotency_key="market.candle_closed|x|0|closed",
                correction_revision=None,
                **_ohlcv(),
            )

    def test_revision_en_vela_provisional_rechazada(self) -> None:
        with pytest.raises(ValidationError):
            CandleUpdatedPayload(
                maturity_state=MaturityState.PROVISIONAL,
                correction_revision=1,
                **_ohlcv(),
            )

    def test_vela_valida_de_cada_tipo(self) -> None:
        updated = CandleUpdatedPayload(
            maturity_state=MaturityState.PROVISIONAL, **_ohlcv()
        )
        closed = CandleClosedPayload(maturity_state=MaturityState.CLOSED, **_ohlcv())
        corrected = CandleCorrectedPayload(
            maturity_state=MaturityState.CORRECTION,
            corrects_idempotency_key=closed.idempotency_key(
                MarketCandleEventType.CANDLE_CLOSED
            ),
            correction_revision=1,
            **_ohlcv(close=Decimal("106.00")),
        )
        assert updated.stream_key() == closed.stream_key() == corrected.stream_key()


class TestIdempotencyKey:
    def test_formula_y_unicidad_por_construccion(self) -> None:
        closed = CandleClosedPayload(maturity_state=MaturityState.CLOSED, **_ohlcv())
        assert closed.idempotency_key(MarketCandleEventType.CANDLE_CLOSED) == (
            f"market.candle_closed|market:candles:binance:spot:BTC-USDT:1m"
            f"|{OPEN_TIME}|closed"
        )

    def test_provisional_y_cerrada_de_la_misma_ventana_no_colisionan(self) -> None:
        updated = CandleUpdatedPayload(
            maturity_state=MaturityState.PROVISIONAL, **_ohlcv()
        )
        closed = CandleClosedPayload(maturity_state=MaturityState.CLOSED, **_ohlcv())
        assert updated.idempotency_key(
            MarketCandleEventType.CANDLE_UPDATED
        ) != closed.idempotency_key(MarketCandleEventType.CANDLE_CLOSED)

    def test_dos_correcciones_de_la_misma_vela_son_dos_hechos(self) -> None:
        # SIN correction_revision, ambas claves serian IDENTICAS y el indice
        # UNIQUE de la outbox (P02b) se tragaria la segunda EN SILENCIO.
        original = (
            "market.candle_closed|market:candles:binance:spot:BTC-USDT:1m|0|closed"
        )
        primera = candle_idempotency_key(
            event_type=MarketCandleEventType.CANDLE_CORRECTED,
            stream_key="market:candles:binance:spot:BTC-USDT:1m",
            open_time=OPEN_TIME,
            maturity_state=MaturityState.CORRECTION,
            correction_revision=1,
        )
        segunda = candle_idempotency_key(
            event_type=MarketCandleEventType.CANDLE_CORRECTED,
            stream_key="market:candles:binance:spot:BTC-USDT:1m",
            open_time=OPEN_TIME,
            maturity_state=MaturityState.CORRECTION,
            correction_revision=2,
        )
        assert primera != segunda
        assert original not in (primera, segunda)

    def test_revision_exigida_en_correccion(self) -> None:
        with pytest.raises(ValueError, match="correction_revision"):
            candle_idempotency_key(
                event_type=MarketCandleEventType.CANDLE_CORRECTED,
                stream_key="market:candles:binance:spot:BTC-USDT:1m",
                open_time=OPEN_TIME,
                maturity_state=MaturityState.CORRECTION,
            )

    def test_matriz_de_no_colision_por_dimension(self) -> None:
        # COMPONENTES exigidos por el dictamen P07-A en la idempotency_key de una vela
        # PUBLICA: event_type (familia.tipo) | stream_key (= exchange : market_type :
        # symbol : timeframe, SIN tenant en los publicos, ADR-011) | open_time |
        # maturity_state (y, en una correccion, su revision). Variar CUALQUIERA de esos
        # ejes cambia la clave: cero colisiones.
        def _closed_key(**overrides: object) -> str:
            payload = CandleClosedPayload(
                maturity_state=MaturityState.CLOSED, **_ohlcv(**overrides)
            )
            return payload.idempotency_key(MarketCandleEventType.CANDLE_CLOSED)

        def _correccion_key(revision: int) -> str:
            return candle_idempotency_key(
                event_type=MarketCandleEventType.CANDLE_CORRECTED,
                stream_key=_key().as_stream_key(),
                open_time=OPEN_TIME,
                maturity_state=MaturityState.CORRECTION,
                correction_revision=revision,
            )

        updated = CandleUpdatedPayload(
            maturity_state=MaturityState.PROVISIONAL, **_ohlcv()
        )
        variantes = {
            # dos EXCHANGES / dos TIMEFRAMES / dos SYMBOLS: cada eje, a solas.
            "closed_base": _closed_key(),
            "otro_exchange": _closed_key(exchange="okx"),
            "otro_timeframe": _closed_key(timeframe=Timeframe.M5),
            "otro_symbol": _closed_key(symbol="ETH-USDT"),
            # los TRES maturity/event_type de la MISMA ventana: distintos entre si.
            "misma_ventana_updated": updated.idempotency_key(
                MarketCandleEventType.CANDLE_UPDATED
            ),
            # dos CORRECCIONES (rev 1 vs 2) de la misma vela: distintas.
            "misma_ventana_correccion_rev1": _correccion_key(1),
            "misma_ventana_correccion_rev2": _correccion_key(2),
        }
        # closed_base cubre a la vez la cerrada base y el tercer maturity (closed): asi
        # no se cuenta dos veces la misma clave. Cero colisiones = tantas claves
        # DISTINTAS como variantes.
        assert len(set(variantes.values())) == len(variantes)

        # Y la FORMULA de una cerrada, verbatim (P07-A): el stream_key ya lleva
        # exchange+market_type+symbol+timeframe, sin tenant en los publicos.
        assert variantes["closed_base"] == (
            f"market.candle_closed|{_key().as_stream_key()}|{OPEN_TIME}|closed"
        )


class TestRegistroCA06:
    def test_los_tres_market_resuelven_a_su_payload_concreto(self) -> None:
        assert (
            payload_class_for(MarketCandleEventType.CANDLE_UPDATED.value)
            is CandleUpdatedPayload
        )
        assert (
            payload_class_for(MarketCandleEventType.CANDLE_CLOSED.value)
            is CandleClosedPayload
        )
        assert (
            payload_class_for(MarketCandleEventType.CANDLE_CORRECTED.value)
            is CandleCorrectedPayload
        )

    def test_event_schema_version_de_los_tres(self) -> None:
        for event_type in MarketCandleEventType:
            assert expected_event_schema_version(event_type.value) == 1

    def test_no_queda_ningun_tipo_diferido(self) -> None:
        # La tarea vinculante CA-06 sobre P07 queda PAGADA: no hay en CE v5 ni un
        # solo event_type declarado sin payload.
        assert DEFERRED_EVENT_TYPES == {}
