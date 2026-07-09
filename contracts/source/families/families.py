"""Taxonomia base de familias de evento (ADR-004).

Declara las 10 familias base CERRADAS y la convencion de naming
dominio.accion. NO define tipos de evento concretos: cada tipo nuevo
dentro de una familia lo declara su componente en su pieza (gobernanza
ADR-004). Aqui solo vive la taxonomia base; familia nueva = ADR.
"""

import re
from enum import StrEnum


class Family(StrEnum):
    """Las 10 familias base cerradas (ADR-004)."""

    MARKET = "market"
    DATASOURCE = "datasource"
    RULE = "rule"
    SIGNAL = "signal"
    ALERT = "alert"
    EXECUTION = "execution"
    NOTIFICATION = "notification"
    USER = "user"
    COMPONENT = "component"
    BILLING = "billing"


_FAMILY_ALT = "|".join(f.value for f in Family)
_ACTION = r"[a-z][a-z0-9_]*"
EVENT_TYPE_PATTERN = re.compile(rf"^(?P<family>{_FAMILY_ALT})\.(?P<action>{_ACTION})$")


def validate_event_type(value: str) -> str:
    """Valida que value sea 'familia.accion' con familia cerrada.

    Devuelve el valor si es valido; lanza ValueError si no. El dominio
    debe ser una de las 10 familias base; la accion es snake_case.
    """
    if EVENT_TYPE_PATTERN.match(value) is None:
        allowed = ", ".join(f.value for f in Family)
        msg = (
            f"event_type invalido: {value!r}. Formato 'familia.accion' "
            f"con familia en: {allowed}."
        )
        raise ValueError(msg)
    return value
