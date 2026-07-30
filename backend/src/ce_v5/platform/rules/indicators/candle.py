"""candle.* -- familia de fuentes descriptivas deterministas sobre la vela.

Re-expresion pura en v5 del CandleEngine de v4 (paridad de RESULTADO/SEMANTICA,
sin engines). Dictamen Central P08b-10 (D1..D5):
  - Anatomia = TRES fuentes escalares separadas: body_pct, upper_shadow_pct,
    lower_shadow_pct (source_id -> tuple[Decimal]).
  - candle.direction, candle.shadow_signal, candle.pullback_moment: fuentes
    CATEGORICAS (un estado por barra).
  - candle.new_high / candle.new_low: booleanas por barra (param lookback).
  - NO existe candle.pivot: los consumidores usan swing.* sobre high/low (D2).
  - Decimal EXACTO en la fuente; el redondeo a 2 decimales de v4 era
    PRESENTACION, no dato (D3). Aritmetica en contexto Decimal pinned.
  - Defaults de paridad como parametros (D4): lookback=20, shadow_ratio=2,
    doji=10% del rango, ventana pullback=8.
  - volume_confirm queda FUERA (composicion hacia volume.*, D5).

Estas fuentes son DESCRIPTIVAS (que forma tiene la vela), NO predictivas: no
llevan AHP ni pre-registro. El umbral shadow_ratio es DEFINITORIO (que ES un
hammer, por v4), no predictivo.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import Enum

from source.datasource import (
    DataSourceDeclaration,
    HistoryUnit,
    MemoryModel,
    Servibility,
    SharingScope,
    SourceType,
)
from source.families.market import Timeframe
from source.rules.scalar import ScalarType

CANDLE_FORMULA_VERSION = 1

# Defaults de paridad v4 (parametros, no hardcode).
_DEFAULT_LOOKBACK = 20
_DEFAULT_SHADOW_RATIO = Decimal(2)
_DEFAULT_DOJI_RATIO = Decimal("0.10")
_DEFAULT_PULLBACK_WINDOW = 8

_PREC = 34
_HUNDRED = Decimal(100)


class Direction(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class ShadowSignal(Enum):
    HAMMER = "hammer"
    SHOOTING_STAR = "shooting_star"
    NONE = "none"


class Pullback(Enum):
    M1_BULL = "m1_bull"
    M1_BEAR = "m1_bear"
    M2_BULL = "m2_bull"
    M2_BEAR = "m2_bear"
    M3_BULL = "m3_bull"
    M3_BEAR = "m3_bear"
    NONE = "none"


def _check_ohlc(
    opens: Sequence[Decimal],
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
) -> None:
    if not (len(opens) == len(highs) == len(lows) == len(closes)):
        raise ValueError("open/high/low/close deben tener la misma longitud")


def _pct_series(
    opens: Sequence[Decimal],
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    component: str,
) -> tuple[Decimal, ...]:
    _check_ohlc(opens, highs, lows, closes)
    out: list[Decimal] = []
    with localcontext() as ctx:
        ctx.prec = _PREC
        ctx.rounding = ROUND_HALF_EVEN
        for o, h, low, c in zip(opens, highs, lows, closes, strict=True):
            rng = h - low
            if rng <= 0:
                out.append(Decimal(0))
                continue
            if component == "body":
                num = abs(c - o)
            elif component == "upper":
                num = h - max(o, c)
            else:  # "lower"
                num = min(o, c) - low
            out.append(num / rng * _HUNDRED)
    return tuple(out)


def body_pct(
    opens: Sequence[Decimal],
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
) -> tuple[Decimal, ...]:
    """Cuerpo / rango * 100 por barra (Decimal exacto en contexto pinned)."""
    return _pct_series(opens, highs, lows, closes, "body")


def upper_shadow_pct(
    opens: Sequence[Decimal],
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
) -> tuple[Decimal, ...]:
    """Sombra superior / rango * 100 por barra."""
    return _pct_series(opens, highs, lows, closes, "upper")


def lower_shadow_pct(
    opens: Sequence[Decimal],
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
) -> tuple[Decimal, ...]:
    """Sombra inferior / rango * 100 por barra."""
    return _pct_series(opens, highs, lows, closes, "lower")


def direction(
    opens: Sequence[Decimal],
    closes: Sequence[Decimal],
) -> tuple[Direction, ...]:
    """BULLISH si close>open, BEARISH si close<open, NEUTRAL si iguales."""
    if len(opens) != len(closes):
        raise ValueError("open/close deben tener la misma longitud")
    out: list[Direction] = []
    for o, c in zip(opens, closes, strict=True):
        if c > o:
            out.append(Direction.BULLISH)
        elif c < o:
            out.append(Direction.BEARISH)
        else:
            out.append(Direction.NEUTRAL)
    return tuple(out)


def new_high(
    highs: Sequence[Decimal],
    lookback: int = _DEFAULT_LOOKBACK,
) -> tuple[bool, ...]:
    """True si high[i] supera ESTRICTAMENTE el max de las lookback barras
    ANTERIORES (excluye la barra actual). Sin historia previa -> False."""
    if lookback < 1:
        raise ValueError("lookback debe ser >= 1")
    out: list[bool] = []
    for i in range(len(highs)):
        window = highs[max(0, i - lookback) : i]
        out.append(bool(window) and highs[i] > max(window))
    return tuple(out)


def new_low(
    lows: Sequence[Decimal],
    lookback: int = _DEFAULT_LOOKBACK,
) -> tuple[bool, ...]:
    """True si low[i] cae ESTRICTAMENTE por debajo del min de las lookback
    barras ANTERIORES (excluye la barra actual). Sin historia previa -> False."""
    if lookback < 1:
        raise ValueError("lookback debe ser >= 1")
    out: list[bool] = []
    for i in range(len(lows)):
        window = lows[max(0, i - lookback) : i]
        out.append(bool(window) and lows[i] < min(window))
    return tuple(out)


def shadow_signal(
    opens: Sequence[Decimal],
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    shadow_ratio: Decimal = _DEFAULT_SHADOW_RATIO,
) -> tuple[ShadowSignal, ...]:
    """HAMMER: sombra_inf > ratio*cuerpo Y sombra_sup < cuerpo.
    SHOOTING_STAR: sombra_sup > ratio*cuerpo Y sombra_inf < cuerpo.
    Requiere cuerpo>0 (dojis -> NONE)."""
    _check_ohlc(opens, highs, lows, closes)
    if shadow_ratio < 0:
        raise ValueError("shadow_ratio debe ser >= 0")
    out: list[ShadowSignal] = []
    with localcontext() as ctx:
        ctx.prec = _PREC
        ctx.rounding = ROUND_HALF_EVEN
        for o, h, low, c in zip(opens, highs, lows, closes, strict=True):
            body = abs(c - o)
            if body <= 0:
                out.append(ShadowSignal.NONE)
                continue
            upper = h - max(o, c)
            lower = min(o, c) - low
            threshold = shadow_ratio * body
            if lower > threshold and upper < body:
                out.append(ShadowSignal.HAMMER)
            elif upper > threshold and lower < body:
                out.append(ShadowSignal.SHOOTING_STAR)
            else:
                out.append(ShadowSignal.NONE)
    return tuple(out)


def _pullback_at(
    opens: Sequence[Decimal],
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    end: int,
    window: int,
    doji_ratio: Decimal,
) -> Pullback:
    """Clasifica el pullback en la barra `end` usando las `window` barras que
    terminan en `end` (incluida). Replica la semantica de v4 sin su rama
    inalcanzable: como los segmentos comprimidos alternan direccion,
    n_segs>=3 -> M3, ==2 -> M2, ==1 -> M1."""
    prefix_len = end + 1
    if prefix_len < 4:
        return Pullback.NONE

    start = max(0, end - window + 1)
    labels: list[str] = []
    for i in range(start, end + 1):
        rng = highs[i] - lows[i]
        body = abs(closes[i] - opens[i])
        if rng > 0 and body < doji_ratio * rng:
            continue  # doji
        labels.append("U" if closes[i] > opens[i] else "D")

    if len(labels) < 3:
        return Pullback.NONE

    segments: list[str] = []
    for lab in labels:
        if not segments or segments[-1] != lab:
            segments.append(lab)

    n_segs = len(segments)
    last = segments[-1]
    if n_segs >= 3:
        return Pullback.M3_BULL if last == "U" else Pullback.M3_BEAR
    if n_segs == 2:
        # dir = direccion del impulso (primer segmento del par)
        return Pullback.M2_BULL if segments[0] == "U" else Pullback.M2_BEAR
    # n_segs == 1
    return Pullback.M1_BULL if last == "U" else Pullback.M1_BEAR


def pullback_moment(
    opens: Sequence[Decimal],
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    window: int = _DEFAULT_PULLBACK_WINDOW,
    doji_ratio: Decimal = _DEFAULT_DOJI_RATIO,
) -> tuple[Pullback, ...]:
    """Momento del pullback (M1/M2/M3 x bull/bear, o NONE) por barra, usando la
    ventana de `window` barras que termina en cada barra."""
    _check_ohlc(opens, highs, lows, closes)
    if window < 1:
        raise ValueError("window debe ser >= 1")
    if doji_ratio < 0:
        raise ValueError("doji_ratio debe ser >= 0")
    return tuple(
        _pullback_at(opens, highs, lows, closes, i, window, doji_ratio)
        for i in range(len(opens))
    )


CANDLE_BODY_PCT_SOURCE_ID = "candle.body_pct"
CANDLE_UPPER_SHADOW_PCT_SOURCE_ID = "candle.upper_shadow_pct"
CANDLE_LOWER_SHADOW_PCT_SOURCE_ID = "candle.lower_shadow_pct"


def _anatomy_declaration(source_id: str) -> DataSourceDeclaration:
    """POINT_LOCAL: el pct de anatomia de la barra T depende SOLO de la vela T."""
    return DataSourceDeclaration(
        source_id=source_id,
        source_type=SourceType.OBSERVABLE,
        servibility=Servibility.CONTINUOUS,
        memory_model=MemoryModel.POINT_LOCAL,
        value_type=ScalarType.DECIMAL,
        evaluation_contexts=tuple(tf.value for tf in Timeframe),
        history_units=(HistoryUnit.BARS,),
        shared_evaluation=True,
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=("exchange", "symbol", "timeframe"),
    )


def declarations() -> tuple[DataSourceDeclaration, ...]:
    """Fuentes de anatomia SIEMPRE-Decimal (LOTE 1a). Las categoricas/booleanas de
    candle.* quedan DIFERIDAS (D1) y NO se declaran aun."""
    return (
        _anatomy_declaration(CANDLE_BODY_PCT_SOURCE_ID),
        _anatomy_declaration(CANDLE_UPPER_SHADOW_PCT_SOURCE_ID),
        _anatomy_declaration(CANDLE_LOWER_SHADOW_PCT_SOURCE_ID),
    )
