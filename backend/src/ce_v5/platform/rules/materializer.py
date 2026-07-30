"""Materializadores de DataSources para el catalogo vivo (CE-14, sub-pieza P08c).

Un materializador convierte una fuente DECLARADA en su SERIE de valores (la
tuple[Decimal, ...] oldest->newest que consume el evaluador, Series por source_id).
Hay uno por MemoryModel:

- POINT_LOCAL: el valor de la barra T es el dato crudo de T; la serie es la ventana
  leida tal cual (patron actual de market.close via read_close_window). No necesita
  materializador propio: la lectura ES la serie.
- WINDOWED: el valor de la barra T sale de una VENTANA ACOTADA [T-w+1, T] de una fuente
  BASE (p.ej. vp.* sobre footprints). materialize_windowed aplica la FUNCION PURA de la
  fuente sobre cada sub-ventana rodante de la base ya leida.
- RECURSIVE/INTEGRATOR: snapshot/replay de VALOR. Tanda propia (T3).

CAPA. Este modulo es PLATFORM y es PURO: recibe la base YA LEIDA (una Sequence) y la
funcion pura de la fuente; NO hace I/O. Quien lee la base (lector_base) y compone
(flujo, open_time, ventana) -> base es la capa de entrypoints (composition), que si
puede importar infra. Interfaz completa del dictamen P08c-MAT-02 punto 3, con la
lectura resuelta por el caller:
    base = lector_base(flujo, open_time, history_bars + window_bars - 1)
    serie = materialize_windowed(base, funcion_pura, window_bars, history_bars)

Asi el materializador es DETERMINISTA y verificable por fixture (mismo base + misma
funcion pura -> misma serie, bit a bit), y platform no depende de infra.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from decimal import Decimal


def materialize_windowed[Base](
    base: Sequence[Base],
    transform: Callable[[Sequence[Base]], Decimal],
    *,
    window_bars: int,
    history_bars: int,
) -> tuple[Decimal, ...]:
    """Serie WINDOWED: aplica transform sobre cada sub-ventana rodante de la base.

    base: elementos de la fuente BASE (p.ej. footprints), oldest->newest, ya leidos.
    transform: funcion PURA de la fuente que mapea una ventana acotada de base a su
      valor Decimal (p.ej. lambda w: compute_volume_profile(w, ...).poc).
    window_bars (w): tamano de la ventana acotada de cada valor.
    history_bars: cuantos valores de salida se piden (los mas recientes).

    Devuelve la serie oldest->newest, un valor por barra de salida. El valor de la barra
    cuyo ultimo elemento de base es base[j] usa la ventana base[j-w+1 : j+1]; solo son
    computables las barras con j >= w-1. Se devuelven las history_bars mas recientes
    computables, o MENOS (incluso la tupla vacia) si la base no da para mas -- NUNCA se
    inventa una barra: la escasez de historia la resuelve el evaluador como
    NOT_EVALUABLE, igual que read_close_window. window_bars/history_bars <= 0 -> ().
    """
    if window_bars <= 0 or history_bars <= 0:
        return ()
    n = len(base)
    # Barras de salida computables: indices finales j en [w-1, n-1]. Tomamos las
    # history_bars mas recientes.
    first_j = max(window_bars - 1, n - history_bars)
    if first_j > n - 1:
        return ()
    return tuple(
        transform(base[j - window_bars + 1 : j + 1]) for j in range(first_j, n)
    )
