"""Registro de materializadores del worker de reglas (CE-14, dispatch MAT-06).

El binding source_id -> (lector de base, funcion pura, ventana) vive AQUI, en el
composition root del worker: es la unica capa que ve a la vez infra (lectores) y
platform (funciones puras). La declaracion ADR-008 no puede portar funciones de
platform, asi que memory_model es METADATA de la declaracion, NO la clave de dispatch
(MAT-06): el dispatch es por SOURCE_ID.

En v5.0 solo estan cableadas las WINDOWED sobre footprint (vp.poc/vah/val): base =
read_footprint_window, ventana rodante = 100 barras [PARIDAD v4], bin_count = 50
(default de la declaracion). market.close conserva su lectura directa
(read_close_window) en _series_for. Las demas servibles derivadas (orderflow.delta,
orderflow.delta_momentum, cvd.value) NO estan cableadas: _series_for lanza
UnwiredSourceError si una regla las referencia, en vez de servir una serie equivocada
(MAT-06 decision 3: excepcion tecnica por ahora; el trato de cuarentena se eleva al
cablear la primera de ellas que una regla viva referencie).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ce_v5.infra.db.market_footprint import read_footprint_window
from ce_v5.platform.rules.materializer import materialize_windowed
from ce_v5.platform.rules.volume_profile import (
    DEFAULT_BIN_COUNT,
    VP_POC_SOURCE_ID,
    VP_VAH_SOURCE_ID,
    VP_VAL_SOURCE_ID,
    compute_volume_profile,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from decimal import Decimal

    from ce_v5.infra.db.ports import Session
    from source.families.footprint import FootprintPayload

# Ventana rodante del perfil de volumen [PARIDAD v4 _WINDOW]. NO es dimension de
# cache_key (VP_CACHE_KEY_SCHEMA no la lleva): es constante FIJA de materializacion
# (MAT-05 Q3), no override por regla. Con 100, el materializador no emite valor hasta
# tener 100 footprints; la historia corta la trata el evaluador como NOT_EVALUABLE.
PROFILE_WINDOW_BARS = 100


class UnwiredSourceError(RuntimeError):
    """Una regla referencia una fuente servible sin materializador cableado (v5.0)."""


@dataclass(frozen=True, slots=True)
class FootprintWindowedSpec:
    """Materializador WINDOWED sobre footprint: funcion pura + ventana rodante.

    transform mapea una ventana ACOTADA de footprints a su valor Decimal (p.ej. el POC
    del perfil de esa ventana). window_bars es la ventana rodante FIJA del perfil.
    """

    transform: Callable[[Sequence[FootprintPayload]], Decimal]
    window_bars: int = PROFILE_WINDOW_BARS


def _poc(window: Sequence[FootprintPayload]) -> Decimal:
    return compute_volume_profile(window, bin_count=DEFAULT_BIN_COUNT).poc


def _vah(window: Sequence[FootprintPayload]) -> Decimal:
    return compute_volume_profile(window, bin_count=DEFAULT_BIN_COUNT).vah


def _val(window: Sequence[FootprintPayload]) -> Decimal:
    return compute_volume_profile(window, bin_count=DEFAULT_BIN_COUNT).val


# Registro por SOURCE_ID (MAT-06). Solo las WINDOWED sobre footprint de v5.0.
FOOTPRINT_MATERIALIZERS: dict[str, FootprintWindowedSpec] = {
    VP_POC_SOURCE_ID: FootprintWindowedSpec(transform=_poc),
    VP_VAH_SOURCE_ID: FootprintWindowedSpec(transform=_vah),
    VP_VAL_SOURCE_ID: FootprintWindowedSpec(transform=_val),
}


def materialize_footprint_windowed(
    session: Session,
    spec: FootprintWindowedSpec,
    exchange: str,
    symbol: str,
    timeframe: str,
    open_time: int,
    history_bars: int,
) -> tuple[Decimal, ...]:
    """Serie WINDOWED de una fuente derivada de footprint (vp.* en v5.0).

    Lee la BASE de footprints necesaria -- history_bars + window_bars - 1, para que
    materialize_windowed pueda emitir history_bars valores, cada uno sobre su ventana
    rodante de window_bars footprints -- y aplica la funcion pura del spec. La escasez
    de historia la resuelve materialize_windowed devolviendo menos valores (o ()): el
    evaluador la trata como NOT_EVALUABLE, no se inventa ninguna barra.
    """
    base = read_footprint_window(
        session,
        exchange,
        symbol,
        timeframe,
        open_time,
        history_bars + spec.window_bars - 1,
    )
    return materialize_windowed(
        base,
        spec.transform,
        window_bars=spec.window_bars,
        history_bars=history_bars,
    )
