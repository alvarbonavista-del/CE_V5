"""Productor de impulse_score para el gate de fase 1 de pivotphase (P08c P5 T2).

6a (AHP REV 2, firmado; DICTAMEN P08c-PIVOT-04): el impulse_score v5.0 NO es el
orderflow_score compuesto de v4 (impulso+absorcion+agotamiento+contexto, con multi-TF);
es un ESCALADO de orderflow.delta normalizado por su distribucion reciente (opcion B del
AHP). Deriva CONSCIENTE y FIRMADA respecto a v4. NO es detector nuevo: no dispara
DEC-AHP-01 (ya vive dentro del AHP REV 2). phase1_impulse_min=70 se reinterpreta como
PERCENTIL [A CALIBRAR].

Forma semilla: impulse_score = percentil mid-rank de |delta| contra la ventana de
distribucion reciente, escalado a 0-100. delta_momentum como segundo input es OPCIONAL
en el AHP ("+/-") y su combinacion queda [A CALIBRAR]; no se hornea una combinacion
inventada aqui (5.11). Determinista, solo Decimal, cuantizacion ROUND_HALF_EVEN
(ADR-007). |delta| -> el impulso es magnitud; la DIRECCION (bull/bear) la decide la FSM
por el signo de delta, no este score.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# Cuantizacion determinista del score 0-100 (ADR-007).
_IMPULSE_QUANTUM = Decimal("0.01")


def _percentile_rank(value: Decimal, distribution: Sequence[Decimal]) -> Decimal:
    """Rango percentil mid-rank de value en [0,1]: (n_menores + n_iguales/2) / n.

    Misma forma semilla que pivotphase_confidence (P4); si aparece un tercer uso se
    extrae a un util comun. distribution vacia la trata el llamador (no evaluable).
    """
    n = len(distribution)
    below = sum(1 for d in distribution if d < value)
    equal = sum(1 for d in distribution if d == value)
    return (Decimal(below) + Decimal(equal) / Decimal(2)) / Decimal(n)


def normalize_impulse_score(
    delta: Decimal, recent_abs_delta: Sequence[Decimal]
) -> Decimal | None:
    """impulse_score 0-100 = percentil de |delta| en la distribucion reciente x100.

    recent_abs_delta: ventana reciente de |delta| del propio simbolo/TF (materializada).
    Vacia -> None: sin base para normalizar es "sin impulso" (BarSignals.impulse_score
    None -> la FSM no arranca fase 1). El tamano de la ventana y percentil vs z-score
    son [A CALIBRAR AHP]. Determinista.
    """
    if not recent_abs_delta:
        return None
    rank = _percentile_rank(abs(delta), recent_abs_delta)
    return (rank * Decimal(100)).quantize(_IMPULSE_QUANTUM, rounding=ROUND_HALF_EVEN)
