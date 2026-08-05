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
- ema.value: RECURSIVE sobre los cierres (EmaRecursiveSpec, replay desde el snapshot de
  la 0023; sin ancla, bootstrap DESDE EL ORIGEN porque la semilla del EMA es el primer
  cierre de la serie, P08b-LOTE3-01).
- macd.line/signal/histogram: RECURSIVE sobre los cierres (MacdRecursiveSpec, replay
  desde el snapshot de la 0026). Las TRES comparten UN estado -- las tres EMAs internas
  -- y un solo paso de calculo; cada entrada del registro publica su proyeccion
  (`output`). Sin warm-up: valor desde la barra 0 (P08b-LOTE3-01).
- fib.nearest_level/level_pct: RECURSIVE sobre el RANGO con histeresis
  (FibRecursiveSpec, glue en fib_materializer; replay desde el snapshot de la 0027).
  Es el unico que consume DOS fuentes derivadas a la vez (swing.high/swing.low) ademas
  del cierre: las pide al REGISTRO ligadas al strength efectivo (P08b-FIB-01).
- rsi.value: RECURSIVE Wilder sobre los cierres (RsiRecursiveSpec, replay desde el
  snapshot de la 0025, cuyo estado son TRES valores -- avg_gain, avg_loss, last_close --
  porque el gain es un diferencial; el warm-up sale como serie MAS CORTA, nunca None a
  media serie; P08b-LOTE3-01).
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

from ce_v5.entrypoints.worker_rules.fib_materializer import FibRecursiveSpec
from ce_v5.entrypoints.worker_rules.pivotphase_materializer import (
    PivotphaseConfidenceSpec,
    PivotphasePhaseSpec,
)
from ce_v5.infra.db.cvd_snapshot import read_cvd_snapshot_before, write_cvd_snapshot
from ce_v5.infra.db.ema_snapshot import read_ema_snapshot_before, write_ema_snapshot
from ce_v5.infra.db.macd_snapshot import read_macd_snapshot_before, write_macd_snapshot
from ce_v5.infra.db.market_candles import (
    read_close_range,
    read_close_window,
    read_ohlcv_window,
)
from ce_v5.infra.db.market_footprint import (
    read_footprint_delta_range,
    read_footprint_window,
)
from ce_v5.infra.db.rsi_snapshot import read_rsi_snapshot_before, write_rsi_snapshot
from ce_v5.platform.rules.cvd import CVD_SOURCE_ID, ResetPolicy, session_starts
from ce_v5.platform.rules.footprint_range import (
    FOOTPRINT_PRICE_RANGE_SOURCE_ID,
    price_range,
)
from ce_v5.platform.rules.indicators.candle import (
    CANDLE_BODY_PCT_SOURCE_ID,
    CANDLE_LOWER_SHADOW_PCT_SOURCE_ID,
    CANDLE_OPEN_SOURCE_ID,
    CANDLE_UPPER_SHADOW_PCT_SOURCE_ID,
    body_pct,
    lower_shadow_pct,
    upper_shadow_pct,
)
from ce_v5.platform.rules.indicators.ema import (
    EMA_PERIOD_DEFAULT,
    EMA_SOURCE_ID,
    ema_from_anchor,
)
from ce_v5.platform.rules.indicators.fib import (
    FIB_LEVEL_PCT_SOURCE_ID,
    FIB_NEAREST_LEVEL_SOURCE_ID,
    FibOutput,
)
from ce_v5.platform.rules.indicators.macd import (
    MACD_FAST_DEFAULT,
    MACD_HISTOGRAM_SOURCE_ID,
    MACD_LINE_SOURCE_ID,
    MACD_SIGNAL_DEFAULT,
    MACD_SIGNAL_SOURCE_ID,
    MACD_SLOW_DEFAULT,
    MacdOutput,
    macd_seed,
    macd_step,
    select_output,
)
from ce_v5.platform.rules.indicators.rsi import (
    RSI_PERIOD_DEFAULT,
    RSI_SOURCE_ID,
    rsi_seed,
    rsi_step,
)
from ce_v5.platform.rules.indicators.swing import (
    SWING_HIGH_SOURCE_ID,
    SWING_LOW_SOURCE_ID,
    SWING_STRENGTH_DEFAULT,
    PivotKind,
    symmetric_pivots,
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

# Ventana rodante de swing.high/low. FIJA, NO param, NO cache_key (MAT-05 Q3, dictamen
# P08b-SWING-01 Q2): la resolucion del pivote depende de strength, no de window_bars.
SWING_WINDOW_BARS = 100


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


@dataclass(frozen=True, slots=True)
class SwingWindowedSpec:
    """Materializador WINDOWED de swing.high/low (dictamen P08b-SWING-01).

    Por barra emite el value del ultimo pivote confirmado del tipo (kind) en la
    ventana rodante de window_bars cierres; si no hay pivote del tipo, FALLBACK = max
    (HIGH) / min (LOW) de la ventana (paridad v4). WINDOWED puro: sin estado, recomputa
    la ventana. strength es overridable (with_params); el registro guarda la instancia
    con default.
    """

    kind: PivotKind
    strength: int = SWING_STRENGTH_DEFAULT
    window_bars: int = SWING_WINDOW_BARS

    def _transform(self, window: Sequence[Decimal]) -> Decimal:
        pivots = symmetric_pivots(window, self.strength)
        matching = [p.value for p in pivots if p.kind is self.kind]
        if matching:
            # symmetric_pivots devuelve en orden de ancla ascendente: el ultimo es
            # el mas reciente confirmado dentro de la ventana.
            return matching[-1]
        return max(window) if self.kind is PivotKind.HIGH else min(window)

    def materialize(
        self,
        session: Session,
        exchange: str,
        symbol: str,
        timeframe: str,
        open_time: int,
        history_bars: int,
    ) -> tuple[Decimal, ...]:
        base = read_close_window(
            session,
            exchange,
            symbol,
            timeframe,
            open_time,
            history_bars + self.window_bars - 1,
        )
        return materialize_windowed(
            base,
            self._transform,
            window_bars=self.window_bars,
            history_bars=history_bars,
        )

    def with_params(self, params: Mapping[str, ScalarValue]) -> SourceMaterializer:
        """Copia ligada al strength EFECTIVO de la regla (MAT-05 Q2).

        El compilador ya valido nombre y tipo (ParamSpec.value_type=INTEGER) para todo
        override que pase por compile(). Este chequeo queda como ULTIMA linea de defensa
        para quien llame with_params directamente (fuera de un ExecutionPlan compilado):
        un valor fuera de dominio (strength < 1) falla RUIDOSO, no materializa con el
        default en silencio.
        """
        value = params.get("strength")
        if value is None or value.integer_value is None:
            return self
        strength = value.integer_value
        if strength < 1:
            msg = (
                f"strength {strength!r} no es una fuerza simetrica valida (exige "
                ">= 1): no se materializa (fail-loud)."
            )
            raise UnwiredSourceError(msg)
        return replace(self, strength=strength)


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
class EmaRecursiveSpec:
    """Materializador RECURSIVE de ema.value: replay ACOTADO desde snapshot (0023).

    EMA[T] = alpha*close[T] + (1-alpha)*EMA[T-1]. Materializa la ventana SEMBRANDO la
    recurrencia con el snapshot vigente ANTERIOR a ella (read_ema_snapshot_before) y
    plegando los cierres posteriores (read_close_range). Tras calcular, PERSISTE el
    snapshot de la barra vigente: es un materializador CON ESTADO, como el de cvd -- el
    snapshot ES su memoria de replay -- e idempotente (ON CONFLICT DO NOTHING).

    SIN ancla, el bootstrap arranca DESDE EL ORIGEN del historico, no desde el inicio de
    la ventana. Es la diferencia de fondo con cvd: el valor absoluto del cvd rolling es
    anchor-dependiente y su forma no, asi que a el le basta la ventana; el EMA NO tiene
    esa propiedad -- su semilla es el PRIMER cierre de la serie (EMA[0] == close[0],
    invariante P08b-08) y cualquier otra semilla da OTRA serie. Sembrar en el inicio de
    ventana no seria un replay acotado, seria un EMA distinto.

    El GATE de ADR-007: el replay desde CUALQUIER snapshot valido reproduce la cola
    identica BIT A BIT a la del ema() puro desde el origen, porque ema_from_anchor
    reutiliza literalmente la recurrencia y el contexto Decimal pinneado de ema().

    period es PARAMETRO DECLARADO (overridable, default 20) y lo propaga el dispatch
    (with_params). Los snapshots NO se cruzan -- period entra en la identidad de
    ema_snapshot (PK, 0023) --, asi que el ancla de un ema(9) nunca siembra un ema(21).
    """

    period: int = EMA_PERIOD_DEFAULT

    def with_params(self, params: Mapping[str, ScalarValue]) -> SourceMaterializer:
        """Copia ligada al period EFECTIVO de la regla (MAT-05 Q2).

        El compilador ya valido nombre y tipo (ParamSpec.value_type=INTEGER) para todo
        override que pase por compile(). Este chequeo queda como ULTIMA linea de defensa
        para quien llame with_params directamente (fuera de un ExecutionPlan compilado):
        un period fuera de dominio (< 1) falla RUIDOSO, no materializa con el default en
        silencio. El mismo dominio que clava la 0023 en su CHECK (period >= 1): un
        period < 1 seria una identidad fantasma de snapshot.
        """
        value = params.get("period")
        if value is None or value.integer_value is None:
            return self
        period = value.integer_value
        if period < 1:
            msg = (
                f"period {period!r} no es un periodo de EMA valido (exige >= 1): no se "
                "materializa (fail-loud)."
            )
            raise UnwiredSourceError(msg)
        return replace(self, period=period)

    def materialize(
        self,
        session: Session,
        exchange: str,
        symbol: str,
        timeframe: str,
        open_time: int,
        history_bars: int,
    ) -> tuple[Decimal, ...]:
        window = read_ohlcv_window(
            session, exchange, symbol, timeframe, open_time, history_bars
        )
        if not window:
            return ()
        first_open_time = window[0].open_time
        anchor = read_ema_snapshot_before(
            session, exchange, symbol, timeframe, self.period, first_open_time
        )
        if anchor is not None:
            anchor_open_time, anchor_value = anchor
            tail = read_close_range(
                session, exchange, symbol, timeframe, anchor_open_time, open_time
            )
            full = ema_from_anchor(
                anchor_value, [close for _, close in tail], self.period
            )
        else:
            # BOOTSTRAP: todos los cierres hasta la barra pedida (after=None => desde el
            # origen). La semilla es closes[0], el PRIMER cierre de la serie, no el
            # primero de la ventana. La ventana sale de la MISMA tabla con el MISMO
            # filtro de madurez, asi que si hay ventana hay cierres: el origen la
            # contiene entera y full nunca es mas corto que ella.
            origin = read_close_range(
                session, exchange, symbol, timeframe, None, open_time
            )
            closes = [close for _, close in origin]
            full = (closes[0], *ema_from_anchor(closes[0], closes[1:], self.period))
        series = full[-len(window) :]
        write_ema_snapshot(
            session,
            exchange,
            symbol,
            timeframe,
            self.period,
            open_time,
            series[-1],
        )
        return series


@dataclass(frozen=True, slots=True)
class RsiRecursiveSpec:
    """Materializador RECURSIVE de rsi.value: replay ACOTADO desde snapshot (0025).

    RSI de Wilder: el estado de la barra T -- (avg_gain, avg_loss, last_close) -- sale
    del de T-1 suavizando con (avg_prev*(period-1) + nuevo)/period. Materializa la
    ventana SEMBRANDO el estado con el snapshot vigente ANTERIOR a ella
    (read_rsi_snapshot_before) y dando un rsi_step por cada cierre posterior
    (read_close_range). Tras calcular,
    PERSISTE el estado de la barra vigente: es un materializador CON ESTADO, como el de
    cvd y el de ema, e idempotente (ON CONFLICT DO NOTHING).

    EL ESTADO SON TRES VALORES, no uno. last_close esta ahi porque gain[T] = close[T] -
    close[T-1] es un DIFERENCIAL: sin el cierre de la barra ancla, el primer paso del
    replay no se puede dar. Es matematica del indicador, no una comodidad del esquema.

    SIN ancla, el bootstrap arranca DESDE EL ORIGEN, sembrando con rsi_seed en la barra
    `period` (media simple de los primeros `period` cambios, convencion P08b-02). Como
    en ema y por el mismo motivo: el RSI no es anchor-independiente, asi que recortar el
    bootstrap a la ventana daria OTRA serie.

    WARM-UP. Las barras anteriores a la semilla NO tienen RSI. Un materializador
    devuelve tuple[Decimal, ...] y no puede emitir None a media serie, asi que la serie
    sale MAS CORTA que la ventana pedida -- exactamente lo que hacen read_close_window y
    materialize_windowed cuando falta historia --, y el evaluador la trata como
    NOT_EVALUABLE (K3). Si la ventana cae ENTERA en warm-up la serie es (): correcto, no
    es un error. Lo que NUNCA se hace es rellenar: una barra inventada seria un hecho
    falso con aspecto de indicador.

    El GATE de ADR-007: el replay desde CUALQUIER snapshot valido reproduce la cola
    identica BIT A BIT a la del wilder_rsi puro desde el origen, porque rsi_seed y
    rsi_step reutilizan literalmente su aritmetica y su contexto Decimal pinneado.

    period es PARAMETRO DECLARADO (overridable, default 14) y lo propaga el dispatch
    (with_params). Los snapshots NO se cruzan -- period entra en la identidad de
    rsi_snapshot (PK, 0025) --, asi que el ancla de un rsi(7) nunca siembra un rsi(14).
    """

    period: int = RSI_PERIOD_DEFAULT

    def with_params(self, params: Mapping[str, ScalarValue]) -> SourceMaterializer:
        """Copia ligada al period EFECTIVO de la regla (MAT-05 Q2).

        El compilador ya valido nombre y tipo (ParamSpec.value_type=INTEGER) para todo
        override que pase por compile(). Este chequeo queda como ULTIMA linea de defensa
        para quien llame with_params directamente (fuera de un ExecutionPlan compilado):
        un period fuera de dominio (< 1) falla RUIDOSO, no materializa con el default en
        silencio. El mismo dominio que clava la 0025 en su CHECK (period >= 1).
        """
        value = params.get("period")
        if value is None or value.integer_value is None:
            return self
        period = value.integer_value
        if period < 1:
            msg = (
                f"period {period!r} no es un periodo de RSI valido (exige >= 1): no se "
                "materializa (fail-loud)."
            )
            raise UnwiredSourceError(msg)
        return replace(self, period=period)

    def materialize(
        self,
        session: Session,
        exchange: str,
        symbol: str,
        timeframe: str,
        open_time: int,
        history_bars: int,
    ) -> tuple[Decimal, ...]:
        window = read_ohlcv_window(
            session, exchange, symbol, timeframe, open_time, history_bars
        )
        if not window:
            return ()
        first_open_time = window[0].open_time
        anchor = read_rsi_snapshot_before(
            session, exchange, symbol, timeframe, self.period, first_open_time
        )
        values: list[Decimal] = []
        if anchor is not None:
            anchor_open_time, avg_gain, avg_loss, last_close = anchor
            tail = read_close_range(
                session, exchange, symbol, timeframe, anchor_open_time, open_time
            )
            for _, close in tail:
                avg_gain, avg_loss, last_close, value = rsi_step(
                    avg_gain, avg_loss, last_close, close, self.period
                )
                values.append(value)
            if not values:
                # El ancla es anterior al inicio de la ventana y la ventana no esta
                # vacia, asi que el rango siempre trae barras; si no las trajera, el
                # estado no habria avanzado y persistirlo en open_time plantaria un
                # ancla desfasada.
                return ()
        else:
            # BOOTSTRAP: todos los cierres hasta la barra pedida (after=None => desde el
            # origen). La semilla de Wilder cae en la barra `period`, no en el inicio de
            # la ventana. La ventana sale de la MISMA tabla con el MISMO filtro de
            # madurez, asi que el origen la contiene entera.
            origin = read_close_range(
                session, exchange, symbol, timeframe, None, open_time
            )
            closes = [close for _, close in origin]
            seed = rsi_seed(closes, self.period)
            if seed is None:
                # WARM-UP COMPLETO: ni siquiera hay semilla. Serie vacia
                # (NOT_EVALUABLE, K3) y NINGUN snapshot: un ancla sin estado real
                # envenenaria todos los replays posteriores.
                return ()
            avg_gain, avg_loss, last_close, first_value = seed
            values.append(first_value)
            for close in closes[self.period + 1 :]:
                avg_gain, avg_loss, last_close, value = rsi_step(
                    avg_gain, avg_loss, last_close, close, self.period
                )
                values.append(value)
        # Cola de hasta len(window) valores: MENOS si el warm-up se come el principio de
        # la ventana. No se rellena (ver el docstring).
        series = tuple(values[-len(window) :])
        write_rsi_snapshot(
            session,
            exchange,
            symbol,
            timeframe,
            self.period,
            open_time,
            avg_gain,
            avg_loss,
            last_close,
        )
        return series


@dataclass(frozen=True, slots=True)
class MacdRecursiveSpec:
    """Materializador RECURSIVE de macd.*: replay ACOTADO desde snapshot (0026).

    Por dentro el MACD son TRES EMAs encadenadas: ema_fast y ema_slow sobre el cierre,
    ema_signal sobre la diferencia de las dos. Ese trio ES el estado. Materializa la
    ventana SEMBRANDO el estado con el snapshot vigente ANTERIOR a ella
    (read_macd_snapshot_before) y dando un macd_step por cada cierre posterior
    (read_close_range). Tras calcular, PERSISTE el estado de la barra vigente: es un
    materializador CON ESTADO, como los de cvd, ema y rsi, e idempotente (ON CONFLICT
    DO NOTHING).

    UN ESTADO, TRES FUENTES. Un solo macd_step produce las tres salidas; `output` dice
    cual publica ESTA instancia. Las tres entradas del registro comparten el mismo
    estado, el mismo snapshot y el mismo calculo, y por eso pueden materializar la misma
    barra sin estorbarse: escriben la MISMA fila, que el ON CONFLICT DO NOTHING absorbe.
    Recalcular tres veces es el precio del dispatch por SOURCE_ID (MAT-06), que en v5.0
    no tiene cache de evaluacion compartida; lo que NO puede pasar -- y no pasa -- es
    que las tres escriban estados distintos.

    SIN WARM-UP, a diferencia del RSI: el MACD da valor desde la barra 0 (macd[0] == 0,
    invariante de semilla P08b-08), asi que la serie devuelta tiene SIEMPRE tantos
    valores como barras tenga la ventana.

    SIN ancla, el bootstrap arranca DESDE EL ORIGEN, sembrando con macd_seed en el
    primer cierre. Como en ema y rsi y por el mismo motivo: el MACD no es
    anchor-independiente, asi que recortar el bootstrap a la ventana daria OTRA serie.

    El GATE de ADR-007: el replay desde CUALQUIER snapshot valido reproduce las tres
    colas identicas BIT A BIT a las del macd() puro desde el origen, porque macd_seed y
    macd_step no reescriben la recurrencia -- llaman a ema_from_anchor, que ES la de
    ema() -- y hacen las restas bajo el mismo contexto pinneado.

    fast/slow/signal son PARAMETROS DECLARADOS (overridables) y los propaga el dispatch
    (with_params). Los snapshots NO se cruzan -- los tres entran en la identidad de
    macd_snapshot (PK, 0026) --, asi que el ancla de un macd(5,35,5) nunca siembra un
    macd(12,26,9).
    """

    output: MacdOutput
    fast: int = MACD_FAST_DEFAULT
    slow: int = MACD_SLOW_DEFAULT
    signal: int = MACD_SIGNAL_DEFAULT

    def _periodo(
        self, params: Mapping[str, ScalarValue], nombre: str, actual: int
    ) -> int:
        """El valor EFECTIVO de un periodo, validando su dominio (fail-loud).

        Ausente o sin entero -> se conserva el actual (aditividad D7: lo que la regla no
        pide, no cambia). Presente y fuera de dominio -> LANZA: el compilador ya valido
        nombre y tipo para todo override que pase por compile(), asi que esto es la
        ultima linea de defensa de quien llame with_params directamente. Mismo dominio
        que los CHECK de la 0026 (>= 1).
        """
        value = params.get(nombre)
        if value is None or value.integer_value is None:
            return actual
        periodo = value.integer_value
        if periodo < 1:
            msg = (
                f"{nombre} {periodo!r} no es un periodo de MACD valido (exige >= 1): "
                "no se materializa (fail-loud)."
            )
            raise UnwiredSourceError(msg)
        return periodo

    def with_params(self, params: Mapping[str, ScalarValue]) -> SourceMaterializer:
        """Copia ligada a los tres periodos EFECTIVOS de la regla (MAT-05 Q2).

        Solo sustituye los que lleguen; el registro conserva su instancia con los
        defaults (si se mutara, una regla contaminaria a las demas). output NO es
        parametro: es la identidad de la fuente, no algo que una regla pueda pedir.
        """
        return replace(
            self,
            fast=self._periodo(params, "fast", self.fast),
            slow=self._periodo(params, "slow", self.slow),
            signal=self._periodo(params, "signal", self.signal),
        )

    def materialize(
        self,
        session: Session,
        exchange: str,
        symbol: str,
        timeframe: str,
        open_time: int,
        history_bars: int,
    ) -> tuple[Decimal, ...]:
        window = read_ohlcv_window(
            session, exchange, symbol, timeframe, open_time, history_bars
        )
        if not window:
            return ()
        first_open_time = window[0].open_time
        anchor = read_macd_snapshot_before(
            session,
            exchange,
            symbol,
            timeframe,
            self.fast,
            self.slow,
            self.signal,
            first_open_time,
        )
        values: list[Decimal] = []
        if anchor is not None:
            anchor_open_time, ema_fast, ema_slow, ema_signal = anchor
            tail = read_close_range(
                session, exchange, symbol, timeframe, anchor_open_time, open_time
            )
            for _, close in tail:
                state = macd_step(
                    ema_fast,
                    ema_slow,
                    ema_signal,
                    close,
                    self.fast,
                    self.slow,
                    self.signal,
                )
                ema_fast, ema_slow, ema_signal = state[0], state[1], state[2]
                values.append(select_output(state, self.output))
            if not values:
                # El ancla es anterior al inicio de la ventana y la ventana no esta
                # vacia, asi que el rango siempre trae barras; si no las trajera, el
                # estado no habria avanzado y persistirlo en open_time plantaria un
                # ancla desfasada.
                return ()
        else:
            # BOOTSTRAP: todos los cierres hasta la barra pedida (after=None => desde el
            # origen). La semilla cae en la barra 0, no en el inicio de la ventana. La
            # ventana sale de la MISMA tabla con el MISMO filtro de madurez, asi que el
            # origen la contiene entera.
            origin = read_close_range(
                session, exchange, symbol, timeframe, None, open_time
            )
            closes = [close for _, close in origin]
            if not closes:
                return ()
            state = macd_seed(closes[0])
            ema_fast, ema_slow, ema_signal = state[0], state[1], state[2]
            values.append(select_output(state, self.output))
            for close in closes[1:]:
                state = macd_step(
                    ema_fast,
                    ema_slow,
                    ema_signal,
                    close,
                    self.fast,
                    self.slow,
                    self.signal,
                )
                ema_fast, ema_slow, ema_signal = state[0], state[1], state[2]
                values.append(select_output(state, self.output))
        series = tuple(values[-len(window) :])
        write_macd_snapshot(
            session,
            exchange,
            symbol,
            timeframe,
            self.fast,
            self.slow,
            self.signal,
            open_time,
            ema_fast,
            ema_slow,
            ema_signal,
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


def _candle_open(candle: CandleOHLCV) -> Decimal:
    return candle.open


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
    CANDLE_OPEN_SOURCE_ID: CandlePointLocalSpec(extract=_candle_open),
    VOLUME_RATIO_VS_AVG_SOURCE_ID: CandleWindowedSpec(
        transform=_volume_ratio_vs_avg, window_bars=LOOKBACK_DEFAULT + 1
    ),
    VWAP_VALUE_SOURCE_ID: CandleWindowedSpec(
        transform=_vwap_value, window_bars=N_CANDLES_DEFAULT
    ),
    VWAP_DISTANCE_PCT_SOURCE_ID: CandleWindowedSpec(
        transform=_vwap_distance_pct, window_bars=N_CANDLES_DEFAULT
    ),
    SWING_HIGH_SOURCE_ID: SwingWindowedSpec(kind=PivotKind.HIGH),
    SWING_LOW_SOURCE_ID: SwingWindowedSpec(kind=PivotKind.LOW),
    EMA_SOURCE_ID: EmaRecursiveSpec(),
    RSI_SOURCE_ID: RsiRecursiveSpec(),
    MACD_LINE_SOURCE_ID: MacdRecursiveSpec(output=MacdOutput.LINE),
    MACD_SIGNAL_SOURCE_ID: MacdRecursiveSpec(output=MacdOutput.SIGNAL),
    MACD_HISTOGRAM_SOURCE_ID: MacdRecursiveSpec(output=MacdOutput.HISTOGRAM),
    FIB_NEAREST_LEVEL_SOURCE_ID: FibRecursiveSpec(output=FibOutput.NEAREST_LEVEL),
    FIB_LEVEL_PCT_SOURCE_ID: FibRecursiveSpec(output=FibOutput.LEVEL_PCT),
}
