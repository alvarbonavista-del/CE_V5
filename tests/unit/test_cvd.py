"""Tests de CVD: declaracion ADR-008 (INTEGRATOR) + acumulado con reset_policy (P08c).

Dos bloques. (1) DECLARACION: forma ADR-008, INTEGRATOR, reset_policy como parametro
(default rolling) en la cache_key, y el grafo consumes (footprint -> delta -> cvd:
completo valida; sin delta -> fail-loud). (2) COMPUTE: acumulado verificado a mano;
rolling acumula sin reset e ignora la frontera de dia; session_utc resetea en medianoche
UTC (open_time // 86_400_000).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from ce_v5.platform.rules.catalog import DataSourceCatalog, MissingDependencyError
from ce_v5.platform.rules.cvd import (
    CVD_SOURCE_ID,
    ResetPolicy,
    compute_cvd,
    cvd_declaration,
)
from ce_v5.platform.rules.orderflow import (
    ORDERFLOW_DELTA_SOURCE_ID,
    orderflow_delta_declaration,
)
from ce_v5.platform.rules.rawfootprint import market_footprint_declaration
from source.datasource import MemoryModel, Servibility, SourceType
from source.rules.scalar import ScalarType

_DAY = 86_400_000  # ms en un dia UTC (igual que el _MS_PER_DAY del modulo).


class TestDeclaracion:
    def test_cvd_es_integrator_derivado_de_delta(self) -> None:
        declaration = cvd_declaration()
        assert declaration.source_id == CVD_SOURCE_ID
        assert declaration.source_type is SourceType.OBSERVABLE
        assert declaration.servibility is Servibility.CONTINUOUS
        assert declaration.memory_model is MemoryModel.INTEGRATOR
        assert declaration.value_type is ScalarType.DECIMAL
        assert declaration.consumes == (ORDERFLOW_DELTA_SOURCE_ID,)

    def test_reset_policy_es_parametro_default_rolling_en_cache_key(self) -> None:
        declaration = cvd_declaration()
        params = {param.name: param for param in declaration.params}
        assert set(params) == {"reset_policy"}
        reset_policy = params["reset_policy"]
        assert reset_policy.value_type is ScalarType.STRING
        assert reset_policy.default is not None
        assert reset_policy.default.string_value == "rolling"
        assert "reset_policy" in declaration.cache_key_schema

    def test_dag_completo_valida_footprint_delta_cvd(self) -> None:
        catalog = DataSourceCatalog()
        catalog.register(market_footprint_declaration())
        catalog.register(orderflow_delta_declaration())
        catalog.register(cvd_declaration())
        catalog.validate()  # cadena completa y aciclica.

    def test_cvd_sin_delta_falla_el_dag(self) -> None:
        # cvd consume orderflow.delta: sin registrarlo, el grafo esta roto (fail-loud).
        catalog = DataSourceCatalog()
        catalog.register(market_footprint_declaration())
        catalog.register(cvd_declaration())
        with pytest.raises(MissingDependencyError):
            catalog.validate()


class TestComputeCvd:
    def test_rolling_acumula_sin_reset(self) -> None:
        bars = [(1000, Decimal("5")), (2000, Decimal("-3")), (3000, Decimal("10"))]
        assert compute_cvd(bars, reset_policy=ResetPolicy.ROLLING) == (
            Decimal("5"),
            Decimal("2"),
            Decimal("12"),
        )

    def test_rolling_es_el_default(self) -> None:
        bars = [(1000, Decimal("5")), (2000, Decimal("-3"))]
        assert compute_cvd(bars) == (Decimal("5"), Decimal("2"))

    def test_rolling_ignora_la_frontera_de_dia(self) -> None:
        # Cruza medianoche UTC (dia 10 -> dia 11): rolling sigue acumulando.
        bars = [
            (10 * _DAY + 1000, Decimal("5")),
            (10 * _DAY + 2000, Decimal("3")),
            (11 * _DAY + 500, Decimal("4")),
        ]
        assert compute_cvd(bars, reset_policy=ResetPolicy.ROLLING) == (
            Decimal("5"),
            Decimal("8"),
            Decimal("12"),
        )

    def test_session_utc_resetea_en_medianoche_utc(self) -> None:
        # dia 10: 5, 8 ; al cruzar al dia 11 el acumulado vuelve a cero -> 4.
        bars = [
            (10 * _DAY + 1000, Decimal("5")),
            (10 * _DAY + 2000, Decimal("3")),
            (11 * _DAY + 500, Decimal("4")),
        ]
        assert compute_cvd(bars, reset_policy=ResetPolicy.SESSION_UTC) == (
            Decimal("5"),
            Decimal("8"),
            Decimal("4"),
        )

    def test_session_utc_sin_cruce_no_resetea(self) -> None:
        bars = [(10 * _DAY + 1000, Decimal("5")), (10 * _DAY + 2000, Decimal("3"))]
        assert compute_cvd(bars, reset_policy=ResetPolicy.SESSION_UTC) == (
            Decimal("5"),
            Decimal("8"),
        )

    def test_ventana_vacia_da_tupla_vacia(self) -> None:
        assert compute_cvd([]) == ()

    def test_una_sola_barra(self) -> None:
        assert compute_cvd([(1000, Decimal("7"))]) == (Decimal("7"),)
