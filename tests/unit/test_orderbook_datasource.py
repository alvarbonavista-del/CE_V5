"""Tests de la declaracion ADR-008 del snapshot del libro L2 (P07c, remediacion G2).

MUERDEN SOBRE LA DECLARACION, no sobre una copia del texto: hay un test POR DIMENSION
de la cache_key, de forma que quitar un campo del schema pone SU test en ROJO con el
nombre de la dimension que falta. Un cache_key_schema incompleto es exactamente el fallo
que no se ve: dos capturas que deberian ser hechos distintos compartirian clave y una
pisaria a la otra en silencio.

Ademas se comprueba que cada dimension declarada TIENE efecto real en la clave que se
persiste (orderbook_snapshot_idempotency_key): una dimension declarada que no discrimine
seria una promesa vacia.
"""

from __future__ import annotations

import pytest

from ce_v5.platform.rules.rawbook import (
    ORDERBOOK_SNAPSHOT_CACHE_KEY_SCHEMA,
    ORDERBOOK_SNAPSHOT_SOURCE_ID,
    orderbook_snapshot_declaration,
)
from source.datasource import (
    HistoryUnit,
    MemoryModel,
    Servibility,
    SharingScope,
    SourceType,
)
from source.families.market import Timeframe
from source.families.orderbook import (
    MarketOrderbookSnapshotKind,
    orderbook_snapshot_idempotency_key,
)
from source.rules.scalar import ScalarType

_DIMENSIONES = (
    "exchange",
    "symbol",
    "market_type",
    "data_family",
    "depth_k",
    "cadence_ms",
    "timeframe",
    "frontier_time_anchor",
    "formula_version",
    # DECIMA dimension, por dictamen de Central (G2/clock_source): una captura por reloj
    # real y otra por reloj simulado del MISMO as_of son hechos distintos.
    "clock_source",
)


class TestCacheKeySchema:
    """Una dimension, un test: quitarla del schema pone ese test en ROJO."""

    @pytest.mark.parametrize("dimension", _DIMENSIONES)
    def test_la_cache_key_declara_la_dimension(self, dimension: str) -> None:
        declaracion = orderbook_snapshot_declaration()
        assert dimension in declaracion.cache_key_schema, (
            f"la cache_key del snapshot no declara '{dimension}': dos capturas que "
            "difieran solo en esa dimension compartirian clave y una pisaria a la otra."
        )

    def test_el_schema_no_repite_dimensiones(self) -> None:
        schema = orderbook_snapshot_declaration().cache_key_schema
        assert len(set(schema)) == len(schema)

    def test_la_constante_y_la_declaracion_no_se_separan(self) -> None:
        # La constante publica es la MISMA que viaja en la declaracion: si alguien
        # edita una y olvida la otra, esto lo canta.
        assert (
            orderbook_snapshot_declaration().cache_key_schema
            == ORDERBOOK_SNAPSHOT_CACHE_KEY_SCHEMA
        )


class TestDeclaracionCoherenteConADR008:
    def test_es_una_fuente_observable_del_dominio_market(self) -> None:
        declaracion = orderbook_snapshot_declaration()
        assert declaracion.source_id == ORDERBOOK_SNAPSHOT_SOURCE_ID
        assert declaracion.source_type is SourceType.OBSERVABLE

    def test_no_se_sirve_como_termino_escalar(self) -> None:
        # Un snapshot top-K no es un escalar: NON_SERVIBLE es lo que dice el marco para
        # "se calcula pero no se combina en reglas". Las magnitudes escalares del libro
        # (imbalance, spread) son fuentes DERIVADAS, catalogo de I-02.
        assert orderbook_snapshot_declaration().servibility is Servibility.NON_SERVIBLE

    def test_el_libro_no_es_point_local(self) -> None:
        # CA-P08-08: el motor SOLO propaga correcciones a fuentes POINT_LOCAL. El libro
        # es con estado y order-dependiente (el de T es el de T-1 mas sus deltas):
        # declararlo point-local autorizaria una propagacion que daria un numero
        # silenciosamente incorrecto.
        assert orderbook_snapshot_declaration().memory_model is MemoryModel.RECURSIVE

    def test_las_magnitudes_del_libro_son_decimal(self) -> None:
        assert orderbook_snapshot_declaration().value_type is ScalarType.DECIMAL

    def test_se_evalua_en_los_seis_timeframes(self) -> None:
        declaracion = orderbook_snapshot_declaration()
        assert declaracion.evaluation_contexts == tuple(tf.value for tf in Timeframe)

    def test_la_historia_va_en_barras_y_en_tiempo(self) -> None:
        # BARS: el frontier es uno por barra cerrada. TIME: la muestra intra-ventana se
        # toma a cadencia, que es historia en tiempo.
        unidades = orderbook_snapshot_declaration().history_units
        assert HistoryUnit.BARS in unidades
        assert HistoryUnit.TIME in unidades

    def test_es_publica_cross_tenant_sin_tenant(self) -> None:
        # scope=public_market (ADR-011): el libro de un flujo es el mismo para todos.
        declaracion = orderbook_snapshot_declaration()
        assert declaracion.shared_evaluation
        assert declaracion.sharing_scope is SharingScope.PUBLIC_CROSS_TENANT

    def test_es_una_fuente_base_sin_insumos(self) -> None:
        assert orderbook_snapshot_declaration().consumes == ()


class TestLasDimensionesDiscriminanDeVerdad:
    """La declaracion no es un adorno: cada dimension declarada mueve la clave REAL."""

    def _clave(self, **overrides: object) -> str:
        base: dict[str, object] = {
            "kind": MarketOrderbookSnapshotKind.FRONTIER,
            "stream_key": "market:orderbook:binance:spot:BTC-USDT",
            "timeframe": Timeframe.H1,
            "open_time": 1_784_073_600_000,
            "sample_time": None,
            "depth_k": 25,
            "cadence_ms": 1000,
            "formula_version": 1,
            "clock_source": "system",
        }
        base.update(overrides)
        return orderbook_snapshot_idempotency_key(**base)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("dimension", "override"),
        [
            # exchange, symbol, market_type y data_family viajan DENTRO del stream_key.
            ("exchange", {"stream_key": "market:orderbook:okx:spot:BTC-USDT"}),
            ("symbol", {"stream_key": "market:orderbook:binance:spot:ETH-USDT"}),
            (
                "market_type",
                {"stream_key": "market:orderbook:binance:futures:BTC-USDT"},
            ),
            ("data_family", {"stream_key": "market:trade:binance:spot:BTC-USDT"}),
            ("depth_k", {"depth_k": 50}),
            ("cadence_ms", {"cadence_ms": 500}),
            ("timeframe", {"timeframe": Timeframe.M15}),
            ("frontier_time_anchor", {"open_time": 1_784_073_600_000 + 3_600_000}),
            ("formula_version", {"formula_version": 2}),
            ("clock_source", {"clock_source": "simulated"}),
        ],
    )
    def test_cambiar_la_dimension_cambia_la_clave_persistida(
        self, dimension: str, override: dict[str, object]
    ) -> None:
        assert dimension in ORDERBOOK_SNAPSHOT_CACHE_KEY_SCHEMA
        assert self._clave() != self._clave(**override), dimension
