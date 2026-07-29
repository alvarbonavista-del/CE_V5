"""No-trade / toxicidad footprint+candle (notrade.*, F7, P08c). Nucleo determinista.

Implementa el detector del PRE-REGISTRO AHP NO-TRADE (firmado 2026-07-29): un score de
TOXICIDAD del entorno (0-100). Alto = mercado caotico, NO operar; bajo = limpio. NO es
direccional: es un GATE que MODULA las senales de entrada (no una entrada).

ENTREGA EN DOS PARTES + L2 DIFERIDO (dictamen Central, opcion b): el score de v4 tiene
tres bloques -- Footprint Inefficiency (40%), L2 Instability (35%), Flow Dislocation
(25%). Este modulo construye AHORA los dos que salen de footprint+vela (FP + Flow = 65%
del maximo); el bloque L2 (35%) se DECLARA pero contribuye 0 hasta que exista l2.*.
Hasta entonces el score es un LIMITE INFERIOR (L2 solo anade toxicidad), "toxic" es
inalcanzable y el estado es PROVISIONAL. La DataSourceDeclaration se DIFIERE hasta que
candle.* (OHLC) sea servible, como climax.

PESOS ABSOLUTOS, NO renormalizados: FP 40 + Flow 25 = 65 max; el 35 de L2 queda
RESERVADO. Cuando L2 entre se SUMA sin rescalar (aditividad; no rompe el golden).

DETECTOR, no calibrador: la LOGICA con las SEMILLAS [PARIDAD v4] como PARAMETRO
(NoTradeParams). Los sub-pesos van FIJOS en paridad v4; se calibran solo las BANDAS de
estado y K. Calibracion (walk-forward) DIFERIDA con dueno P08c.

MEMORIA WINDOWED: cada feature se normaliza por MIN-MAX sobre una ventana rodante
acotada (NORM_WINDOW); el score de la barra T se recomputa por ventana. NO-CONFORME
para correccion en v5.0 (enum MemoryModel).

DECIMAL PINNED: todo en Decimal bajo el contexto por defecto; sin round en la fuente
(round = presentacion). eps 1e-9 es artefacto anti-division-por-cero de v4 (parametro).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# Version de la formula. El golden se ata a esta cadena: si la formula cambia, sube
# la version y se regenera. Alimentara el formula_version de la declaracion diferida.
NOTRADE_FORMULA_VERSION = "notrade.v1"

# Semillas [PARIDAD v4] (engines/l1/no_trade_engine.py). NO son verdades: punto de
# partida parametrizado; el valor final lo fija la calibracion (AHP), diferida.
_FP_BLOCK_WEIGHT = Decimal("40")  # peso del bloque Footprint Inefficiency
_FLOW_BLOCK_WEIGHT = Decimal("25")  # peso del bloque Flow Dislocation
_L2_BLOCK_WEIGHT = Decimal("35")  # peso RESERVADO de L2 (diferido, contribuye 0)

# Sub-pesos FP (lineas 316-321) y Flow (lineas 381-385) de v4. FIJOS en paridad.
_W_DELTA_INEFF = Decimal("0.35")
_W_VOL_INEFF = Decimal("0.25")
_W_DELTA_FLIP = Decimal("0.20")
_W_DIVERGENCE = Decimal("0.20")
_W_MOVE_NO_DELTA = Decimal("0.40")
_W_DELTA_STALL = Decimal("0.40")
_W_FAILED_BREAK = Decimal("0.20")

# Bandas de estado (v4 lineas 63-88) y modulador (linea 162).
_STATE_SAFE_MAX = Decimal("30")
_STATE_CAUTION_MAX = Decimal("60")
_STATE_NO_TRADE_MAX = Decimal("80")
_NET_EDGE_K = Decimal("50")

# Ventanas [PARIDAD v4].
NORM_WINDOW = 100  # ventana rodante de la normalizacion min-max (linea 60)
MIN_CANDLES = 5  # minimo de velas para evaluar (linea 59)
DELTA_FLIP_WINDOW = 5  # velas del calculo de delta_flip_rate (linea 294)
FAILED_BREAK_WINDOW = 10  # velas del proxy failed_break (linea 363)

_EPS = Decimal("1e-9")  # anti-division-por-cero (parity v4)
_SCORE_MAX = Decimal("100")
_ZERO = Decimal(0)
_ONE = Decimal(1)


class NoTradeState(StrEnum):
    """Clasificacion de toxicidad del entorno [PARIDAD v4]."""

    SAFE = "safe"
    CAUTION = "caution"
    NO_TRADE = "no_trade"
    TOXIC = "toxic"


@dataclass(frozen=True, slots=True)
class NoTradeParams:
    """Umbrales/pesos como PARAMETRO (semillas [PARIDAD v4] por defecto).

    Sub-pesos FIJOS en paridad (decision registrada: calibrar solo bandas y K reduce
    grados de libertad y sobreajuste). La calibracion (diferida) sustituye lo que toque.
    """

    fp_block_weight: Decimal = _FP_BLOCK_WEIGHT
    flow_block_weight: Decimal = _FLOW_BLOCK_WEIGHT
    l2_block_weight: Decimal = _L2_BLOCK_WEIGHT
    w_delta_ineff: Decimal = _W_DELTA_INEFF
    w_vol_ineff: Decimal = _W_VOL_INEFF
    w_delta_flip: Decimal = _W_DELTA_FLIP
    w_divergence: Decimal = _W_DIVERGENCE
    w_move_no_delta: Decimal = _W_MOVE_NO_DELTA
    w_delta_stall: Decimal = _W_DELTA_STALL
    w_failed_break: Decimal = _W_FAILED_BREAK
    state_safe_max: Decimal = _STATE_SAFE_MAX
    state_caution_max: Decimal = _STATE_CAUTION_MAX
    state_no_trade_max: Decimal = _STATE_NO_TRADE_MAX
    net_edge_k: Decimal = _NET_EDGE_K
    norm_window: int = NORM_WINDOW
    min_candles: int = MIN_CANDLES
    delta_flip_window: int = DELTA_FLIP_WINDOW
    failed_break_window: int = FAILED_BREAK_WINDOW
    eps: Decimal = _EPS


_DEFAULT_PARAMS = NoTradeParams()


@dataclass(frozen=True, slots=True)
class NoTradeCandle:
    """Vela de entrada: delta y volumen agresor (footprint) + OHLC (candle.*)."""

    delta: Decimal
    volume: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    open: Decimal


@dataclass(frozen=True, slots=True)
class NoTradeSignal:
    """Veredicto de la ULTIMA vela de la ventana.

    no_trade_score en [0,100] (max 65 hasta que L2 entre). footprint_ineff (0-40) y
    flow_dislocation (0-25) son los bloques activos; l2_instability es 0 (DIFERIDO).
    state clasifica la toxicidad (PROVISIONAL sin L2).
    """

    no_trade_score: Decimal
    footprint_ineff: Decimal
    flow_dislocation: Decimal
    l2_instability: Decimal
    state: NoTradeState


def _norm(values: Sequence[Decimal], value: Decimal, eps: Decimal) -> Decimal:
    """Min-max de value sobre values. [PARIDAD v4 _norm_last, lineas 71-78].

    0.5 si hay < 2 muestras; (value - min) / (max - min + eps), recortado a [0,1].
    """
    if len(values) < 2:
        return Decimal("0.5")
    minimum = min(values)
    maximum = max(values)
    ratio = (value - minimum) / (maximum - minimum + eps)
    if ratio < _ZERO:
        return _ZERO
    if ratio > _ONE:
        return _ONE
    return ratio


def _delta_flip_rate(
    window: Sequence[NoTradeCandle], index: int, flip_window: int
) -> Decimal:
    """Frecuencia de cambios de signo del delta en las ultimas flip_window velas."""
    start = max(0, index - (flip_window - 1))
    recent = window[start : index + 1]
    flips = sum(
        1
        for k in range(1, len(recent))
        if recent[k].delta * recent[k - 1].delta < _ZERO
    )
    denom = max(len(recent) - 1, 1)
    return Decimal(flips) / Decimal(denom)


def _failed_break(
    window: Sequence[NoTradeCandle], index: int, break_window: int
) -> Decimal:
    """Proxy booleano de rotura fallida en break_window velas (v4 363-371)."""
    start = max(0, index - (break_window - 1))
    window_slice = window[start : index + 1]
    if len(window_slice) < 2:
        return _ZERO
    current = window[index]
    previous = window[index - 1]
    prev_high = max(candle.high for candle in window_slice[:-1])
    prev_low = min(candle.low for candle in window_slice[:-1])
    broke_high = current.high > prev_high and current.close < previous.high
    broke_low = current.low < prev_low and current.close > previous.low
    return _ONE if (broke_high or broke_low) else _ZERO


def _feature_columns(
    window: Sequence[NoTradeCandle], params: NoTradeParams
) -> dict[str, list[Decimal]]:
    """Calcula, por vela de la ventana, las 7 features activas (FP + Flow)."""
    columns: dict[str, list[Decimal]] = {
        "delta_ineff": [],
        "vol_ineff": [],
        "delta_flip": [],
        "divergence": [],
        "move_no_delta": [],
        "delta_stall": [],
        "failed_break": [],
    }
    for index, candle in enumerate(window):
        span = candle.high - candle.low
        if span <= _ZERO:
            span = params.eps
        volume = candle.volume if candle.volume > _ZERO else params.eps
        price_change = candle.close - candle.open
        abs_delta = abs(candle.delta)
        columns["delta_ineff"].append(abs_delta / span)
        columns["vol_ineff"].append(volume / span)
        columns["delta_flip"].append(
            _delta_flip_rate(window, index, params.delta_flip_window)
        )
        columns["divergence"].append(
            abs(candle.delta * price_change) if price_change != _ZERO else _ZERO
        )
        columns["move_no_delta"].append(abs(price_change) / (abs_delta + params.eps))
        columns["delta_stall"].append(abs_delta / (abs(price_change) + params.eps))
        columns["failed_break"].append(
            _failed_break(window, index, params.failed_break_window)
        )
    return columns


def _state(score: Decimal, params: NoTradeParams) -> NoTradeState:
    """Estado de toxicidad por bandas [PARIDAD v4]."""
    if score < params.state_safe_max:
        return NoTradeState.SAFE
    if score < params.state_caution_max:
        return NoTradeState.CAUTION
    if score < params.state_no_trade_max:
        return NoTradeState.NO_TRADE
    return NoTradeState.TOXIC


def evaluate_no_trade(
    window: Sequence[NoTradeCandle],
    params: NoTradeParams = _DEFAULT_PARAMS,
) -> NoTradeSignal | None:
    """No-trade score de la ULTIMA vela de la ventana. None si faltan velas (< min).

    Bloques activos (FP + Flow) con normalizacion min-max sobre la ventana. l2 = 0
    (DIFERIDO, peso 35 reservado). Score = FP + Flow + L2, en [0,100] (max 65 sin L2).
    """
    if len(window) < params.min_candles:
        return None
    columns = _feature_columns(window, params)

    def norm_last(name: str) -> Decimal:
        values = columns[name]
        return _norm(values, values[-1], params.eps)

    footprint_ineff = (
        norm_last("delta_ineff") * params.w_delta_ineff
        + norm_last("vol_ineff") * params.w_vol_ineff
        + norm_last("delta_flip") * params.w_delta_flip
        + norm_last("divergence") * params.w_divergence
    ) * params.fp_block_weight
    flow_dislocation = (
        norm_last("move_no_delta") * params.w_move_no_delta
        + norm_last("delta_stall") * params.w_delta_stall
        + norm_last("failed_break") * params.w_failed_break
    ) * params.flow_block_weight
    l2_instability = _ZERO  # DIFERIDO hasta l2.*; peso params.l2_block_weight reservado
    score = footprint_ineff + flow_dislocation + l2_instability
    if score < _ZERO:
        score = _ZERO
    elif score > _SCORE_MAX:
        score = _SCORE_MAX
    return NoTradeSignal(
        no_trade_score=score,
        footprint_ineff=footprint_ineff,
        flow_dislocation=flow_dislocation,
        l2_instability=l2_instability,
        state=_state(score, params),
    )


def net_edge(
    orderflow_score: Decimal,
    no_trade_score: Decimal,
    params: NoTradeParams = _DEFAULT_PARAMS,
) -> Decimal:
    """Modulador [PARIDAD v4, linea 162]: orderflow_score * exp(-no_trade_score / K).

    Helper: toma el orderflow score (fuente hermana) y lo atenua por la toxicidad. NO es
    una fuente servible independiente. Recortado a [0,100].
    """
    modulated = orderflow_score * (-no_trade_score / params.net_edge_k).exp()
    if modulated < _ZERO:
        return _ZERO
    if modulated > _SCORE_MAX:
        return _SCORE_MAX
    return modulated
