"""Tests de orderflow determinista: declaraciones ADR-008 + delta_momentum (P08c).

Dos bloques. (1) DECLARACIONES: forma ADR-008 de orderflow.delta (POINT_LOCAL) y
orderflow.delta_momentum (WINDOWED), y el grafo consumes (footprint -> delta ->
delta_momentum: completo valida; roto -> fail-loud). (2) COMPUTE: verificacion NUMERICA
de delta_momentum = delta[T] - delta[T-1] (paridad v4), calculada a mano.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from ce_v5.platform.rules.catalog import DataSourceCatalog, MissingDependencyError
from ce_v5.platform.rules.orderflow import (
    ORDERFLOW_DELTA_MOMENTUM_SOURCE_ID,
    ORDERFLOW_DELTA_SOURCE_ID,
    compute_delta_momentum,
    orderflow_delta_declaration,
    orderflow_delta_momentum_declaration,
)
from ce_v5.platform.rules.rawfootprint import (
    MARKET_FOOTPRINT_SOURCE_ID,
    market_footprint_declaration,
)
from source.datasource import MemoryModel, Servibility, SourceType
from source.rules.scalar import ScalarType


class TestDeclaraciones:
    def test_delta_es_point_local_derivado_de_footprint(self) -> None:
        declaration = orderflow_delta_declaration()
        assert declaration.source_id == ORDERFLOW_DELTA_SOURCE_ID
        assert declaration.source_type is SourceType.OBSERVABLE
        assert declaration.servibility is Servibility.CONTINUOUS
        assert declaration.memory_model is MemoryModel.POINT_LOCAL
        assert declaration.value_type is ScalarType.DECIMAL
        assert declaration.consumes == (MARKET_FOOTPRINT_SOURCE_ID,)

    def test_delta_momentum_es_windowed_derivado_de_delta(self) -> None:
        declaration = orderflow_delta_momentum_declaration()
        assert declaration.source_id == ORDERFLOW_DELTA_MOMENTUM_SOURCE_ID
        assert declaration.servibility is Servibility.CONTINUOUS
        assert declaration.memory_model is MemoryModel.WINDOWED
        assert declaration.value_type is ScalarType.DECIMAL
        assert declaration.consumes == (ORDERFLOW_DELTA_SOURCE_ID,)

    def test_dag_completo_valida_footprint_delta_momentum(self) -> None:
        catalog = DataSourceCatalog()
        catalog.register(market_footprint_declaration())
        catalog.register(orderflow_delta_declaration())
        catalog.register(orderflow_delta_momentum_declaration())
        catalog.validate()  # cadena completa y aciclica.

    def test_delta_sin_footprint_falla_el_dag(self) -> None:
        catalog = DataSourceCatalog()
        catalog.register(orderflow_delta_declaration())
        with pytest.raises(MissingDependencyError):
            catalog.validate()

    def test_delta_momentum_sin_delta_falla_el_dag(self) -> None:
        # delta_momentum consume orderflow.delta: sin registrarlo, el grafo esta roto.
        catalog = DataSourceCatalog()
        catalog.register(market_footprint_declaration())
        catalog.register(orderflow_delta_momentum_declaration())
        with pytest.raises(MissingDependencyError):
            catalog.validate()


class TestDeltaMomentum:
    def test_momentum_es_la_diferencia_barra_a_barra(self) -> None:
        # delta 10 -> 13 -> 8 : momentum 0 (primera), +3, -5. Misma longitud.
        deltas = [Decimal("10"), Decimal("13"), Decimal("8")]
        assert compute_delta_momentum(deltas) == (
            Decimal("0"),
            Decimal("3"),
            Decimal("-5"),
        )

    def test_una_sola_barra_da_momento_cero(self) -> None:
        assert compute_delta_momentum([Decimal("42")]) == (Decimal("0"),)

    def test_ventana_vacia_da_tupla_vacia(self) -> None:
        assert compute_delta_momentum([]) == ()

    def test_conserva_signo_y_negativos(self) -> None:
        deltas = [Decimal("-5"), Decimal("-2"), Decimal("-9")]
        assert compute_delta_momentum(deltas) == (
            Decimal("0"),
            Decimal("3"),
            Decimal("-7"),
        )
