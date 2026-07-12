"""Vocabulario de capacidades del gate (ADR-012).

REGLA DURA (decision D1 de P06): la SENSIBILIDAD es CODIGO, el CATALOGO de
capacidades es DATO. El catalogo de capacidades no sensibles (widgets,
premium, dibujo...) es configuracion de negocio y vive en la base de datos
como dato versionado. Pero QUE capacidades son SENSIBLES se fija aqui, en una
lista cerrada: si fuese un dato, un UPDATE podria marcar execute_order como no
sensible y apagar el fail-closed sin tocar codigo.

Las capacidades sensibles NO se construyen en P06 (la ejecucion es M5):
aqui solo se NOMBRAN, para poder BLOQUEARLAS antes de que existan (el gate
existe antes que la capacidad gateada, DOC_ROADMAP sec.2).
"""

from enum import StrEnum

CapabilityId = str


class SensitiveCapability(StrEnum):
    """Capacidades sensibles (ADR-012). Lista cerrada."""

    CONNECT_BROKER = "connect_broker"
    EXECUTE_ORDER = "execute_order"
    ACTIVATE_AUTOTRADE = "activate_autotrade"
    MANUAL_ORDER = "manual_order"
    MANAGE_API_KEY = "manage_api_key"


SENSITIVE_CAPABILITIES: frozenset[str] = frozenset(c.value for c in SensitiveCapability)


def is_sensitive(capability_id: CapabilityId) -> bool:
    """True si la capability esta en la lista cerrada de sensibles (ADR-012)."""
    return capability_id in SENSITIVE_CAPABILITIES
