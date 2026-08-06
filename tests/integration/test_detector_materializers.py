"""Materializacion de los detectores footprint+vela contra PostgreSQL (P08c-DET-01).

Contra PostgreSQL REAL y con los DOS roles que manda la regla 5.20: el footprint y las
velas los ESCRIBE el rol de INGESTA por el camino real (persistir_footprint /
persistir_vela del conftest) y las series las materializa el rol de REGLAS.

QUE ANADE ESTE FICHERO sobre los tests sin BD. Los detectores son las PRIMERAS fuentes
que se materializan sobre DOS tablas a la vez (market_footprint y market_candle), y esas
tablas las escriben caminos distintos: nada garantiza por construccion que cubran las
mismas barras. Aqui se prueba lo que solo se puede probar con las dos tablas de verdad:
que la alineacion se COMPRUEBA y falla RUIDOSO, y que la serie servida es determinista
bit a bit (ADR-007).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from decimal import Decimal

import pytest

from ce_v5.entrypoints.worker_rules.materializers import (
    SOURCE_MATERIALIZERS,
    UnwiredSourceError,
)
from ce_v5.infra.db.market_orderbook import PostgresOrderbookWriter
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.platform.rules.absorption import (
    ABSORPTION_ASK_STRENGTH_SOURCE_ID,
    ABSORPTION_BID_STRENGTH_SOURCE_ID,
)
from ce_v5.platform.rules.climax import (
    CLIMAX_BOTTOM_STRENGTH_SOURCE_ID,
    CLIMAX_TOP_STRENGTH_SOURCE_ID,
)
from ce_v5.platform.rules.imbalance import (
    IMBALANCE_BUY_STACK_SOURCE_ID,
    IMBALANCE_SELL_STACK_SOURCE_ID,
    detect_stacked_imbalance,
)
from ce_v5.platform.rules.notrade import (
    NOTRADE_FLOW_DISLOCATION_SOURCE_ID,
    NOTRADE_FOOTPRINT_INEFF_SOURCE_ID,
    NOTRADE_SCORE_SOURCE_ID,
    NOTRADE_STATE_SOURCE_ID,
    NoTradeState,
)
from ce_v5.platform.rules.void import (
    VOID_SNAP_BEARISH_SOURCE_ID,
    VOID_SNAP_BULLISH_SOURCE_ID,
)
from source.envelope import Envelope, Scope
from source.families.footprint import (
    FootprintCell,
    FootprintClosedPayload,
    FootprintPayload,
    MarketFootprintEventType,
)
from source.families.market import (
    CandleClosedPayload,
    CandlePayload,
    MarketCandleEventType,
    MarketType,
    Timeframe,
)
from source.families.orderbook import (
    MarketOrderbookEventType,
    MarketOrderbookSnapshotKind,
    OrderbookLevel,
    OrderbookSnapshotPayload,
)
from source.families.registry import expected_event_schema_version
from source.rules.scalar import ScalarType, ScalarValue
from source.time import MaturityState

_DSN = os.environ.get("CE_V5_DATABASE_URL")
pytestmark = pytest.mark.skipif(_DSN is None, reason="requiere CE_V5_DATABASE_URL")

_EXCHANGE = "binance"
_SYMBOL = "BTC-USDT"
_TF = Timeframe.H1
_OPEN = 1_784_073_600_000

# La ventana del detector es 100: con 130 barras hay ventana llena y cola suficiente
# para pedir varias barras de salida.
_BARRAS = 130

_ABSORPTION = (ABSORPTION_BID_STRENGTH_SOURCE_ID, ABSORPTION_ASK_STRENGTH_SOURCE_ID)
_CLIMAX = (CLIMAX_TOP_STRENGTH_SOURCE_ID, CLIMAX_BOTTOM_STRENGTH_SOURCE_ID)
_VOID = (VOID_SNAP_BULLISH_SOURCE_ID, VOID_SNAP_BEARISH_SOURCE_ID)
_NOTRADE_DECIMAL = (
    NOTRADE_SCORE_SOURCE_ID,
    NOTRADE_FOOTPRINT_INEFF_SOURCE_ID,
    NOTRADE_FLOW_DISLOCATION_SOURCE_ID,
)
_TODAS = (*_ABSORPTION, *_CLIMAX, *_VOID, *_NOTRADE_DECIMAL, NOTRADE_STATE_SOURCE_ID)

PersistirVela = Callable[[CandlePayload, MarketCandleEventType, int], bool]
PersistirFootprint = Callable[[FootprintPayload, MarketFootprintEventType, int], bool]


def _open_time(indice: int) -> int:
    return _OPEN + indice * _TF.duration_ms


def _footprint(
    indice: int, *, buy: str, sell: str, low: str, high: str
) -> FootprintClosedPayload:
    """Footprint de dos niveles: el span (high-low) alimenta el absorption_ratio."""
    open_time = _open_time(indice)
    cells = (
        FootprintCell(
            price=Decimal(low),
            buy_volume=Decimal(buy),
            sell_volume=Decimal(0),
            delta=Decimal(buy),
        ),
        FootprintCell(
            price=Decimal(high),
            buy_volume=Decimal(0),
            sell_volume=Decimal(sell),
            delta=-Decimal(sell),
        ),
    )
    return FootprintClosedPayload(
        maturity_state=MaturityState.CLOSED,
        exchange=_EXCHANGE,
        market_type=MarketType.SPOT,
        symbol=_SYMBOL,
        timeframe=_TF,
        open_time=open_time,
        close_time=open_time + _TF.duration_ms,
        cells=cells,
        bar_buy_volume=Decimal(buy),
        bar_sell_volume=Decimal(sell),
        bar_delta=Decimal(buy) - Decimal(sell),
        trade_count=2,
        is_complete=True,
    )


def _vela(
    indice: int, *, low: str, high: str, apertura: str, cierre: str
) -> CandleClosedPayload:
    open_time = _open_time(indice)
    return CandleClosedPayload(
        maturity_state=MaturityState.CLOSED,
        exchange=_EXCHANGE,
        market_type=MarketType.SPOT,
        symbol=_SYMBOL,
        timeframe=_TF,
        open_time=open_time,
        close_time=open_time + _TF.duration_ms,
        open=Decimal(apertura),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(cierre),
        volume=Decimal("12.5"),
    )


# DOS barras excepcionales, en sitios DISTINTOS y a proposito.
#
# La ULTIMA lleva absorcion de VENDEDORES (BID): ratio altisimo (1000/10), agresion
# vendedora (delta<0) y precio CONTENIDO que SUBE (displacement>0).
#
# La barra _CLIMAX_BAR lleva climax de TECHO: volumen y rango excepcionales con el
# cierre
# en el tercio INFERIOR del rango.
#
# NO pueden ser la misma barra, y eso es geometria del detector, no comodidad del test:
# el climax de TECHO exige que el cierre caiga abajo del rango, mientras que la
# absorcion
# de VENDEDORES exige que el precio SUBA dentro de la vela (delta y displacement en
# direcciones opuestas). Un cierre no puede estar arriba y abajo a la vez.
_ULTIMA = _BARRAS - 1
_CLIMAX_BAR = 120


def _sembrar(
    persistir_footprint: PersistirFootprint, persistir_vela: PersistirVela
) -> None:
    for indice in range(_BARRAS):
        buy, sell = ("5", "5")
        low, high = ("100", "110")
        apertura, cierre = ("105", "105")
        if indice == _ULTIMA:  # absorcion BID: ratio alto, delta<0, precio sube poco
            buy, sell = ("100", "900")
            apertura, cierre = ("104", "105")
        elif indice == _CLIMAX_BAR:  # climax TOP: volumen y rango altos, cierre abajo
            buy, sell = ("5000", "5000")
            low, high = ("50", "200")
            apertura, cierre = ("190", "60")
        assert persistir_footprint(
            _footprint(indice, buy=buy, sell=sell, low=low, high=high),
            MarketFootprintEventType.FOOTPRINT_CLOSED,
            _OPEN + indice,
        )
        assert persistir_vela(
            _vela(indice, low=low, high=high, apertura=apertura, cierre=cierre),
            MarketCandleEventType.CANDLE_CLOSED,
            _OPEN + indice,
        )


@pytest.fixture
def limpiar_detectores(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """Las tablas de mercado sin FK a tenant, que se limpian a mano.

    market_orderbook_snapshot entra en P08c-CONF-05: sin borrarla, el frontier sembrado
    por un test se filtraria al siguiente y el caso "sin libro" (OBS-1) dejaria de
    probar lo que dice -- pasaria en verde leyendo el libro de otro test.
    """

    def _wipe() -> None:
        with migrator_db.transaction() as session:
            session.execute("DELETE FROM market_candle")
            session.execute("DELETE FROM market_footprint")
            session.execute("DELETE FROM market_orderbook_snapshot")
            session.execute("DELETE FROM outbox")

    _wipe()
    yield
    _wipe()


def _materializar(
    rules_db: PsycopgDatabase,
    source_id: str,
    open_time: int,
    history_bars: int,
) -> tuple[ScalarValue, ...]:
    """La fuente con el spec REAL del registro, con el rol de reglas."""
    spec = SOURCE_MATERIALIZERS[source_id]
    with rules_db.transaction() as session:
        return spec.materialize(
            session, _EXCHANGE, _SYMBOL, _TF.value, open_time, history_bars
        )


class TestSerieServida:
    def test_la_barra_con_absorcion_bid_publica_fuerza_y_ask_publica_cero(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: PersistirFootprint,
        persistir_vela: PersistirVela,
        limpiar_detectores: None,
    ) -> None:
        _sembrar(persistir_footprint, persistir_vela)

        bid = _materializar(
            rules_db, ABSORPTION_BID_STRENGTH_SOURCE_ID, _open_time(_ULTIMA), 1
        )
        ask = _materializar(
            rules_db, ABSORPTION_ASK_STRENGTH_SOURCE_ID, _open_time(_ULTIMA), 1
        )

        assert len(bid) == len(ask) == 1
        assert bid[0].scalar_type is ScalarType.DECIMAL
        assert bid[0].decimal_value is not None
        assert bid[0].decimal_value > 0
        assert ask[0].decimal_value == Decimal(0)

    def test_las_barras_planas_publican_cero_en_los_dos_lados(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: PersistirFootprint,
        persistir_vela: PersistirVela,
        limpiar_detectores: None,
    ) -> None:
        # Sin absorcion la respuesta es 0, un HECHO servido, no un hueco: es lo que
        # permite escribir "absorption.bid_strength > 0.5" sin tratar el silencio.
        _sembrar(persistir_footprint, persistir_vela)

        for source_id in _ABSORPTION:
            serie = _materializar(rules_db, source_id, _open_time(_ULTIMA - 1), 3)
            assert len(serie) == 3
            assert all(valor.decimal_value == Decimal(0) for valor in serie)

    def test_el_warm_up_recorta_la_serie_y_no_la_inventa(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: PersistirFootprint,
        persistir_vela: PersistirVela,
        limpiar_detectores: None,
    ) -> None:
        # La ventana es 100: en la barra 50 no hay ninguna sub-ventana completa, asi que
        # la serie sale VACIA (NOT_EVALUABLE, K3). Nunca se rellena con ceros.
        _sembrar(persistir_footprint, persistir_vela)

        serie = _materializar(
            rules_db, ABSORPTION_BID_STRENGTH_SOURCE_ID, _open_time(50), 5
        )
        assert serie == ()

    def test_sin_datos_no_inventa_serie(
        self,
        rules_db: PsycopgDatabase,
        limpiar_detectores: None,
    ) -> None:
        assert (
            _materializar(rules_db, ABSORPTION_BID_STRENGTH_SOURCE_ID, _open_time(0), 5)
            == ()
        )


class TestDeterminismo:
    def test_materializar_dos_veces_da_la_misma_serie_bit_a_bit(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: PersistirFootprint,
        persistir_vela: PersistirVela,
        limpiar_detectores: None,
    ) -> None:
        # ADR-007: la fuente es WINDOWED y sin estado, asi que dos materializaciones de
        # la MISMA barra tienen que coincidir digito a digito, no solo en valor.
        _sembrar(persistir_footprint, persistir_vela)

        for source_id in _TODAS:
            una = _materializar(rules_db, source_id, _open_time(_ULTIMA), 5)
            otra = _materializar(rules_db, source_id, _open_time(_ULTIMA), 5)
            assert una == otra
            # Bit a bit y para los DOS tipos: str(ScalarValue) incluye el campo tipado,
            # asi que cubre tanto las nueve DECIMAL como notrade.state (STRING).
            assert [str(v) for v in una] == [str(v) for v in otra]

    def test_la_cola_no_depende_de_cuantas_barras_se_pidan(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: PersistirFootprint,
        persistir_vela: PersistirVela,
        limpiar_detectores: None,
    ) -> None:
        # Pedir 3 o 7 barras tiene que dar la MISMA cola: si el tamano de la peticion
        # cambiara los valores, la ventana rodante estaria mal recortada.
        _sembrar(persistir_footprint, persistir_vela)

        corta = _materializar(
            rules_db, ABSORPTION_BID_STRENGTH_SOURCE_ID, _open_time(_ULTIMA), 3
        )
        larga = _materializar(
            rules_db, ABSORPTION_BID_STRENGTH_SOURCE_ID, _open_time(_ULTIMA), 7
        )

        assert len(corta) == 3
        assert len(larga) == 7
        assert larga[-3:] == corta


class TestAlineacionFailLoud:
    """Lo que SOLO se puede probar con las dos tablas: la alineacion se comprueba.

    footprint y vela viven en tablas distintas que escriben caminos distintos. Si una
    tiene una barra que la otra no, emparejar por POSICION cruzaria el delta de una
    barra con el OHLC de otra -- un fallo MUDO que ningun test de forma cazaria. Por eso
    el materializador lo comprueba y ROMPE.
    """

    def test_un_footprint_sin_su_vela_rompe_ruidoso(
        self,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        persistir_footprint: PersistirFootprint,
        persistir_vela: PersistirVela,
        limpiar_detectores: None,
    ) -> None:
        _sembrar(persistir_footprint, persistir_vela)
        # Se borra UNA vela intermedia: el footprint de esa barra se queda huerfano.
        with migrator_db.transaction() as session:
            session.execute(
                "DELETE FROM market_candle WHERE open_time = %s",
                (_open_time(_ULTIMA - 10),),
            )

        with pytest.raises(UnwiredSourceError, match="fail-loud"):
            _materializar(
                rules_db, ABSORPTION_BID_STRENGTH_SOURCE_ID, _open_time(_ULTIMA), 3
            )

    def test_una_vela_sin_su_footprint_rompe_ruidoso(
        self,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        persistir_footprint: PersistirFootprint,
        persistir_vela: PersistirVela,
        limpiar_detectores: None,
    ) -> None:
        _sembrar(persistir_footprint, persistir_vela)
        with migrator_db.transaction() as session:
            session.execute(
                "DELETE FROM market_footprint WHERE open_time = %s",
                (_open_time(_ULTIMA - 10),),
            )

        with pytest.raises(UnwiredSourceError, match="fail-loud"):
            _materializar(
                rules_db, ABSORPTION_BID_STRENGTH_SOURCE_ID, _open_time(_ULTIMA), 3
            )


class TestClimaxServido:
    def test_la_barra_excepcional_publica_climax_top_y_bottom_cero(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: PersistirFootprint,
        persistir_vela: PersistirVela,
        limpiar_detectores: None,
    ) -> None:
        _sembrar(persistir_footprint, persistir_vela)

        top = _materializar(
            rules_db, CLIMAX_TOP_STRENGTH_SOURCE_ID, _open_time(_CLIMAX_BAR), 1
        )
        bottom = _materializar(
            rules_db, CLIMAX_BOTTOM_STRENGTH_SOURCE_ID, _open_time(_CLIMAX_BAR), 1
        )

        assert top[0].decimal_value is not None
        assert top[0].decimal_value > 0
        assert bottom[0].decimal_value == Decimal(0)

    def test_las_barras_planas_publican_cero_en_los_dos_lados(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: PersistirFootprint,
        persistir_vela: PersistirVela,
        limpiar_detectores: None,
    ) -> None:
        _sembrar(persistir_footprint, persistir_vela)

        for source_id in _CLIMAX:
            serie = _materializar(rules_db, source_id, _open_time(_ULTIMA - 1), 3)
            assert len(serie) == 3
            assert all(valor.decimal_value == Decimal(0) for valor in serie)

    def test_cada_detector_dispara_en_SU_barra_y_no_en_la_del_otro(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: PersistirFootprint,
        persistir_vela: PersistirVela,
        limpiar_detectores: None,
    ) -> None:
        # absorption y climax leen la MISMA ventana compuesta pero miran cosas
        # distintas. Cada uno tiene su barra: si alguno estuviera leyendo la cara
        # equivocada (candle.volume en vez del agresor, o el rango de la vela en vez del
        # span del footprint), dispararia donde no toca o no dispararia donde toca.
        _sembrar(persistir_footprint, persistir_vela)

        bid_en_absorcion = _materializar(
            rules_db, ABSORPTION_BID_STRENGTH_SOURCE_ID, _open_time(_ULTIMA), 1
        )
        top_en_climax = _materializar(
            rules_db, CLIMAX_TOP_STRENGTH_SOURCE_ID, _open_time(_CLIMAX_BAR), 1
        )
        bid_en_climax = _materializar(
            rules_db, ABSORPTION_BID_STRENGTH_SOURCE_ID, _open_time(_CLIMAX_BAR), 1
        )
        top_en_absorcion = _materializar(
            rules_db, CLIMAX_TOP_STRENGTH_SOURCE_ID, _open_time(_ULTIMA), 1
        )

        assert bid_en_absorcion[0].decimal_value is not None
        assert top_en_climax[0].decimal_value is not None
        assert bid_en_absorcion[0].decimal_value > 0
        assert top_en_climax[0].decimal_value > 0
        # Y ninguno se cuela en la barra del otro.
        assert bid_en_climax[0].decimal_value == Decimal(0)
        assert top_en_absorcion[0].decimal_value == Decimal(0)


class TestVoidServido:
    def test_las_dos_salidas_son_indicadoras_cero_o_uno(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: PersistirFootprint,
        persistir_vela: PersistirVela,
        limpiar_detectores: None,
    ) -> None:
        _sembrar(persistir_footprint, persistir_vela)

        for source_id in _VOID:
            serie = _materializar(rules_db, source_id, _open_time(_ULTIMA), 5)
            assert len(serie) == 5
            for valor in serie:
                assert valor.scalar_type is ScalarType.DECIMAL
                assert valor.decimal_value in {Decimal(0), Decimal(1)}

    def test_no_dispara_sin_cruces_del_nivel(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: PersistirFootprint,
        persistir_vela: PersistirVela,
        limpiar_detectores: None,
    ) -> None:
        # El fixture tiene cierres constantes salvo en las dos barras excepcionales:
        # nadie cruza el LVN y vuelve, asi que no hay snap en la cola plana.
        _sembrar(persistir_footprint, persistir_vela)

        for source_id in _VOID:
            serie = _materializar(rules_db, source_id, _open_time(_ULTIMA - 2), 3)
            assert all(valor.decimal_value == Decimal(0) for valor in serie)

    def test_el_nivel_lvn_se_computa_sin_pasar_por_dispatch(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: PersistirFootprint,
        persistir_vela: PersistirVela,
        limpiar_detectores: None,
    ) -> None:
        # vp.lvn es NON_SERVIBLE: si el materializador intentase pedirla por source_id,
        # el compilador la rechazaria. Que la serie salga demuestra que el nivel se
        # deriva DENTRO del spec (patron MACD).
        _sembrar(persistir_footprint, persistir_vela)

        serie = _materializar(
            rules_db, VOID_SNAP_BULLISH_SOURCE_ID, _open_time(_ULTIMA), 1
        )

        assert len(serie) == 1
        assert serie[0].decimal_value is not None


class TestNoTradeServido:
    def test_las_tres_cifras_salen_en_rango(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: PersistirFootprint,
        persistir_vela: PersistirVela,
        limpiar_detectores: None,
    ) -> None:
        _sembrar(persistir_footprint, persistir_vela)
        topes = {
            NOTRADE_SCORE_SOURCE_ID: Decimal(65),
            NOTRADE_FOOTPRINT_INEFF_SOURCE_ID: Decimal(40),
            NOTRADE_FLOW_DISLOCATION_SOURCE_ID: Decimal(25),
        }

        for source_id, tope in topes.items():
            serie = _materializar(rules_db, source_id, _open_time(_ULTIMA), 3)
            assert len(serie) == 3
            for valor in serie:
                assert valor.scalar_type is ScalarType.DECIMAL
                assert valor.decimal_value is not None
                assert Decimal(0) <= valor.decimal_value <= tope

    def test_state_sirve_un_token_string_por_el_mismo_borde(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: PersistirFootprint,
        persistir_vela: PersistirVela,
        limpiar_detectores: None,
    ) -> None:
        # La UNICA salida STRING de la familia, servida por el MISMO carrier y el MISMO
        # spec WINDOWED que las nueve DECIMAL. Es lo que habilito materialize_windowed
        # generica en la salida (enmienda P08c-DET-01).
        _sembrar(persistir_footprint, persistir_vela)

        serie = _materializar(rules_db, NOTRADE_STATE_SOURCE_ID, _open_time(_ULTIMA), 4)

        assert len(serie) == 4
        for valor in serie:
            assert valor.scalar_type is ScalarType.STRING
            assert valor.decimal_value is None
            assert valor.string_value in {estado.value for estado in NoTradeState}

    def test_el_score_cuadra_con_la_suma_de_sus_bloques(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: PersistirFootprint,
        persistir_vela: PersistirVela,
        limpiar_detectores: None,
    ) -> None:
        # Las tres cifras se materializan por separado (dispatch por SOURCE_ID) pero
        # salen del MISMO recorrido: si alguna leyera otra ventana, la suma no
        # cuadraria.
        _sembrar(persistir_footprint, persistir_vela)
        ultimo = _open_time(_ULTIMA)

        score = _materializar(rules_db, NOTRADE_SCORE_SOURCE_ID, ultimo, 3)
        fp = _materializar(rules_db, NOTRADE_FOOTPRINT_INEFF_SOURCE_ID, ultimo, 3)
        flow = _materializar(rules_db, NOTRADE_FLOW_DISLOCATION_SOURCE_ID, ultimo, 3)

        for total, bloque_fp, bloque_flow in zip(score, fp, flow, strict=True):
            assert total.decimal_value is not None
            assert bloque_fp.decimal_value is not None
            assert bloque_flow.decimal_value is not None
            assert (
                total.decimal_value
                == bloque_fp.decimal_value + bloque_flow.decimal_value
            )


# --- imbalance.* : POINT_LOCAL sobre celdas (P08c-CONF-05) ----------------------------

# Barra con TRES niveles y una pila COMPRADORA de 3: cada nivel tiene buy >= 3 * el sell
# del nivel de abajo, asi que la corrida llega a MIN_STACK y la fuerza satura a 1.
_PILA_BUY = ((100, 1, 1), (101, 30, 1), (102, 30, 1), (103, 30, 1))


def _footprint_con_pila(indice: int) -> FootprintClosedPayload:
    """Footprint de cuatro niveles con una pila compradora conocida."""
    open_time = _open_time(indice)
    cells = tuple(
        FootprintCell(
            price=Decimal(precio),
            buy_volume=Decimal(buy),
            sell_volume=Decimal(sell),
            delta=Decimal(buy) - Decimal(sell),
        )
        for precio, buy, sell in _PILA_BUY
    )
    total_buy = sum((c.buy_volume for c in cells), Decimal(0))
    total_sell = sum((c.sell_volume for c in cells), Decimal(0))
    return FootprintClosedPayload(
        maturity_state=MaturityState.CLOSED,
        exchange=_EXCHANGE,
        market_type=MarketType.SPOT,
        symbol=_SYMBOL,
        timeframe=_TF,
        open_time=open_time,
        close_time=open_time + _TF.duration_ms,
        cells=cells,
        bar_buy_volume=total_buy,
        bar_sell_volume=total_sell,
        bar_delta=total_buy - total_sell,
        trade_count=len(cells) * 2,
        is_complete=True,
    )


class TestImbalanceMaterializado:
    """imbalance.* servido desde PostgreSQL real, con el rol de reglas.

    Es POINT_LOCAL y solo consume market.footprint, asi que -- a diferencia de los
    cuatro detectores -- NO necesita la tabla de velas ni la comprobacion de alineacion:
    aqui se siembra solo el footprint, y que eso baste es parte de lo que se prueba.
    """

    def test_sirve_un_valor_por_barra_sin_warm_up(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: PersistirFootprint,
        limpiar_detectores: None,
    ) -> None:
        # POINT_LOCAL: pedir 5 barras devuelve 5 valores desde la PRIMERA. Si se hubiera
        # declarado WINDOWED(100), las primeras 99 saldrian vacias -- este es el candado
        # de esa decision (DICTAMEN P08c-CONF-05) visto desde la BD.
        for indice in range(5):
            persistir_footprint(
                _footprint_con_pila(indice),
                MarketFootprintEventType.FOOTPRINT_CLOSED,
                indice,
            )
        serie = _materializar(rules_db, IMBALANCE_BUY_STACK_SOURCE_ID, _open_time(4), 5)
        assert len(serie) == 5

    def test_el_valor_servido_es_el_del_nucleo_puro(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: PersistirFootprint,
        limpiar_detectores: None,
    ) -> None:
        # La BD no puede cambiar el veredicto: lo que sale por el materializador tiene
        # que ser exactamente lo que da detect_stacked_imbalance sobre esas celdas.
        for indice in range(3):
            persistir_footprint(
                _footprint_con_pila(indice),
                MarketFootprintEventType.FOOTPRINT_CLOSED,
                indice,
            )
        esperado_buy, esperado_sell = detect_stacked_imbalance(
            _footprint_con_pila(2).cells
        )
        assert esperado_buy == Decimal(1)  # la pila de 3 satura
        assert esperado_sell == Decimal(0)

        buy = _materializar(rules_db, IMBALANCE_BUY_STACK_SOURCE_ID, _open_time(2), 1)
        sell = _materializar(rules_db, IMBALANCE_SELL_STACK_SOURCE_ID, _open_time(2), 1)
        assert buy[0].scalar_type is ScalarType.DECIMAL
        assert buy[0].decimal_value == esperado_buy
        assert sell[0].decimal_value == esperado_sell

    def test_es_determinista_bit_a_bit(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: PersistirFootprint,
        limpiar_detectores: None,
    ) -> None:
        # ADR-007: dos materializaciones de la misma barra, digito a digito.
        for indice in range(3):
            persistir_footprint(
                _footprint_con_pila(indice),
                MarketFootprintEventType.FOOTPRINT_CLOSED,
                indice,
            )
        for source_id in (
            IMBALANCE_BUY_STACK_SOURCE_ID,
            IMBALANCE_SELL_STACK_SOURCE_ID,
        ):
            una = _materializar(rules_db, source_id, _open_time(2), 3)
            otra = _materializar(rules_db, source_id, _open_time(2), 3)
            assert una == otra
            assert [str(v) for v in una] == [str(v) for v in otra]


# --- Bloque L2 de notrade: el frontier del libro (P08c-CONF-05, grant 0029) -----------

_CADENCIA_MS = 1000
_FORMULA_LIBRO = 1


def _frontier(indice: int, *, bid: str, ask: str) -> OrderbookSnapshotPayload:
    """Snapshot FRONTIER de una barra: un nivel por lado, tamanos parametrizados."""
    open_time = _open_time(indice)
    return OrderbookSnapshotPayload(
        exchange=_EXCHANGE,
        market_type=MarketType.SPOT,
        symbol=_SYMBOL,
        depth_k=1,
        bids=(OrderbookLevel(price=Decimal("99"), size=Decimal(bid)),),
        asks=(OrderbookLevel(price=Decimal("101"), size=Decimal(ask)),),
        sequence=indice,
        kind=MarketOrderbookSnapshotKind.FRONTIER,
        timeframe=_TF,
        open_time=open_time,
        close_time=open_time + _TF.duration_ms,
        is_complete=True,
        cadence_ms=_CADENCIA_MS,
        formula_version=_FORMULA_LIBRO,
    )


@pytest.fixture
def persistir_frontier(
    ingestion_db: PsycopgDatabase,
) -> Callable[[OrderbookSnapshotPayload], bool]:
    """Escribe un frontier por el camino REAL (historico+outbox atomico, INGESTA).

    Mismo criterio que persistir_footprint: que el dato lo siembre el
    PostgresOrderbookWriter de produccion con el rol de INGESTA, no un INSERT de juguete
    que taparia una discrepancia entre escritor y lector -- que es justo lo que este
    fichero existe para cazar, ahora sobre una TERCERA tabla.
    """
    writer = PostgresOrderbookWriter(ingestion_db)
    tipo = MarketOrderbookEventType.ORDERBOOK_FRONTIER

    def _persistir(payload: OrderbookSnapshotPayload) -> bool:
        clave = payload.idempotency_key(payload.kind)
        envelope = Envelope[OrderbookSnapshotPayload](
            event_type=tipo.value,
            event_schema_version=expected_event_schema_version(tipo.value),
            source="worker_ingestion",
            idempotency_key=clave,
            stream_key=payload.stream_key(),
            scope=Scope.PUBLIC_MARKET,
            event_time=payload.close_time,
            ingestion_time=payload.close_time,
            processing_time=payload.close_time,
            correlation_id=payload.stream_key(),
            payload=payload,
        )
        return writer.persist_and_enqueue(
            envelope_json=envelope.model_dump_json().encode(),
            payload=payload,
            event_type=tipo.value,
            stream_key=payload.stream_key(),
            idempotency_key=clave,
            event_time=payload.close_time,
        )

    return _persistir


class TestBloqueL2Materializado:
    """notrade.* leyendo el LIBRO desde PostgreSQL con el rol de reglas (grant 0029).

    Es lo unico que no se puede probar sin BD: que el rol de reglas TIENE el SELECT que
    la 0029 abrio, que el lector filtra a kind='frontier' y que el emparejamiento por
    open_time aguanta que falten barras.
    """

    def test_el_rol_de_reglas_puede_leer_el_libro_y_el_bloque_l2_aporta(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: PersistirFootprint,
        persistir_vela: PersistirVela,
        persistir_frontier: Callable[[OrderbookSnapshotPayload], bool],
        limpiar_detectores: None,
    ) -> None:
        # Sin el grant de la 0029 esto no seria un fallo de asercion sino un error de
        # permisos de PostgreSQL, que es exactamente como tiene que salir.
        _sembrar(persistir_footprint, persistir_vela)
        for indice in range(_BARRAS):
            # Libro cada vez mas desequilibrado: la ultima barra es la mas toxica.
            persistir_frontier(_frontier(indice, bid=str(100 + indice * 10), ask="10"))
        score = _materializar(rules_db, NOTRADE_SCORE_SOURCE_ID, _open_time(_ULTIMA), 1)
        assert score[0].decimal_value is not None
        assert score[0].decimal_value > Decimal(0)

    def test_sin_frontier_el_score_no_pasa_de_65(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: PersistirFootprint,
        persistir_vela: PersistirVela,
        limpiar_detectores: None,
    ) -> None:
        # OBS-1 de punta a punta: se siembra footprint y vela pero NINGUN frontier. El
        # lector devuelve la tupla vacia, el emparejamiento deja book=None en todas las
        # barras y el bloque L2 aporta 0 -- sin reventar y sin inventar toxicidad.
        _sembrar(persistir_footprint, persistir_vela)
        score = _materializar(rules_db, NOTRADE_SCORE_SOURCE_ID, _open_time(_ULTIMA), 1)
        assert score[0].decimal_value is not None
        assert Decimal(0) <= score[0].decimal_value <= Decimal(65)

    def test_es_determinista_bit_a_bit_con_el_libro(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: PersistirFootprint,
        persistir_vela: PersistirVela,
        persistir_frontier: Callable[[OrderbookSnapshotPayload], bool],
        limpiar_detectores: None,
    ) -> None:
        # ADR-007 sobre las TRES tablas a la vez.
        _sembrar(persistir_footprint, persistir_vela)
        for indice in range(_BARRAS):
            persistir_frontier(_frontier(indice, bid="500", ask="300"))
        una = _materializar(rules_db, NOTRADE_SCORE_SOURCE_ID, _open_time(_ULTIMA), 3)
        otra = _materializar(rules_db, NOTRADE_SCORE_SOURCE_ID, _open_time(_ULTIMA), 3)
        assert una == otra
        assert [str(v) for v in una] == [str(v) for v in otra]
