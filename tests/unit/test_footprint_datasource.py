"""Tests de la declaracion ADR-008 de la fuente base de footprint (market.footprint).

MUERDEN SOBRE LA DECLARACION: hay un test POR DIMENSION de la cache_key, de forma que
quitar un campo del schema pone SU test en ROJO con el nombre de la dimension que falta.
Un cache_key_schema incompleto es el fallo que no se ve: dos footprints que deberian ser
hechos distintos compartirian clave y uno pisaria al otro en silencio.

Ademas se comprueba que cada dimension declarada TIENE efecto real en la clave que se
persiste (footprint_idempotency_key). Las cuatro viajan DENTRO del stream_key, asi que
se varian a nivel de stream_key, mismo patron que el test del snapshot del libro.
"""

from __future__ import annotations

import pytest

from ce_v5.platform.rules.rawfootprint import (
    MARKET_FOOTPRINT_CACHE_KEY_SCHEMA,
    MARKET_FOOTPRINT_SOURCE_ID,
    market_footprint_declaration,
)
from source.datasource import (
    HistoryUnit,
    MemoryModel,
    Servibility,
    SharingScope,
    SourceType,
)
from source.families.footprint import (
    MarketFootprintEventType,
    footprint_idempotency_key,
)
from source.families.market import Timeframe
from source.rules.scalar import ScalarType
from source.time import MaturityState

# market_type entra explicito: Binance lista el mismo par en spot y en derivados y son
# footprints distintos. En v5.0 MarketType es spot-only, asi que el override de abajo la
# varia a nivel de stream_key (cadena), como el test del libro: la clave no parsea el
# stream_key contra el enum, solo lo concatena.
_DIMENSIONES = (
    "exchange",
    "symbol",
    "market_type",
    "timeframe",
)


class TestCacheKeySchema:
    """Una dimension, un test: quitarla del schema pone ese test en ROJO."""

    @pytest.mark.parametrize("dimension", _DIMENSIONES)
    def test_la_cache_key_declara_la_dimension(self, dimension: str) -> None:
        declaracion = market_footprint_declaration()
        assert dimension in declaracion.cache_key_schema, (
            f"la cache_key del footprint no declara '{dimension}': dos footprints que "
            "difieran solo en esa dimension compartirian clave y uno pisaria al otro."
        )

    def test_el_schema_no_repite_dimensiones(self) -> None:
        schema = market_footprint_declaration().cache_key_schema
        assert len(set(schema)) == len(schema)

    def test_la_constante_y_la_declaracion_no_se_separan(self) -> None:
        assert (
            market_footprint_declaration().cache_key_schema
            == MARKET_FOOTPRINT_CACHE_KEY_SCHEMA
        )


class TestDeclaracionCoherenteConADR008:
    def test_es_una_fuente_observable_del_dominio_market(self) -> None:
        declaracion = market_footprint_declaration()
        assert declaracion.source_id == MARKET_FOOTPRINT_SOURCE_ID
        assert declaracion.source_type is SourceType.OBSERVABLE

    def test_no_se_sirve_como_termino_escalar(self) -> None:
        assert market_footprint_declaration().servibility is Servibility.NON_SERVIBLE

    def test_el_footprint_es_point_local(self) -> None:
        assert market_footprint_declaration().memory_model is MemoryModel.POINT_LOCAL

    def test_las_magnitudes_del_footprint_son_decimal(self) -> None:
        assert market_footprint_declaration().value_type is ScalarType.DECIMAL

    def test_se_evalua_en_los_seis_timeframes(self) -> None:
        declaracion = market_footprint_declaration()
        assert declaracion.evaluation_contexts == tuple(tf.value for tf in Timeframe)

    def test_la_historia_va_en_barras(self) -> None:
        assert market_footprint_declaration().history_units == (HistoryUnit.BARS,)

    def test_es_publica_cross_tenant(self) -> None:
        declaracion = market_footprint_declaration()
        assert declaracion.shared_evaluation
        assert declaracion.sharing_scope is SharingScope.PUBLIC_CROSS_TENANT

    def test_es_una_fuente_base_sin_insumos(self) -> None:
        assert market_footprint_declaration().consumes == ()


class TestLasDimensionesDiscriminanDeVerdad:
    """La declaracion no es un adorno: cada dimension declarada mueve la clave REAL."""

    def _clave(self, **overrides: object) -> str:
        base: dict[str, object] = {
            "event_type": MarketFootprintEventType.FOOTPRINT_CLOSED,
            "stream_key": "market:footprint:binance:spot:BTC-USDT:1m",
            "open_time": 1_784_073_600_000,
            "maturity_state": MaturityState.CLOSED,
            "correction_revision": None,
        }
        base.update(overrides)
        return footprint_idempotency_key(**base)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("dimension", "override"),
        [
            ("exchange", {"stream_key": "market:footprint:okx:spot:BTC-USDT:1m"}),
            ("symbol", {"stream_key": "market:footprint:binance:spot:ETH-USDT:1m"}),
            (
                "market_type",
                {"stream_key": "market:footprint:binance:futures:BTC-USDT:1m"},
            ),
            ("timeframe", {"stream_key": "market:footprint:binance:spot:BTC-USDT:5m"}),
        ],
    )
    def test_cambiar_la_dimension_cambia_la_clave_persistida(
        self, dimension: str, override: dict[str, object]
    ) -> None:
        assert dimension in MARKET_FOOTPRINT_CACHE_KEY_SCHEMA
        assert self._clave() != self._clave(**override), dimension
