"""volume.* -- familia de fuentes descriptivas deterministas sobre el volumen.

Re-expresion pura en v5 del VolumeEngine de v4 (paridad de RESULTADO/SEMANTICA,
sin engines). Dictamen Central P08b-11 (D1..D6):
  - Familia: volume.ratio_vs_avg (escalar), volume.direction (categorica),
    volume.is_increasing (booleana).
  - volume.avg queda INTERNO (denominador de ratio_vs_avg), NO fuente, hasta
    que una Rule lo consuma de forma independiente (CE-8, D1).
  - pct_above OMITIDO (transformada monotona trivial de ratio; deriva una Rule, D2).
  - above_avg NO existe: el umbral (20%) es de DECISION -> vive en la Rule/factor,
    no en la fuente (D3). candle.volume_confirm se resuelve como Rule.
  - Volumen crudo = market.volume (basico servible de P07); aqui solo se deriva (D4).
  - Decimal EXACTO; el round de v4 era presentacion. Aritmetica en contexto pinned.
  - Defaults de paridad como parametros (D5): lookback=20; direction empate -> UP
    (via >=); avg<=0 -> ratio=1; is_increasing requiere >=4 (si no, False).
  - Descriptivas deterministas: SIN AHP ni pre-registro (D6).

avg e is_increasing miran las N barras ANTERIORES (excluyen la actual);
direction compara la barra actual con la inmediata anterior.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import Enum

VOLUME_FORMULA_VERSION = 1

_DEFAULT_LOOKBACK = 20
_PREC = 34
_HUNDRED = Decimal(100)


class VolumeDirection(Enum):
    UP = "up"
    DOWN = "down"


def _mean(window: Sequence[Decimal]) -> Decimal:
    """Media aritmetica exacta (sum/len) en el contexto pinned vigente."""
    total = Decimal(0)
    for v in window:
        total += v
    return total / Decimal(len(window))


def ratio_vs_avg(
    volumes: Sequence[Decimal],
    lookback: int = _DEFAULT_LOOKBACK,
) -> tuple[Decimal | None, ...]:
    """volumen_actual / media de las `lookback` barras ANTERIORES.

    Barra 0 (sin historia) -> None. Si la media es <= 0 -> Decimal(1) (fail-safe
    de v4). Decimal exacto en contexto pinned.
    """
    if lookback < 1:
        raise ValueError("lookback debe ser >= 1")
    out: list[Decimal | None] = []
    with localcontext() as ctx:
        ctx.prec = _PREC
        ctx.rounding = ROUND_HALF_EVEN
        for i in range(len(volumes)):
            window = volumes[max(0, i - lookback) : i]
            if not window:
                out.append(None)
                continue
            avg = _mean(window)
            out.append(volumes[i] / avg if avg > 0 else Decimal(1))
    return tuple(out)


def direction(
    volumes: Sequence[Decimal],
) -> tuple[VolumeDirection | None, ...]:
    """UP si volumen[i] >= volumen[i-1], DOWN si no. Barra 0 -> None."""
    out: list[VolumeDirection | None] = []
    for i in range(len(volumes)):
        if i == 0:
            out.append(None)
        elif volumes[i] >= volumes[i - 1]:
            out.append(VolumeDirection.UP)
        else:
            out.append(VolumeDirection.DOWN)
    return tuple(out)


def is_increasing(
    volumes: Sequence[Decimal],
    lookback: int = _DEFAULT_LOOKBACK,
) -> tuple[bool, ...]:
    """True si, en las `lookback` barras ANTERIORES, la media de la 2a mitad
    supera a la de la 1a. Requiere >=4 barras de referencia; si no, False.
    """
    if lookback < 1:
        raise ValueError("lookback debe ser >= 1")
    out: list[bool] = []
    with localcontext() as ctx:
        ctx.prec = _PREC
        ctx.rounding = ROUND_HALF_EVEN
        for i in range(len(volumes)):
            ref = volumes[max(0, i - lookback) : i]
            if len(ref) < 4:
                out.append(False)
                continue
            half = len(ref) // 2
            first = _mean(ref[:half])
            second = _mean(ref[half:])
            out.append(second > first)
    return tuple(out)
