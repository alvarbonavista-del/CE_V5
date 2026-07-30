"""Discovery EXPLICITO de DataSources para el catalogo vivo (CE-14, ADR-008).

Convencion (dictamen P08c-MAT-02 punto 2): cada modulo productor expone una funcion
declarations() -> tuple[DataSourceDeclaration, ...] que retorna SOLO las declaraciones
cuyas dependencias son servibles hoy. El discovery agrega una LISTA EXPLICITA de modulos
productores (no descubrimiento por nombre de fichero). Las fuentes diferidas (absorcion,
climax, void, notrade por candle.*; bloque L2 por l2.*) NO exponen declaracion y por
tanto NO entran al catalogo vivo hasta que su dependencia exista (aditividad).
"""

from __future__ import annotations

from ce_v5.platform.rules import cvd, orderflow, rawclose, rawfootprint, volume_profile
from ce_v5.platform.rules.indicators import candle, volume
from source.datasource import DataSourceDeclaration


def discover_declarations() -> tuple[DataSourceDeclaration, ...]:
    """Agrega las declaraciones de todos los modulos productores (lista explicita)."""
    return (
        *rawclose.declarations(),
        *rawfootprint.declarations(),
        *volume_profile.declarations(),
        *orderflow.declarations(),
        *cvd.declarations(),
        *candle.declarations(),
        *volume.declarations(),
    )
