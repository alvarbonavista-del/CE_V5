"""Tests del validador de cache_key por naturaleza (CE-14, MAT-03)."""

from __future__ import annotations

import pytest

from ce_v5.platform.rules.cache_key_validator import (
    CacheKeyValidationError,
    validate_cache_key,
    validate_cache_keys,
)
from ce_v5.platform.rules.discovery import discover_declarations
from source.datasource import (
    DataSourceDeclaration,
    HistoryUnit,
    MemoryModel,
    ParamSpec,
    Servibility,
    SharingScope,
    SourceType,
)
from source.rules.scalar import ScalarType


def _make(
    cache_key_schema: tuple[str, ...],
    params: tuple[ParamSpec, ...] = (),
) -> DataSourceDeclaration:
    """Declaracion minima valida con cache_key/params variables para el test."""
    return DataSourceDeclaration(
        source_id="test.source",
        source_type=SourceType.OBSERVABLE,
        servibility=Servibility.CONTINUOUS,
        memory_model=MemoryModel.POINT_LOCAL,
        value_type=ScalarType.DECIMAL,
        evaluation_contexts=("1m",),
        history_units=(HistoryUnit.BARS,),
        params=params,
        shared_evaluation=True,
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=cache_key_schema,
    )


def test_todas_las_declaraciones_vivas_pasan() -> None:
    # Aditividad (D7): el validador PASA todas las declaraciones del catalogo vivo.
    validate_cache_keys(discover_declarations())


def test_flujo_minimo_ok() -> None:
    validate_cache_key(_make(("exchange", "symbol", "timeframe")))


def test_market_type_opcional_ok() -> None:
    validate_cache_key(_make(("exchange", "symbol", "market_type", "timeframe")))


def test_param_en_clave_ok() -> None:
    validate_cache_key(
        _make(
            ("exchange", "symbol", "timeframe", "bin_count"),
            (ParamSpec(name="bin_count", value_type=ScalarType.INTEGER),),
        )
    )


def test_formula_version_y_as_of_se_aceptan() -> None:
    validate_cache_key(
        _make(("exchange", "symbol", "timeframe", "formula_version", "as_of"))
    )


def test_falta_dim_de_flujo_lanza() -> None:
    with pytest.raises(CacheKeyValidationError):
        validate_cache_key(_make(("exchange", "symbol")))


def test_param_fuera_de_clave_lanza() -> None:
    with pytest.raises(CacheKeyValidationError):
        validate_cache_key(
            _make(
                ("exchange", "symbol", "timeframe"),
                (ParamSpec(name="bin_count", value_type=ScalarType.INTEGER),),
            )
        )


def test_dims_duplicadas_lanza() -> None:
    with pytest.raises(CacheKeyValidationError):
        validate_cache_key(_make(("exchange", "symbol", "timeframe", "timeframe")))
