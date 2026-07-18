"""Marco declarativo de DataSource (ADR-008, INFORME 6 sec 12)."""

from source.datasource.declaration import (
    DataSourceDeclaration,
    HistoryUnit,
    ParamSpec,
    Servibility,
    SharingScope,
    SourceType,
)

__all__ = [
    "DataSourceDeclaration",
    "HistoryUnit",
    "ParamSpec",
    "Servibility",
    "SharingScope",
    "SourceType",
]
