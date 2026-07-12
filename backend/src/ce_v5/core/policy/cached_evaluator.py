"""CachedPolicyEvaluator: sirve del cache o recomputa, SIEMPRE fail-closed.

Envuelve un PolicyEvaluator con un CapabilitySetCache (ADR-012). Camino paso a
paso:
- Entrada FRESCA (dentro de max_staleness) y con la policy_version VIGENTE ->
  se sirve sin recomputar.
- Falta, esta stale, o su policy_version no es la vigente -> se RECOMPUTA con el
  evaluador (el cambio de version, si el evento se perdio, se detecta aqui).
- Si la recomputacion (o la lectura de la version vigente) LANZA -> FAIL-CLOSED:
    * capacidades SENSIBLES -> DENY. Jamas se sirve un valor stale para una
      capacidad sensible: ni ejecucion, ni API keys, ni autotrade (ADR-012).
      reason_code: DENIED_POLICY_VERSION_NOT_CURRENT si la entrada era de otra
      version; DENIED_CACHE_STALE si habia una entrada stale de la version
      vigente; DENIED_NOT_RECOMPUTABLE si no habia ninguna.
    * capacidades NO sensibles -> se sirve la stale SOLO si
      degrade_non_sensitive_with_stale es True Y existe una entrada de la version
      vigente; en cualquier otro caso, NOT_APPLICABLE.

MECANISMO DE EXPOSICION DEL FALLO: cuando degrada, evaluate() NO devuelve el set
en silencio; LANZA PolicyDegradedError, que envuelve el set degradado y conserva
la excepcion original como causa. Se eligio una excepcion (y no un campo del
resultado) para que un fallo sea imposible de ignorar: el gate (B8) DEBE
capturarla, AUDITA la causa y aplica el set degradado. Invariante: un fallo
NUNCA concede (el set degradado no lleva ALLOW en sensibles) y NUNCA se pierde.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NoReturn, Protocol, runtime_checkable

from ce_v5.core.policy.cache import (
    CacheEntry,
    CacheKey,
    CapabilitySetCache,
    capabilities_digest,
    resources_digest,
)
from ce_v5.core.policy.capabilities import is_sensitive
from ce_v5.core.policy.decisions import Decision, ReasonCode
from ce_v5.core.policy.evaluator import (
    CapabilityDecision,
    CapabilitySet,
    ResourceContext,
)
from ce_v5.core.policy.inputs import PolicyInputs


@runtime_checkable
class Evaluator(Protocol):
    """Lo que el CachedPolicyEvaluator necesita del evaluador subyacente."""

    def current_policy_version(self) -> str | None:
        """La policy_version en vigor (lectura barata)."""
        ...

    def evaluate(
        self,
        inputs: PolicyInputs,
        capability_ids: Sequence[str],
        resources: ResourceContext | None = None,
    ) -> CapabilitySet:
        """Recomputa el capability set autoritativo."""
        ...


class PolicyDegradedError(RuntimeError):
    """La recomputacion fallo: capability set DEGRADADO fail-closed (ADR-012).

    capability_set lleva todas las capacidades en DENY/NOT_APPLICABLE (jamas
    ALLOW en sensibles). La excepcion original queda como __cause__ para que el
    gate (B8) la audite. Un fallo NUNCA concede y NUNCA se pierde.
    """

    def __init__(self, capability_set: CapabilitySet) -> None:
        super().__init__(
            "recomputacion de politica fallida; capability set degradado fail-closed"
        )
        self.capability_set = capability_set


class CachedPolicyEvaluator:
    """Cachea el capability set; ante fallo, degrada fail-closed (ADR-012)."""

    def __init__(
        self,
        evaluator: Evaluator,
        cache: CapabilitySetCache,
        degrade_non_sensitive_with_stale: bool = False,
    ) -> None:
        self._evaluator = evaluator
        self._cache = cache
        self._degrade = degrade_non_sensitive_with_stale

    def evaluate(
        self,
        inputs: PolicyInputs,
        capability_ids: Sequence[str],
        resources: ResourceContext | None = None,
    ) -> CapabilitySet:
        """Sirve del cache o recomputa; ante fallo lanza PolicyDegradedError."""
        digest = resources_digest(resources)
        # La lista de capabilities es parte de la clave: sin ella, una respuesta a
        # una pregunta se serviria como respuesta a OTRA pregunta (defecto de B9).
        caps_digest = capabilities_digest(capability_ids)
        entry = self._cache.find(
            inputs.subject_tenant_id, inputs.subject_user_id, digest, caps_digest
        )

        # Version vigente: lectura barata. Si falla, el store esta caido.
        try:
            current_version = self._evaluator.current_policy_version()
        except Exception as exc:
            self._degrade_and_raise(
                inputs, capability_ids, entry, None, version_known=False, cause=exc
            )

        # Entrada fresca y con la version vigente -> se sirve sin recomputar.
        if (
            entry is not None
            and not self._cache.is_stale(entry)
            and entry.capability_set.policy_version == current_version
        ):
            return entry.capability_set

        # Falta, stale, o version distinta -> recomputar.
        try:
            result = self._evaluator.evaluate(inputs, capability_ids, resources)
        except Exception as exc:
            self._degrade_and_raise(
                inputs,
                capability_ids,
                entry,
                current_version,
                version_known=True,
                cause=exc,
            )

        self._cache.put(
            CacheKey(
                tenant_id=inputs.subject_tenant_id,
                user_id=inputs.subject_user_id,
                policy_version=result.policy_version,
                resources_digest=digest,
                capabilities_digest=caps_digest,
            ),
            result,
        )
        return result

    def _degrade_and_raise(
        self,
        inputs: PolicyInputs,
        capability_ids: Sequence[str],
        entry: CacheEntry | None,
        current_version: str | None,
        *,
        version_known: bool,
        cause: Exception,
    ) -> NoReturn:
        version_mismatch = (
            version_known
            and entry is not None
            and entry.capability_set.policy_version != current_version
        )
        decisions: dict[str, CapabilityDecision] = {}
        for cap in capability_ids:
            if is_sensitive(cap):
                if version_mismatch:
                    reason = ReasonCode.DENIED_POLICY_VERSION_NOT_CURRENT
                elif entry is not None:
                    reason = ReasonCode.DENIED_CACHE_STALE
                else:
                    reason = ReasonCode.DENIED_NOT_RECOMPUTABLE
                decisions[cap] = CapabilityDecision(
                    capability_id=cap,
                    decision=Decision.DENY,
                    reason_code=reason,
                    policy_version=current_version,
                    sensitive=True,
                    kill_switch_id=None,
                )
            elif self._degrade and entry is not None and not version_mismatch:
                # No sensible con degradacion explicita: se sirve la stale.
                decisions[cap] = entry.capability_set.decision_for(cap)
            else:
                decisions[cap] = CapabilityDecision(
                    capability_id=cap,
                    decision=Decision.NOT_APPLICABLE,
                    reason_code=ReasonCode.NOT_APPLICABLE_UNKNOWN_CAPABILITY,
                    policy_version=current_version,
                    sensitive=False,
                    kill_switch_id=None,
                )
        degraded = CapabilitySet(
            tenant_id=inputs.subject_tenant_id,
            user_id=inputs.subject_user_id,
            policy_version=current_version,
            evaluated_at=self._cache.now_ms(),
            decisions=decisions,
        )
        raise PolicyDegradedError(degraded) from cause
