"""Formato canonico de tiempo de CE v5 (ADR-007).

El tiempo canonico "en cable" es UTC epoch milliseconds (int64): un
entero, nunca una fecha-hora naive ni local. La representacion ISO 8601
UTC es solo para display/logs, nunca "en cable".
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from pydantic import Field

# Entero con signo de 64 bits (int64): valor maximo admitido.
INT64_MAX = 2**63 - 1

EpochMillis = Annotated[
    int,
    Field(
        ge=0,
        le=INT64_MAX,
        description=(
            "Instante en UTC epoch milliseconds (int64). Formato canonico "
            "de tiempo en cable (ADR-007)."
        ),
    ),
]

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def to_iso8601(ms: int) -> str:
    """Formatea un EpochMillis como ISO 8601 UTC (solo display/logs)."""
    return (_EPOCH + timedelta(milliseconds=ms)).isoformat()
