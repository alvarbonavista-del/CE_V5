"""Registro de materializadores del worker de reglas (CE-14, dispatch MAT-06/07).

El binding source_id -> materializador vive AQUI, en el composition root del worker:
es la unica capa que ve a la vez infra (lectores) y platform (funciones puras). La
declaracion ADR-008 no puede portar funciones de platform, asi que memory_model es
METADATA de la declaracion, NO la clave de dispatch (MAT-06): el dispatch es por
SOURCE_ID contra este registro.

Cada materializador implementa el Protocol SourceMaterializer (structural): sabe leer
su base y producir su serie tuple[Decimal, ...] oldest->newest. En v5.0 estan
cableadas (MAT-07, DAG bottom-up footprint -> delta -> cvd):
- vp.poc/vah/val/hvn/lvn: WINDOWED sobre footprint (FootprintWindowedSpec, ventana 100).
- orderflow.delta: POINT_LOCAL sobre footprint (FootprintPointLocalSpec, bar_delta).
- cvd.value: INTEGRATOR sobre el delta (CvdIntegratorSpec, replay desde snapshot).
- orderflow.delta_momentum: WINDOWED sobre orderflow.delta (DerivedSeriesSpec, DAG de
  2o nivel: consume otra fuente DERIVADA, no el footprint crudo; MAT-08).
- pivotphase.phase/confidence: RECURSIVE, replay propio desde snapshot
  (PivotphasePhaseSpec/PivotphaseConfidenceSpec, glue en pivotphase_materializer; T5).
market.close conserva su lectura directa (read_close_window) en _series_for.

Con delta_momentum cableada NO queda ninguna fuente servible del catalogo vivo sin
materializador. El fallo ruidoso sigue vigente como INVARIANTE, no como caso muerto:
cualquier fuente servible que se declare sin cablear su materializador -- o una base de
DerivedSeriesSpec que no este en el registro -- levanta UnwiredSourceError en vez de
servir una serie equivocada (MAT-06 decision 3, MAT-08).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ce_v5.entrypoints.worker_rules.pivotphase_materializer import (
    PivotphaseConfidenceSpec,
    PivotphasePhaseSpec,
)
from ce_v5.infra.db.cvd_snapshot import read_cvd_snapshot_before, write_cvd_snapshot
from ce_v5.infra.db.market_candles import read_ohlcv_window
from ce_v5.infra.db.market_footprint import (
    read_footprint_delta_range,
    read_footprint_window,
)
from ce_v5.platform.rules.cvd import CVD_SOURCE_ID, ResetPolicy, session_starts
from ce_v5.platform.rules.footprint_range import (
    FOOTPRINT_PRICE_RANGE_SOURCE_ID,
    price_range,
)
from ce_v5.platform.rules.indicators.candle import (
    CANDLE_BODY_PCT_SOURCE_ID,
    CANDLE_LOWER_SHADOW_PCT_SOURCE_ID,
    CANDLE_UPPER_SHADOW_PCT_SOURCE_ID,
    body_pct,
    lower_shadow_pct,
    upper_shadow_pct,
)
from ce_v5.platform.rules.indicators.volume import (
    LOOKBACK_DEFAULT,
    VOLUME_RATIO_VS_AVG_SOURCE_ID,
    ratio_vs_avg,
)
from ce_v5.platform.rules.indicators.vwap import (
    N_CANDLES_DEFAULT,
    VWAP_DISTANCE_PCT_SOURCE_ID,
    VWAP_VALUE_SOURCE_ID,
)
from ce_v5.platform.rules.indicators.vwap import (
    value as vwap_value,
)
from ce_v5.platform.rules.materializer import (
    materialize_recursive,
    materialize_windowed,
)
from ce_v5.platform.rules.orderflow import (
    ORDERFLOW_DELTA_MOMENTUM_SOURCE_ID,
    ORDERFLOW_DELTA_SOURCE_ID,
    compute_delta_momentum,
)
from ce_v5.platform.rules.pivotphase import (
    PIVOTPHASE_CONFIDENCE_SOURCE_ID,
    PIVOTPHASE_PHASE_SOURCE_ID,
)
from ce_v5.platform.rules.volume_profile import (
    DEFAULT_BIN_COUNT,
    VP_HVN_SOURCE_ID,
    VP_LVN_SOURCE_ID,
    VP_POC_SOURCE_ID,
    VP_VAH_SOURCE_ID,
    VP_VAL_SOURCE_ID,
    compute_volume_profile,
    select_hvn_price,
    select_lvn_price,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from ce_v5.infra.db.market_candles import CandleOHLCV
    from ce_v5.infra.db.ports import Session
    from source.families.footprint import FootprintPayload
    from source.rules.scalar import ScalarValue

# Ventana rodante del perfil de volumen [PARIDAD v4 _WINDOW]. NO es dimension de
# cache_key (VP_CACHE_KEY_SCHEMA no la lleva): es constante FIJA de materializacion
# (MAT-05 Q3), no override por regla. Con 100, el materializador no emite valor hasta
# tener 100 footprints; la historia corta la trata el evaluador como NOT_EVALUABLE.
PROFILE_WINDOW_BARS = 100

# Politica de reset por DEFECTO de cvd.value: la que trae la declaracion. Desde MAT-05
# Q2 una regla puede pedir session_utc por parametro y el dispatch la propaga
# (with_params); sin override se sigue sirviendo rolling.
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


@runtime_checkable
class ParameterizedMaterializer(Protocol):
    """Materializador cuyo comportamiento depende de params DECLARADOS (MAT-05 Q2).

    El dispatch pregunta por isinstance y, si la fuente trae params efectivos, pide una
    copia LIGADA a ellos; el registro guarda siempre la instancia con los defaults. Un
    materializador que NO implementa esto ignora los params por construccion, no por
    olvido: su serie no depende de ninguno (vp.* con bin_count es el caso pendiente --
    su transform aun fija DEFAULT_BIN_COUNT, y cablearlo es trabajo de su propia tanda).
    """

    def with_params(self, params: Mapping[str, ScalarValue]) -> SourceMaterializer: ...


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


@dataclass(frozen=True, slots=True)
class CandlePointLocalSpec:
    """Materializador POINT_LOCAL sobre vela: valor de T = extract(vela de T).

    Como FootprintPointLocalSpec, pero sobre CandleOHLCV via read_ohlcv_window (el
    lector ya existe en main; P08b-INT-03 C1, no se crea lector nuevo).
    """

    extract: Callable[[CandleOHLCV], Decimal]

    def materialize(
        self,
        session: Session,
        exchange: str,
        symbol: str,
        timeframe: str,
        open_time: int,
        history_bars: int,
    ) -> tuple[Decimal, ...]:
        candles = read_ohlcv_window(
            session, exchange, symbol, timeframe, open_time, history_bars
        )
        return tuple(self.extract(candle) for candle in candles)


@dataclass(frozen=True, slots=True)
class CandleWindowedSpec:
    """Materializador WINDOWED sobre vela: funcion pura + ventana rodante.

    Como FootprintWindowedSpec, pero sobre CandleOHLCV via read_ohlcv_window. Lee
    history_bars + window_bars - 1 velas y aplica materialize_windowed.
    """

    transform: Callable[[Sequence[CandleOHLCV]], Decimal]
    window_bars: int

    def materialize(
        self,
        session: Session,
        exchange: str,
        symbol: str,
        timeframe: str,
        open_time: int,
        history_bars: int,
    ) -> tuple[Decimal, ...]:
        base = read_ohlcv_window(
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


def _cvd_step(previous: Decimal, delta: Decimal) -> Decimal:
    """Recurrencia INTEGRATOR de cvd rolling: acumula (cvd[T] = cvd[T-1] + delta[T])."""
    return previous + delta


def _cvd_session_step(previous: Decimal, bar: tuple[bool, Decimal]) -> Decimal:
    """Recurrencia INTEGRATOR de cvd session_utc: la barra que ABRE dia UTC nuevo
    reinicia el acumulado en su propio delta; el resto acumula como rolling.

    El flag lo precalcula session_starts (cvd.py) comparando el dia de cada barra con el
    de su predecesora -- incluida el ANCLA del replay --, asi que el paso sigue siendo
    PURO y sin estado: mismo (previo, barra) -> mismo valor (ADR-007).
    """
    starts_session, delta = bar
    return delta if starts_session else previous + delta


def _cvd_series(
    policy: ResetPolicy,
    seed: Decimal,
    bars: Sequence[tuple[int, Decimal]],
    *,
    previous_open_time: int | None,
) -> tuple[Decimal, ...]:
    """El acumulado de cvd sobre `bars`, sembrado en `seed`, segun la politica.

    rolling ignora los open_time (acumulado continuo). session_utc los necesita para
    saber que barra abre dia UTC nuevo respecto a su predecesora (previous_open_time =
    la barra ancla, o None en bootstrap). Un solo camino de fold en ambos casos.
    """
    if policy is not ResetPolicy.SESSION_UTC:
        return materialize_recursive(seed, [delta for _, delta in bars], _cvd_step)
    resets = session_starts(
        [bar_open_time for bar_open_time, _ in bars],
        previous_open_time=previous_open_time,
    )
    session_bars = [
        (starts_session, delta)
        for starts_session, (_, delta) in zip(resets, bars, strict=True)
    ]
    return materialize_recursive(seed, session_bars, _cvd_session_step)


@dataclass(frozen=True, slots=True)
class CvdIntegratorSpec:
    """Materializador INTEGRATOR de cvd.value: replay ACOTADO desde snapshot (MAT-07).

    cvd es el acumulado del delta de barra (orderflow.delta = bar_delta del footprint).
    Materializa la ventana SEMBRANDO el fold con el snapshot vigente ANTERIOR a ella y
    acumulando los deltas posteriores. Sin snapshot ancla, arranca el acumulado en el
    inicio de la ventana (bootstrap: el valor ABSOLUTO del rolling es
    anchor-dependiente, la divergencia no, cvd.py). Tras calcular, PERSISTE el snapshot
    de la barra vigente (open_time): es un materializador CON ESTADO (el snapshot ES su
    memoria de replay), idempotente (ON CONFLICT DO NOTHING). El replay desde CUALQUIER
    snapshot valido reproduce la cola identica bit a bit (ADR-007): el fold es
    determinista sobre Decimal.

    reset_policy es PARAMETRO DECLARADO (OA-1) y desde MAT-05 Q2 lo propaga el dispatch
    (with_params): rolling por defecto, session_utc si la regla lo pide. Los snapshots
    NO se cruzan -- reset_policy entra en la identidad de cvd_snapshot --, asi que el
    ancla de un session-CVD nunca siembra un rolling-CVD ni al reves.
    """

    reset_policy: str = CVD_RESET_POLICY_V5

    def with_params(self, params: Mapping[str, ScalarValue]) -> SourceMaterializer:
        """Copia ligada al reset_policy EFECTIVO de la regla (MAT-05 Q2).

        El compilador ya valido nombre, tipo Y dominio (fix MAT-05 Q2, seccion 34:
        ParamSpec.valid_values) para todo override que pase por compile(). Este chequeo
        queda como ULTIMA linea de defensa para quien llame with_params directamente
        (fuera de un ExecutionPlan compilado): un valor fuera del enum falla RUIDOSO,
        no materializa rolling en silencio.
        """
        value = params.get("reset_policy")
        if value is None or value.string_value is None:
            return self
        try:
            policy = ResetPolicy(value.string_value)
        except ValueError as exc:
            msg = (
                f"reset_policy {value.string_value!r} no es una politica de cvd valida "
                f"({[p.value for p in ResetPolicy]!r}): no se materializa (fail-loud)."
            )
            raise UnwiredSourceError(msg) from exc
        return replace(self, reset_policy=policy.value)

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
        policy = ResetPolicy(self.reset_policy)
        first_open_time = window[0].open_time
        anchor = read_cvd_snapshot_before(
            session, exchange, symbol, timeframe, self.reset_policy, first_open_time
        )
        if anchor is not None:
            anchor_open_time, anchor_value = anchor
            deltas = read_footprint_delta_range(
                session, exchange, symbol, timeframe, anchor_open_time, open_time
            )
            full = _cvd_series(
                policy, anchor_value, deltas, previous_open_time=anchor_open_time
            )
            series = full[-len(window) :]
        else:
            series = _cvd_series(
                policy,
                Decimal(0),
                [(footprint.open_time, footprint.bar_delta) for footprint in window],
                previous_open_time=None,
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


@dataclass(frozen=True, slots=True)
class DerivedSeriesSpec:
    """Materializador de una fuente que consume OTRA fuente DERIVADA (DAG 2o nivel).

    Materializa la fuente BASE por su source_id (su SourceMaterializer en el registro),
    pidiendo history_bars + lookback barras; aplica un transform de SERIE puro sobre la
    serie completa de la base; y devuelve las history_bars mas recientes. lookback es el
    numero de barras previas que el transform necesita para que el PRIMER valor de la
    ventana pedida use su contexto real (p.ej. 1 para un diff barra-a-barra; el borde
    real sin prior lo resuelve la funcion pura). Fallo ruidoso si la base no esta
    cableada -> UnwiredSourceError, como toda fuente sin materializador (MAT-08).
    """

    base_source_id: str
    transform: Callable[[Sequence[Decimal]], tuple[Decimal, ...]]
    lookback: int

    def materialize(
        self,
        session: Session,
        exchange: str,
        symbol: str,
        timeframe: str,
        open_time: int,
        history_bars: int,
    ) -> tuple[Decimal, ...]:
        base_materializer = SOURCE_MATERIALIZERS.get(self.base_source_id)
        if base_materializer is None:
            msg = (
                f"la fuente base {self.base_source_id!r} de un DerivedSeriesSpec no "
                "esta cableada en v5.0: no se materializa la derivada (MAT-08)."
            )
            raise UnwiredSourceError(msg)
        base = base_materializer.materialize(
            session,
            exchange,
            symbol,
            timeframe,
            open_time,
            history_bars + self.lookback,
        )
        series = self.transform(base)
        if history_bars <= 0:
            return ()
        return series[-history_bars:]


def _poc(window: Sequence[FootprintPayload]) -> Decimal:
    return compute_volume_profile(window, bin_count=DEFAULT_BIN_COUNT).poc


def _vah(window: Sequence[FootprintPayload]) -> Decimal:
    return compute_volume_profile(window, bin_count=DEFAULT_BIN_COUNT).vah


def _val(window: Sequence[FootprintPayload]) -> Decimal:
    return compute_volume_profile(window, bin_count=DEFAULT_BIN_COUNT).val


def _hvn(window: Sequence[FootprintPayload]) -> Decimal:
    return select_hvn_price(window, bin_count=DEFAULT_BIN_COUNT)


def _lvn(window: Sequence[FootprintPayload]) -> Decimal:
    return select_lvn_price(window, bin_count=DEFAULT_BIN_COUNT)


def _bar_delta(footprint: FootprintPayload) -> Decimal:
    return footprint.bar_delta


def _candle_body_pct(candle: CandleOHLCV) -> Decimal:
    return body_pct((candle.open,), (candle.high,), (candle.low,), (candle.close,))[0]


def _candle_upper_shadow_pct(candle: CandleOHLCV) -> Decimal:
    return upper_shadow_pct(
        (candle.open,), (candle.high,), (candle.low,), (candle.close,)
    )[0]


def _candle_lower_shadow_pct(candle: CandleOHLCV) -> Decimal:
    return lower_shadow_pct(
        (candle.open,), (candle.high,), (candle.low,), (candle.close,)
    )[0]


def _volume_ratio_vs_avg(window: Sequence[CandleOHLCV]) -> Decimal:
    series = ratio_vs_avg(tuple(c.volume for c in window), lookback=LOOKBACK_DEFAULT)
    value = series[-1]
    if (
        value is None
    ):  # inalcanzable: window_bars = LOOKBACK_DEFAULT + 1 => ventana llena
        msg = "volume.ratio_vs_avg materializado sin ventana llena (invariante roto)."
        raise RuntimeError(msg)
    return value


def _hlc3_mean(window: Sequence[CandleOHLCV]) -> Decimal:
    """Media NO PONDERADA de HLC3 sobre la ventana: fallback de materializacion del VWAP
    cuando el volumen total de la ventana es 0 (P08b-INT-04, opcion B). Es el limite del
    VWAP con pesos uniformes; con volumen 0 las velas estan planas, asi que coincide con
    el precio plano real. La POLITICA del degenerado vive AQUI (cableado), no en la
    funcion pura de vwap, que se queda fiel (None = indefinido)."""
    with localcontext() as ctx:
        ctx.prec = 34
        ctx.rounding = ROUND_HALF_EVEN
        total = Decimal(0)
        for candle in window:
            total += (candle.high + candle.low + candle.close) / 3
        return total / Decimal(len(window))


def _vwap_effective(window: Sequence[CandleOHLCV]) -> Decimal:
    """VWAP de la ventana (funcion pura), o el fallback HLC3 si el volumen total
    es 0."""
    v = vwap_value(
        tuple(c.high for c in window),
        tuple(c.low for c in window),
        tuple(c.close for c in window),
        tuple(c.volume for c in window),
        n_candles=N_CANDLES_DEFAULT,
    )[-1]
    return v if v is not None else _hlc3_mean(window)


def _vwap_value(window: Sequence[CandleOHLCV]) -> Decimal:
    return _vwap_effective(window)


def _vwap_distance_pct(window: Sequence[CandleOHLCV]) -> Decimal:
    """|close - VWAP| / VWAP * 100 con el VWAP EFECTIVO (fallback HLC3 si vol
    total = 0)."""
    vwap_ref = _vwap_effective(window)
    close = window[-1].close
    with localcontext() as ctx:
        ctx.prec = 34
        ctx.rounding = ROUND_HALF_EVEN
        if vwap_ref <= 0:
            return Decimal(0)
        return abs(close - vwap_ref) / vwap_ref * Decimal(100)


# Registro por SOURCE_ID (MAT-06/07). Las fuentes cableadas de v5.0.
SOURCE_MATERIALIZERS: dict[str, SourceMaterializer] = {
    VP_POC_SOURCE_ID: FootprintWindowedSpec(transform=_poc),
    VP_VAH_SOURCE_ID: FootprintWindowedSpec(transform=_vah),
    VP_VAL_SOURCE_ID: FootprintWindowedSpec(transform=_val),
    VP_HVN_SOURCE_ID: FootprintWindowedSpec(transform=_hvn),
    VP_LVN_SOURCE_ID: FootprintWindowedSpec(transform=_lvn),
    ORDERFLOW_DELTA_SOURCE_ID: FootprintPointLocalSpec(extract=_bar_delta),
    CVD_SOURCE_ID: CvdIntegratorSpec(),
    ORDERFLOW_DELTA_MOMENTUM_SOURCE_ID: DerivedSeriesSpec(
        base_source_id=ORDERFLOW_DELTA_SOURCE_ID,
        transform=compute_delta_momentum,
        lookback=1,
    ),
    FOOTPRINT_PRICE_RANGE_SOURCE_ID: FootprintPointLocalSpec(extract=price_range),
    PIVOTPHASE_PHASE_SOURCE_ID: PivotphasePhaseSpec(),
    PIVOTPHASE_CONFIDENCE_SOURCE_ID: PivotphaseConfidenceSpec(),
    CANDLE_BODY_PCT_SOURCE_ID: CandlePointLocalSpec(extract=_candle_body_pct),
    CANDLE_UPPER_SHADOW_PCT_SOURCE_ID: CandlePointLocalSpec(
        extract=_candle_upper_shadow_pct
    ),
    CANDLE_LOWER_SHADOW_PCT_SOURCE_ID: CandlePointLocalSpec(
        extract=_candle_lower_shadow_pct
    ),
    VOLUME_RATIO_VS_AVG_SOURCE_ID: CandleWindowedSpec(
        transform=_volume_ratio_vs_avg, window_bars=LOOKBACK_DEFAULT + 1
    ),
    VWAP_VALUE_SOURCE_ID: CandleWindowedSpec(
        transform=_vwap_value, window_bars=N_CANDLES_DEFAULT
    ),
    VWAP_DISTANCE_PCT_SOURCE_ID: CandleWindowedSpec(
        transform=_vwap_distance_pct, window_bars=N_CANDLES_DEFAULT
    ),
}
