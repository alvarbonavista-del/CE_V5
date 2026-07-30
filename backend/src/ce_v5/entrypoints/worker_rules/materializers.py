"""Registro de materializadores del worker de reglas (CE-14, dispatch MAT-06/07).

El binding source_id -> materializador vive AQUI, en el composition root del worker:
es la unica capa que ve a la vez infra (lectores) y platform (funciones puras). La
declaracion ADR-008 no puede portar funciones de platform, asi que memory_model es
METADATA de la declaracion, NO la clave de dispatch (MAT-06): el dispatch es por
SOURCE_ID contra este registro.

Cada materializador implementa el Protocol SourceMaterializer (structural): sabe leer
su base y producir su serie tuple[Decimal, ...] oldest->newest. En v5.0 estan
cableadas (MAT-07, DAG bottom-up footprint -> delta -> cvd):
- vp.poc/vah/val: WINDOWED sobre footprint (FootprintWindowedSpec, ventana 100).
- orderflow.delta: POINT_LOCAL sobre footprint (FootprintPointLocalSpec, bar_delta).
- cvd.value: INTEGRATOR sobre el delta (CvdIntegratorSpec, replay desde snapshot).
market.close conserva su lectura directa (read_close_window) en _series_for.
orderflow.delta_momentum (WINDOWED sobre delta) sigue SIN cablear: _series_for lanza
UnwiredSourceError si una regla la referencia, en vez de servir una serie equivocada
(MAT-06 decision 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from ce_v5.infra.db.cvd_snapshot import read_cvd_snapshot_before, write_cvd_snapshot
from ce_v5.infra.db.market_footprint import (
    read_footprint_delta_range,
    read_footprint_window,
)
from ce_v5.platform.rules.cvd import CVD_SOURCE_ID, ResetPolicy
from ce_v5.platform.rules.materializer import (
    materialize_recursive,
    materialize_windowed,
)
from ce_v5.platform.rules.orderflow import ORDERFLOW_DELTA_SOURCE_ID
from ce_v5.platform.rules.volume_profile import (
    DEFAULT_BIN_COUNT,
    VP_POC_SOURCE_ID,
    VP_VAH_SOURCE_ID,
    VP_VAL_SOURCE_ID,
    compute_volume_profile,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ce_v5.infra.db.ports import Session
    from source.families.footprint import FootprintPayload

# Ventana rodante del perfil de volumen [PARIDAD v4 _WINDOW]. NO es dimension de
# cache_key (VP_CACHE_KEY_SCHEMA no la lleva): es constante FIJA de materializacion
# (MAT-05 Q3), no override por regla. Con 100, el materializador no emite valor hasta
# tener 100 footprints; la historia corta la trata el evaluador como NOT_EVALUABLE.
PROFILE_WINDOW_BARS = 100

# v5.0 solo materializa cvd rolling: la guarda del compilador (MAT-05 Q4) rechaza
# reglas con params de fuente, asi que cvd.value llega siempre con su default (rolling).
# session_utc queda diferido a la propagacion de params (MAT-07 D4).
CVD_RESET_POLICY_V5 = ResetPolicy.ROLLING.value


class UnwiredSourceError(RuntimeError):
    """Una regla referencia una fuente servible sin materializador cableado (v5.0)."""


class SourceMaterializer(Protocol):
    """Materializa la serie de una fuente: lee su base y produce tuple[Decimal, ...]."""

    def materialize(
        self,
        session: Session,
        exchange: str,
        symbol: str,
        timeframe: str,
        open_time: int,
        history_bars: int,
    ) -> tuple[Decimal, ...]: ...


@dataclass(frozen=True, slots=True)
class FootprintWindowedSpec:
    """Materializador WINDOWED sobre footprint: funcion pura + ventana rodante.

    transform mapea una ventana ACOTADA de footprints a su valor Decimal (p.ej. el POC
    del perfil de esa ventana). window_bars es la ventana rodante FIJA del perfil.
    """

    transform: Callable[[Sequence[FootprintPayload]], Decimal]
    window_bars: int = PROFILE_WINDOW_BARS

    def materialize(
        self,
        session: Session,
        exchange: str,
        symbol: str,
        timeframe: str,
        open_time: int,
        history_bars: int,
    ) -> tuple[Decimal, ...]:
        # Lee history_bars + window_bars - 1 footprints para emitir history_bars
        # valores, cada uno sobre su ventana rodante de window_bars. La escasez la
        # resuelve materialize_windowed (menos valores o ()): NOT_EVALUABLE.
        base = read_footprint_window(
            session,
            exchange,
            symbol,
            timeframe,
            open_time,
            history_bars + self.window_bars - 1,
        )
        return materialize_windowed(
            base,
            self.transform,
            window_bars=self.window_bars,
            history_bars=history_bars,
        )


@dataclass(frozen=True, slots=True)
class FootprintPointLocalSpec:
    """Materializador POINT_LOCAL sobre footprint: valor de T = footprint de T.

    extract mapea el footprint de la barra a su valor Decimal (p.ej. bar_delta para
    orderflow.delta). La serie es la ventana de footprints leida tal cual, un valor por
    barra: no hay ventana acotada ni recurrencia.
    """

    extract: Callable[[FootprintPayload], Decimal]

    def materialize(
        self,
        session: Session,
        exchange: str,
        symbol: str,
        timeframe: str,
        open_time: int,
        history_bars: int,
    ) -> tuple[Decimal, ...]:
        footprints = read_footprint_window(
            session, exchange, symbol, timeframe, open_time, history_bars
        )
        return tuple(self.extract(footprint) for footprint in footprints)


def _cvd_step(previous: Decimal, delta: Decimal) -> Decimal:
    """Recurrencia INTEGRATOR de cvd rolling: acumula (cvd[T] = cvd[T-1] + delta[T])."""
    return previous + delta


@dataclass(frozen=True, slots=True)
class CvdIntegratorSpec:
    """Materializador INTEGRATOR de cvd.value: replay ACOTADO desde snapshot (MAT-07).

    cvd es el acumulado del delta de barra (orderflow.delta = bar_delta del footprint).
    Materializa la ventana SEMBRANDO materialize_recursive con el snapshot vigente
    ANTERIOR a ella y acumulando los deltas posteriores (rolling; session_utc
    diferido). Sin snapshot ancla, arranca el acumulado en el inicio de la ventana
    (bootstrap: el valor ABSOLUTO del rolling es anchor-dependiente, la divergencia no,
    cvd.py). Tras calcular, PERSISTE el snapshot de la barra vigente (open_time): es un
    materializador CON ESTADO (el snapshot ES su memoria de replay), idempotente (ON
    CONFLICT DO NOTHING). El replay desde CUALQUIER snapshot valido reproduce la cola
    identica bit a bit (ADR-007): el fold es determinista sobre Decimal.
    """

    reset_policy: str = CVD_RESET_POLICY_V5

    def materialize(
        self,
        session: Session,
        exchange: str,
        symbol: str,
        timeframe: str,
        open_time: int,
        history_bars: int,
    ) -> tuple[Decimal, ...]:
        window = read_footprint_window(
            session, exchange, symbol, timeframe, open_time, history_bars
        )
        if not window:
            return ()
        first_open_time = window[0].open_time
        anchor = read_cvd_snapshot_before(
            session, exchange, symbol, timeframe, self.reset_policy, first_open_time
        )
        if anchor is not None:
            anchor_open_time, anchor_value = anchor
            deltas = read_footprint_delta_range(
                session, exchange, symbol, timeframe, anchor_open_time, open_time
            )
            full = materialize_recursive(
                anchor_value, [delta for _, delta in deltas], _cvd_step
            )
            series = full[-len(window) :]
        else:
            series = materialize_recursive(
                Decimal(0), [footprint.bar_delta for footprint in window], _cvd_step
            )
        write_cvd_snapshot(
            session,
            exchange,
            symbol,
            timeframe,
            self.reset_policy,
            open_time,
            series[-1],
        )
        return series


def _poc(window: Sequence[FootprintPayload]) -> Decimal:
    return compute_volume_profile(window, bin_count=DEFAULT_BIN_COUNT).poc


def _vah(window: Sequence[FootprintPayload]) -> Decimal:
    return compute_volume_profile(window, bin_count=DEFAULT_BIN_COUNT).vah


def _val(window: Sequence[FootprintPayload]) -> Decimal:
    return compute_volume_profile(window, bin_count=DEFAULT_BIN_COUNT).val


def _bar_delta(footprint: FootprintPayload) -> Decimal:
    return footprint.bar_delta


# Registro por SOURCE_ID (MAT-06/07). Las fuentes cableadas de v5.0.
SOURCE_MATERIALIZERS: dict[str, SourceMaterializer] = {
    VP_POC_SOURCE_ID: FootprintWindowedSpec(transform=_poc),
    VP_VAH_SOURCE_ID: FootprintWindowedSpec(transform=_vah),
    VP_VAL_SOURCE_ID: FootprintWindowedSpec(transform=_val),
    ORDERFLOW_DELTA_SOURCE_ID: FootprintPointLocalSpec(extract=_bar_delta),
    CVD_SOURCE_ID: CvdIntegratorSpec(),
}
