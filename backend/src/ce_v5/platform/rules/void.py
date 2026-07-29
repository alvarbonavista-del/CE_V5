"""Void de liquidez footprint/candle-based (void.*, F7, P08c). Nucleo determinista.

Implementa el detector del PRE-REGISTRO AHP VOID (firmado 2026-07-29): una RUPTURA
FALSA a traves de un LVN (nivel de bajo volumen). El precio cruza el hueco y, si VUELVE
rapido al lado original (sin alejarse de verdad), esa vuelta ("snap") firma que la
ruptura era falsa y se espera un giro. Alcance v5.0 = via vp.lvn (niveles) + el close de
la vela; SIN libro L2, SIN estado no acotado.

DETECTOR, no calibrador: aqui vive la LOGICA (dispara bien DADO un umbral) con las
SEMILLAS [PARIDAD v4] como PARAMETRO (VoidParams). El numero final de cada umbral
lo fija la calibracion (walk-forward sobre corpus), DIFERIDA con dueno P08c.

MEMORIA WINDOWED, NO RECURSIVE: el veredicto de la vela T sale de una VENTANA ACOTADA de
a lo sumo R+1 velas (todo cruce caduca en R=5). La "watchlist" de v4 es solo la
implementacion en streaming de esa semantica de ventana; se recomputa por ventana como
vp.*. En v5.0 queda NO-CONFORME para correccion (enum MemoryModel).

ENTREGA EN DOS PARTES (dictamen Central, H1 opcion A): este modulo entrega AHORA el
nucleo puro (deteccion + etiquetado) + tests. La DataSourceDeclaration void.*
(consumes vp.lvn + market.close, ambos ya existentes) se hornea en un micro-paso
posterior, una vez fijada la forma servible y el helper de cruce sobre ventana.

VARIABLES (por vela cerrada, un flujo). LVN = nivel de vp.lvn; close de la vela. El
cruce y el retorno se derivan de la ventana de close contra el LVN (no hay evento
externo en v5). De donde salen vp.lvn y close es materializacion (CE-14).

FORMA SERVIBLE (para la declaracion diferida): void.snap_bullish / void.snap_bearish,
Decimal en {0,1} (1 si el snap de esa direccion confirma en T). BINARIA: v4 no da score
graduado; un score derivado de candles_to_snap seria ADICION sobre v4 y se ELEVARIA.

DECIMAL PINNED: todo en Decimal bajo el contexto por defecto; solo comparaciones y
multiplicaciones (nunca division: el FAR usa |close-LVN| > LVN*far, no divide), asi
que el veredicto es exacto bit a bit. Sin round en la fuente (round = presentacion).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# Version de la logica de deteccion. El golden se ata a esta cadena: si cambia,
# la version sube y el golden se regenera. Alimentara el formula_version de la
# DataSourceDeclaration diferida (junto con las semillas, todas en la cache_key).
VOID_FORMULA_VERSION = "void.v1"

# Semillas [PARIDAD v4] (engines/l1/pivots/liquidity_void_engine.py). NO son verdades:
# punto de partida parametrizado; su valor final lo fija la calibracion (AHP), diferida.
_R_BARS = 5  # horizonte de retorno / timeout (_RETURN_MAX_CANDLES, linea 51)
_RETURN_TOLERANCE = Decimal("0.0005")  # banda +/-0.05% retorno (l.52)
_FAR_THRESHOLD = Decimal("0.005")  # 0.5% alejamiento = breakout real (l.53)

# Semillas de la ETIQUETA [PARIDAD v4 void, ensamblaje ratificado por Central+CSA].
# Solo fixtures/calibracion; nunca en produccion.
_MOVE_TARGET = Decimal("0.005")  # el fade recorrio 0.5% en la direccion predicha
_INVALIDATION = Decimal("0.005")  # el breakout se reanudo 0.5% = el fade fallo


class VoidSnapDirection(StrEnum):
    """Direccion del snap (contraria al cruce que lo origino)."""

    # Cruce ASCENDENTE que vuelve -> se espera caida.
    BEARISH = "bearish"
    # Cruce DESCENDENTE que vuelve -> se espera subida.
    BULLISH = "bullish"


@dataclass(frozen=True, slots=True)
class VoidParams:
    """Umbrales del detector como PARAMETRO (semillas [PARIDAD v4] por defecto).

    Se exponen para que la calibracion (AHP, diferida) los sustituya sin tocar
    la logica.
    """

    r_bars: int = _R_BARS
    return_tolerance: Decimal = _RETURN_TOLERANCE
    far_threshold: Decimal = _FAR_THRESHOLD


# Singleton de parametros por defecto (evita construir en el default de la firma, B008).
_DEFAULT_PARAMS = VoidParams()


@dataclass(frozen=True, slots=True)
class VoidSnap:
    """Un snap detectado: en que vela (indice en la ventana) y su direccion."""

    index: int
    direction: VoidSnapDirection


@dataclass(frozen=True, slots=True)
class VoidSignal:
    """Veredicto de void para la ULTIMA vela de la ventana.

    detected=True si esa vela es un snap; direction es su lado (None si no hay snap).
    """

    detected: bool
    direction: VoidSnapDirection | None


def scan_void_snaps(
    closes: Sequence[Decimal],
    lvn: Decimal,
    params: VoidParams = _DEFAULT_PARAMS,
) -> list[VoidSnap]:
    """Recorre la ventana de closes contra UN nivel LVN; devuelve snaps. [PARIDAD v4].

    closes oldest->newest. Replica la watchlist de v4 como fold sobre la ventana
    (bounded: todo cruce caduca en r_bars). Reglas por vela tras el cruce (elapsed>=1):
      - FAR (invalidacion): |close - LVN| > LVN * far_threshold -> descarta. Tiene
        PRIORIDAD sobre el retorno en la misma vela (fail-safe CSA, punto 3.4).
      - RETORNO (snap) si elapsed <= r_bars: cruce up -> close <= LVN*(1+tol) ->
        BEARISH; cruce down -> close >= LVN*(1-tol) -> BULLISH.
      - TIMEOUT: elapsed > r_bars -> descarta.
    La vela del propio cruce no cuenta (elapsed arranca en 1 en la siguiente).
    """
    snaps: list[VoidSnap] = []
    if lvn <= 0 or len(closes) < 2:
        return snaps
    return_up = lvn * (Decimal(1) + params.return_tolerance)  # cruce up: close <= esto
    return_down = lvn * (Decimal(1) - params.return_tolerance)  # down: close >= esto
    far_abs = lvn * params.far_threshold  # |close - LVN| > esto -> FAR (sin dividir)
    # Cada watch: (indice del cruce, is_up). is_up True = cruce ascendente.
    watching: list[tuple[int, bool]] = []
    for index, close in enumerate(closes):
        survivors: list[tuple[int, bool]] = []
        for cross_index, is_up in watching:
            if cross_index == index:
                survivors.append((cross_index, is_up))  # no contar la vela del cruce
                continue
            elapsed = index - cross_index
            far = abs(close - lvn) > far_abs
            returned = close <= return_up if is_up else close >= return_down
            if far:
                continue  # invalidacion / breakout real: prioridad sobre retorno (3.4)
            if returned and elapsed <= params.r_bars:
                direction = (
                    VoidSnapDirection.BEARISH if is_up else VoidSnapDirection.BULLISH
                )
                snaps.append(VoidSnap(index=index, direction=direction))
                continue  # entrada consumida por el snap
            if elapsed > params.r_bars:
                continue  # timeout
            survivors.append((cross_index, is_up))
        watching = survivors
        if index >= 1:
            prev = closes[index - 1]
            if prev < lvn <= close:
                watching.append((index, True))
            elif prev > lvn >= close:
                watching.append((index, False))
    return snaps


def evaluate_void(
    closes: Sequence[Decimal],
    lvn: Decimal,
    params: VoidParams = _DEFAULT_PARAMS,
) -> VoidSignal:
    """Veredicto void para la ULTIMA vela de la ventana (la evaluada en T)."""
    if len(closes) < 2:
        return VoidSignal(detected=False, direction=None)
    last = len(closes) - 1
    for snap in scan_void_snaps(closes, lvn, params):
        if snap.index == last:
            return VoidSignal(detected=True, direction=snap.direction)
    return VoidSignal(detected=False, direction=None)


@dataclass(frozen=True, slots=True)
class VoidLabelParams:
    """Semillas de la etiqueta [PARIDAD v4 void]. Solo fixtures/calibracion."""

    r_bars: int = _R_BARS
    move_target: Decimal = _MOVE_TARGET
    invalidation: Decimal = _INVALIDATION


# Singleton de parametros de etiqueta por defecto (evita B008 en la firma).
_DEFAULT_LABEL_PARAMS = VoidLabelParams()


def label_void(
    *,
    direction: VoidSnapDirection,
    lvn: Decimal,
    subsequent: Sequence[Decimal],
    params: VoidLabelParams = _DEFAULT_LABEL_PARAMS,
) -> int:
    """Etiqueta 1 (el fade funciono) / 0 (fallo) de un snap. [Etiqueta AHP VOID].

    Tras el snap (direction), dentro de r_bars closes posteriores:
      - INVALIDA (0): el breakout se reanuda mas alla de INVALIDATION (bearish snap ->
        close > LVN*(1+inv); bullish -> close < LVN*(1-inv)). Tiene PRIORIDAD: si en la
        misma vela coinciden invalidacion y target, o la invalidacion llega antes, es 0.
      - CONFIRMA (1): el close recorre MOVE_TARGET en la direccion del snap (bearish ->
        close < LVN*(1-tgt); bullish -> close > LVN*(1+tgt)).
      - TIMEOUT (0): ni target ni invalidacion dentro de r_bars.
    Solo fixtures deterministas y futura calibracion, nunca produccion.
    """
    if lvn <= 0:
        return 0
    up_invalidation = lvn * (Decimal(1) + params.invalidation)
    down_invalidation = lvn * (Decimal(1) - params.invalidation)
    up_target = lvn * (Decimal(1) + params.move_target)
    down_target = lvn * (Decimal(1) - params.move_target)
    for close in subsequent[: params.r_bars]:
        if direction is VoidSnapDirection.BEARISH:
            invalidated = close > up_invalidation
            reached = close < down_target
        else:
            invalidated = close < down_invalidation
            reached = close > up_target
        if invalidated:
            return 0  # prioridad de la invalidacion (fail-safe + "antes que el target")
        if reached:
            return 1
    return 0
