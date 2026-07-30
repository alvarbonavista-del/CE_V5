"""Validacion del cache_key COMPLETO por NATURALEZA (CE-14, dictamen P08c-MAT-03).

Regla ratificada (opcion B, MAT-03): el cache_key de una fuente esta COMPLETO si:
  1. Lleva las dims de FLUJO obligatorias: exchange, symbol, timeframe. (market_type se
     anade donde la fuente separa spot/derivado; NO es obligatorio -- market.close lo
     omite porque esta pineado a spot.)
  2. TODO param declarado (tuple params) aparece como dim del cache_key: dos reglas con
     distinto valor de un param NO deben colisionar en la evaluacion compartida.
  3. formula_version SOLO donde la formula pueda cambiar de semantica SIN cambio de
     param (fuentes config/versionadas, p.ej. l2). Las lossless (footprint, orderflow)
     lo OMITEN con justificacion escrita. NO es detectable estaticamente, asi que el
     validador NO lo EXIGE: lo ACEPTA como dim opcional.
  4. as_of SOLO en fuentes ancladas en tiempo por snapshot (l2). Las derivadas por
     barra lo omiten (as_of = open_time, implicito en timeframe). Tampoco se
     EXIGE; se ACEPTA.

El validador enforce 1 y 2 (lo verificable y lo que el consumidor -- P08b -- necesita) y
ACEPTA formula_version/as_of como dims opcionales. Es ADITIVO: pasa todas las
declaraciones ya construidas. El HANDOFF documenta cuando aplican 3 y 4 por naturaleza,
con un ejemplo por memory_model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from source.datasource import DataSourceDeclaration

# Dims de flujo OBLIGATORIAS en toda fuente (regla 1). market_type NO es obligatoria:
# se anade solo donde la fuente distingue spot/derivado.
_MANDATORY_FLOW_DIMS = ("exchange", "symbol", "timeframe")


class CacheKeyValidationError(RuntimeError):
    """El cache_key_schema de una fuente no cumple la regla de naturaleza (MAT-03)."""


def validate_cache_key(declaration: DataSourceDeclaration) -> None:
    """Valida el cache_key_schema de UNA declaracion (reglas 1 y 2). Lanza si falla."""
    schema = declaration.cache_key_schema
    dims = set(schema)
    if len(dims) != len(schema):
        msg = (
            f"{declaration.source_id}: cache_key_schema con dimensiones duplicadas "
            f"{schema}."
        )
        raise CacheKeyValidationError(msg)
    missing_flow = [dim for dim in _MANDATORY_FLOW_DIMS if dim not in dims]
    if missing_flow:
        msg = (
            f"{declaration.source_id}: faltan dims de flujo {missing_flow} en el "
            f"cache_key_schema {schema} (regla 1, MAT-03)."
        )
        raise CacheKeyValidationError(msg)
    missing_params = [
        param.name for param in declaration.params if param.name not in dims
    ]
    if missing_params:
        msg = (
            f"{declaration.source_id}: los params {missing_params} no estan en el "
            f"cache_key_schema {schema}: dos reglas con distinto valor colisionarian "
            f"en la evaluacion compartida (regla 2, MAT-03)."
        )
        raise CacheKeyValidationError(msg)


def validate_cache_keys(declarations: Iterable[DataSourceDeclaration]) -> None:
    """Valida el cache_key de cada declaracion; lanza en la primera que incumpla."""
    for declaration in declarations:
        validate_cache_key(declaration)
