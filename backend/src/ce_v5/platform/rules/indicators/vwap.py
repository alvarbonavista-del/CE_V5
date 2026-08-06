"""vwap.* -- familia de fuentes sobre el VWAP de ventana movil.

Re-expresion pura en v5 del VWAPEngine de v4 (paridad de RESULTADO/SEMANTICA,
sin engines). Dictamen Central P08b-12 (D1..D6 + A/B/C):
  - Familia: vwap.value (escalar), vwap.distance_pct (escalar),
    vwap.side (categorica), vwap.direction (categorica).
  - near_vwap NO existe: el umbral (0.5%) es de DECISION -> vive en la Rule de
    pullback-a-VWAP (principio DEC-UMBRAL-LOCUS). La fuente expone distance_pct
    EXACTO (D2).
  - H/L/C/V son basicos servibles de P07; current_price = candle.close (D4).
  - Decimal EXACTO; los round() de v4 eran presentacion (C). Aritmetica pinned.
  - Defaults de paridad como parametros (D5): n_candles=20; side empate -> ABOVE
    (via >=); vwap<=0 -> distance=0; vol_total=0 -> vwap None; direction sin
    previo -> None.
  - Descriptivas deterministas: SIN AHP (D6).

Paridad fijada:
  A. VWAP de VENTANA MOVIL de N velas (NO anclado a sesion).
  B. Precio tipico = HLC3 = (High+Low+Close)/3.
  C. Decimal exacto en la fuente.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import Enum

from source.datasource import (
    DataSourceDeclaration,
    HistoryUnit,
    MemoryModel,
    ParamSpec,
    Servibility,
    SharingScope,
    SourceType,
)
from source.families.market import Timeframe
from source.rules.scalar import ScalarType, ScalarValue

VWAP_FORMULA_VERSION = 1

N_CANDLES_DEFAULT = 20
_PREC = 34
_HUNDRED = Decimal(100)
_THREE = Decimal(3)


class VwapSide(Enum):
    ABOVE = "above"
    BELOW = "below"


class VwapDirection(Enum):
    UP = "up"
    DOWN = "down"


def _check(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    volumes: Sequence[Decimal],
    n: int,
) -> None:
    if not (len(highs) == len(lows) == len(closes) == len(volumes)):
        raise ValueError("high/low/close/volume deben tener la misma longitud")
    if n < 1:
        raise ValueError("n_candles debe ser >= 1")


def _vwap_series(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    volumes: Sequence[Decimal],
    n: int,
) -> list[Decimal | None]:
    """VWAP de ventana movil de N velas por barra (None si vol_total==0).

    VWAP = SUM(HLC3 * vol) / SUM(vol) sobre las N barras que terminan en cada
    barra; HLC3 = (High+Low+Close)/3.
    """
    out: list[Decimal | None] = []
    for i in range(len(highs)):
        start = max(0, i - n + 1)
        total_tpv = Decimal(0)
        total_vol = Decimal(0)
        for j in range(start, i + 1):
            tp = (highs[j] + lows[j] + closes[j]) / _THREE
            total_tpv += tp * volumes[j]
            total_vol += volumes[j]
        out.append(None if total_vol == 0 else total_tpv / total_vol)
    return out


def value(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    volumes: Sequence[Decimal],
    n_candles: int = N_CANDLES_DEFAULT,
) -> tuple[Decimal | None, ...]:
    """VWAP de ventana movil de N velas por barra (None si vol_total==0)."""
    _check(highs, lows, closes, volumes, n_candles)
    with localcontext() as ctx:
        ctx.prec = _PREC
        ctx.rounding = ROUND_HALF_EVEN
        return tuple(_vwap_series(highs, lows, closes, volumes, n_candles))


def distance_pct(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    volumes: Sequence[Decimal],
    n_candles: int = N_CANDLES_DEFAULT,
) -> tuple[Decimal | None, ...]:
    """|close - vwap| / vwap * 100 por barra. vwap None -> None; vwap<=0 -> 0."""
    _check(highs, lows, closes, volumes, n_candles)
    out: list[Decimal | None] = []
    with localcontext() as ctx:
        ctx.prec = _PREC
        ctx.rounding = ROUND_HALF_EVEN
        vwap = _vwap_series(highs, lows, closes, volumes, n_candles)
        for i in range(len(closes)):
            v = vwap[i]
            if v is None:
                out.append(None)
            elif v > 0:
                out.append(abs(closes[i] - v) / v * _HUNDRED)
            else:
                out.append(Decimal(0))
    return tuple(out)


def side(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    volumes: Sequence[Decimal],
    n_candles: int = N_CANDLES_DEFAULT,
) -> tuple[VwapSide | None, ...]:
    """ABOVE si close >= vwap, BELOW si no. vwap None -> None."""
    _check(highs, lows, closes, volumes, n_candles)
    with localcontext() as ctx:
        ctx.prec = _PREC
        ctx.rounding = ROUND_HALF_EVEN
        vwap = _vwap_series(highs, lows, closes, volumes, n_candles)
    out: list[VwapSide | None] = []
    for i in range(len(closes)):
        v = vwap[i]
        if v is None:
            out.append(None)
        elif closes[i] >= v:
            out.append(VwapSide.ABOVE)
        else:
            out.append(VwapSide.BELOW)
    return tuple(out)


def direction(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    volumes: Sequence[Decimal],
    n_candles: int = N_CANDLES_DEFAULT,
) -> tuple[VwapDirection | None, ...]:
    """UP si vwap[i] > vwap[i-1], DOWN si no (empate -> DOWN). Sin previo o vwap
    None -> None."""
    _check(highs, lows, closes, volumes, n_candles)
    with localcontext() as ctx:
        ctx.prec = _PREC
        ctx.rounding = ROUND_HALF_EVEN
        vwap = _vwap_series(highs, lows, closes, volumes, n_candles)
    out: list[VwapDirection | None] = []
    for i in range(len(closes)):
        cur = vwap[i]
        prev = vwap[i - 1] if i > 0 else None
        if cur is None or prev is None:
            out.append(None)
        elif cur > prev:
            out.append(VwapDirection.UP)
        else:
            out.append(VwapDirection.DOWN)
    return tuple(out)


VWAP_VALUE_SOURCE_ID = "vwap.value"
VWAP_DISTANCE_PCT_SOURCE_ID = "vwap.distance_pct"


def _vwap_windowed_declaration(source_id: str) -> DataSourceDeclaration:
    """WINDOWED: el VWAP (o su distancia) de la barra T sale de la ventana de N velas
    [T-N+1, T]; no depende de su valor previo. n_candles es PARAM en la cache_key."""
    return DataSourceDeclaration(
        source_id=source_id,
        source_type=SourceType.OBSERVABLE,
        servibility=Servibility.CONTINUOUS,
        memory_model=MemoryModel.WINDOWED,
        value_type=ScalarType.DECIMAL,
        evaluation_contexts=tuple(tf.value for tf in Timeframe),
        history_units=(HistoryUnit.BARS,),
        params=(
            ParamSpec(
                name="n_candles",
                value_type=ScalarType.INTEGER,
                default=ScalarValue(
                    scalar_type=ScalarType.INTEGER,
                    integer_value=N_CANDLES_DEFAULT,
                ),
            ),
        ),
        shared_evaluation=True,
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=("exchange", "symbol", "timeframe", "n_candles"),
    )


def declarations() -> tuple[DataSourceDeclaration, ...]:
    """vwap.value y vwap.distance_pct (LOTE 1b, WINDOWED). side/direction categoricas
    quedan DIFERIDAS (D1) y NO se declaran aun."""
    return (
        _vwap_windowed_declaration(VWAP_VALUE_SOURCE_ID),
        _vwap_windowed_declaration(VWAP_DISTANCE_PCT_SOURCE_ID),
    )
