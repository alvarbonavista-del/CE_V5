"""Funciones canonicas continuas sobre una ventana de historia (INFORME 6 sec 10.9).

Neutrales y mercado-agnosticas: operan sobre una VENTANA de valores (oldest->newest) de
una fuente CONTINUA, en la unidad de historia que la fuente declara (la ventana ya viene
en esa unidad). Devuelven un resultado que puede ser NO EVALUABLE por historia
insuficiente (INFORME 6 sec 9.1): en SaaS, "no hay suficiente historia" NO es "false".

value_at/previous_value/average/change se implementan aqui (las que demuestra el precio
de cierre crudo). is_active/elapsed_since operan sobre la VIGENCIA de fuentes
ESPORADICAS y se implementan con la primera fuente esporadica del catalogo (no hay
ninguna en v5.0; el validador del Bloque 3 impide usarlas sobre fuentes continuas). El
marco de DataSource YA declara fuentes esporadicas (2a); aqui solo estan las funciones
continuas.
"""

from dataclasses import dataclass
from decimal import Decimal

from source.rules.vocab import NO_OFFSET_FUNCTIONS, CanonicalFunction


@dataclass(frozen=True, slots=True)
class FunctionValue:
    """Resultado de una funcion continua: un valor, o NO EVALUABLE.

    evaluable=False cuando no hay suficiente historia para el offset/count pedido; value
    es None entonces. NOT_EVALUABLE NO es FALSE (INFORME 6 sec 9.1).
    """

    evaluable: bool
    value: Decimal | None = None


NOT_EVALUABLE = FunctionValue(evaluable=False)


def value_at(window: tuple[Decimal, ...], offset: int) -> FunctionValue:
    """Valor de hace `offset` posiciones (0=actual). NO EVALUABLE si falta historia."""
    if offset < 0 or offset >= len(window):
        return NOT_EVALUABLE
    return FunctionValue(evaluable=True, value=window[-1 - offset])


def previous_value(window: tuple[Decimal, ...], offset: int) -> FunctionValue:
    """Valor de hace `offset` posiciones, con offset >= 1 (el anterior)."""
    if offset < 1:
        msg = "previous_value exige offset >= 1."
        raise ValueError(msg)
    return value_at(window, offset)


def average(window: tuple[Decimal, ...], count: int) -> FunctionValue:
    """Media de los ultimos `count` valores. NO EVALUABLE si no hay `count` valores."""
    if count < 1:
        msg = "average exige count >= 1."
        raise ValueError(msg)
    if len(window) < count:
        return NOT_EVALUABLE
    last = window[-count:]
    return FunctionValue(evaluable=True, value=sum(last, Decimal(0)) / count)


def change(window: tuple[Decimal, ...], offset: int) -> FunctionValue:
    """Delta entre el actual y el de hace `offset`. NO EVALUABLE si falta historia."""
    if offset < 1:
        msg = "change exige offset >= 1."
        raise ValueError(msg)
    if offset >= len(window):
        return NOT_EVALUABLE
    return FunctionValue(evaluable=True, value=window[-1] - window[-1 - offset])


# Version del catalogo de funciones (input del PlanFingerprint, ADR-017). Se SUBE cuando
# cambia la SEMANTICA de alguna funcion (p.ej. la ventana que necesita), porque un plan
# calculado con la vieja semantica ya no es recomputable igual.
FUNCTION_CATALOG_VERSION = 1


class SporadicFunctionUnsupportedError(RuntimeError):
    """Una funcion esporadica no tiene fuente en v5.0 (soporte diferido, Bloque 2)."""


def history_bars_needed(function: CanonicalFunction | None, offset: int | None) -> int:
    """Cuantas velas CERRADAS necesita una funcion para el offset dado.

    Coherente con la semantica de ventana (oldest->newest) de este modulo, que accede a
    window[-1 - offset] y promedia los ultimos count:
    - None (acceso directo, valor actual) ........... 1
    - value_at / previous_value / change (offset n) . n + 1
    - average (count n) ............................. n

    is_active / elapsed_since son ESPORADICAS y en v5.0 NO tienen fuente (el marco las
    declara, pero el soporte esporadico esta diferido, Bloque 2): lanzan
    SporadicFunctionUnsupportedError. Que una funcion CASE con la servibilidad de la
    fuente ya lo garantizo el validador del Bloque 3; aqui solo se dimensiona historia.
    """
    if function is None:
        return 1
    if function in NO_OFFSET_FUNCTIONS:
        msg = (
            f"la funcion esporadica {function.value} no tiene fuente en v5.0 "
            "(soporte esporadico diferido, Bloque 2): no se dimensiona su historia."
        )
        raise SporadicFunctionUnsupportedError(msg)
    if offset is None:
        msg = f"la funcion {function.value} exige un offset para dimensionar historia."
        raise ValueError(msg)
    if function is CanonicalFunction.AVERAGE:
        return offset
    return offset + 1
