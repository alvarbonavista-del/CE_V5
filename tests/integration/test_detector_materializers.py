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
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.platform.rules.absorption import (
    ABSORPTION_ASK_STRENGTH_SOURCE_ID,
    ABSORPTION_BID_STRENGTH_SOURCE_ID,
)
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

_TODAS = (ABSORPTION_BID_STRENGTH_SOURCE_ID, ABSORPTION_ASK_STRENGTH_SOURCE_ID)

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


# La ULTIMA barra lleva absorcion de VENDEDORES (BID): ratio altisimo (1000/10),
# agresion vendedora (delta<0) y precio CONTENIDO que sube (displacement>0). Las 129
# previas son planas (ratio 1), asi que el umbral adaptativo se queda en su piso.
_ULTIMA = _BARRAS - 1


def _sembrar(
    persistir_footprint: PersistirFootprint, persistir_vela: PersistirVela
) -> None:
    for indice in range(_BARRAS):
        con_absorcion = indice == _ULTIMA
        buy, sell = ("100", "900") if con_absorcion else ("5", "5")
        apertura, cierre = ("104", "105") if con_absorcion else ("105", "105")
        assert persistir_footprint(
            _footprint(indice, buy=buy, sell=sell, low="100", high="110"),
            MarketFootprintEventType.FOOTPRINT_CLOSED,
            _OPEN + indice,
        )
        assert persistir_vela(
            _vela(indice, low="100", high="110", apertura=apertura, cierre=cierre),
            MarketCandleEventType.CANDLE_CLOSED,
            _OPEN + indice,
        )


@pytest.fixture
def limpiar_detectores(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """market_candle/market_footprint/outbox: sin FK a tenant, se limpian a mano."""

    def _wipe() -> None:
        with migrator_db.transaction() as session:
            session.execute("DELETE FROM market_candle")
            session.execute("DELETE FROM market_footprint")
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

        for source_id in _TODAS:
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
            assert [str(v.decimal_value) for v in una] == [
                str(v.decimal_value) for v in otra
            ]

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
