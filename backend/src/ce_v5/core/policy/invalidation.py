"""Traductor de eventos policy.* a invalidaciones de cache (ADR-012).

Estos tres eventos son la INVALIDACION POR EVENTO de ADR-012 (mecanismo
PRINCIPAL de frescura; el TTL del cache es solo la red de seguridad). Pagan la
tarea futura que P05 asigno a P06: la invalidacion por cambio de
rol/premium/jurisdiccion/KYC llega como subject_invalidated con su
InvalidationReason.

SIN dependencia del bus: el cableado al EventBus es de B6/B9. Aqui se recibe el
payload YA deserializado y se traduce a una operacion del cache.

on_kill_switch_changed y on_version_published invalidan TODO: un switch de
exchange o de capability puede afectar a cualquiera, y una version nueva cambia
el reglamento entero. Invalidar todo es lo CORRECTO y lo BARATO (recomputar es
leer la DB); equivocarse aqui dejaria activa una capacidad recien prohibida.
"""

from __future__ import annotations

from ce_v5.core.policy.cache import CapabilitySetCache
from source.families.policy import (
    KillSwitchPayload,
    PolicyVersionPublishedPayload,
    SubjectInvalidatedPayload,
)


class PolicyCacheInvalidator:
    """Aplica al cache las invalidaciones que dictan los eventos policy.*."""

    def __init__(self, cache: CapabilitySetCache) -> None:
        self._cache = cache

    def on_kill_switch_changed(self, payload: KillSwitchPayload) -> None:
        """Un kill switch (activado o desactivado) invalida TODO el cache."""
        self._cache.invalidate_all()

    def on_version_published(self, payload: PolicyVersionPublishedPayload) -> None:
        """Una policy_version nueva cambia el reglamento: invalida TODO."""
        self._cache.invalidate_all()

    def on_subject_invalidated(self, payload: SubjectInvalidatedPayload) -> None:
        """Invalida al sujeto; user_id None invalida el tenant entero."""
        self._cache.invalidate_subject(payload.tenant_id, payload.user_id)
