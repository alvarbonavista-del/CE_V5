"""Cache del capability set con TTL como RED DE SEGURIDAD (ADR-012).

La INVALIDACION POR EVENTO (invalidation.py) es el mecanismo PRINCIPAL de
frescura; el TTL (max_staleness_ms) solo cubre el caso de que un evento de
invalidacion se PIERDA. Nunca al reves: no se confia en el TTL para propagar un
cambio de politica, se confia en el evento.

REGLA DURA (paga la tarea futura que P05 asigno a P06): la clave SIEMPRE incluye
tenant_id; dos tenants jamas comparten una entrada. La clave incluye tambien la
huella del ResourceContext: la respuesta depende del recurso, y cachear sin el
mezclaria preguntas distintas (asimetria UI/backend).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ce_v5.core.clock import Clock
from ce_v5.core.policy.evaluator import CapabilitySet, ResourceContext


def resources_digest(resources: ResourceContext | None) -> str:
    """Huella estable del ResourceContext para la clave de cache."""
    if resources is None:
        return repr((None, None, None))
    return repr((resources.exchange, resources.connector, resources.market_scope))


def capabilities_digest(capability_ids: Sequence[str]) -> str:
    """Huella ESTABLE de las capabilities pedidas (ordenadas, deduplicadas).

    Parte de la clave de cache. Sin ella (defecto destapado por la validacion en
    caliente de B9), un CapabilitySet cacheado tras preguntar por una lista se
    serviria como respuesta a una pregunta por OTRA lista, y decision_for
    devolveria "no evaluada" (NOT_APPLICABLE, o DENY denied_not_evaluated si es
    sensible): el gate DENEGARIA capacidades que la politica PERMITE. Estable
    frente al orden y a los duplicados: la MISMA pregunta reusa la entrada.
    """
    return repr(tuple(sorted(set(capability_ids))))


@dataclass(frozen=True, slots=True)
class CacheKey:
    """Clave de cache. SIEMPRE incluye tenant_id (regla dura, ADR-012).

    Incluye tambien la huella de las capabilities preguntadas: una respuesta a
    una pregunta NO vale como respuesta a otra pregunta (defecto de B9).
    """

    tenant_id: str
    user_id: str | None
    policy_version: str | None
    resources_digest: str
    capabilities_digest: str


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """Un capability set cacheado con su instante de evaluacion (epoch ms)."""

    capability_set: CapabilitySet
    evaluated_at: int


class CapabilitySetCache:
    """Cache en memoria del capability set (TTL = red de seguridad, ADR-012)."""

    def __init__(self, clock: Clock, max_staleness_ms: int) -> None:
        self._clock = clock
        self._max_staleness_ms = max_staleness_ms
        self._entries: dict[CacheKey, CacheEntry] = {}

    def now_ms(self) -> int:
        """Instante actual segun el reloj inyectado (ADR-007)."""
        return self._clock.now_ms()

    def is_stale(self, entry: CacheEntry) -> bool:
        """True si la entrada supera max_staleness respecto al reloj inyectado."""
        return self._clock.now_ms() - entry.evaluated_at > self._max_staleness_ms

    def get(self, key: CacheKey) -> CapabilitySet | None:
        """Devuelve el set si hay entrada fresca; None si falta o esta stale."""
        entry = self._entries.get(key)
        if entry is None or self.is_stale(entry):
            return None
        return entry.capability_set

    def put(self, key: CacheKey, capability_set: CapabilitySet) -> None:
        """Guarda el set; una entrada por sujeto+recurso+capabilities (reemplaza).

        Una nueva policy_version reemplaza a la anterior de la MISMA pregunta;
        preguntas por listas de capabilities distintas coexisten como entradas
        separadas (cada una es una respuesta a SU pregunta, defecto de B9).
        """
        stale = [
            existing
            for existing in self._entries
            if existing.tenant_id == key.tenant_id
            and existing.user_id == key.user_id
            and existing.resources_digest == key.resources_digest
            and existing.capabilities_digest == key.capabilities_digest
        ]
        for existing in stale:
            del self._entries[existing]
        self._entries[key] = CacheEntry(
            capability_set=capability_set, evaluated_at=capability_set.evaluated_at
        )

    def find(
        self,
        tenant_id: str,
        user_id: str | None,
        digest: str,
        capabilities: str,
    ) -> CacheEntry | None:
        """Entrada del sujeto+recurso+capabilities, sea cual sea su policy_version.

        Sirve para decidir la degradacion fail-closed (habia una entrada, aunque
        fuese stale o de otra version). Se exige el digest de capabilities: una
        entrada de OTRA lista NO decide una degradacion sobre ESTA (defecto de B9).
        """
        for key, entry in self._entries.items():
            if (
                key.tenant_id == tenant_id
                and key.user_id == user_id
                and key.resources_digest == digest
                and key.capabilities_digest == capabilities
            ):
                return entry
        return None

    def invalidate_subject(self, tenant_id: str, user_id: str | None) -> None:
        """Tira las entradas del sujeto. Si user_id es None, todo el tenant."""
        to_remove = [
            key
            for key in self._entries
            if key.tenant_id == tenant_id
            and (user_id is None or key.user_id == user_id)
        ]
        for key in to_remove:
            del self._entries[key]

    def invalidate_all(self) -> None:
        """Tira todas las entradas."""
        self._entries.clear()
