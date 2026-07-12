"""PolicyGate: la primitiva de enforcement que TODO llamador sensible atraviesa.

require() es EL PUNTO SENSIBLE: reevalua la politica, audita las capacidades
sensibles y, si algo impide permitir con garantias, DENIEGA. Es la decision
AUTORITATIVA de backend. capability_set() es la vista de CORTESIA para la UI:
informativa, sin auditoria, fail-closed. P06b cablea el gate en los bordes de la
API; aqui se entrega la primitiva, no la API.

D8 (REGLA DURA): si la escritura de auditoria FALLA en una capability SENSIBLE
que la politica iba a PERMITIR, el gate DENIEGA (reason_code
denied_audit_unavailable). Una accion sensible sin traza es una accion que el
sistema no puede demostrar: preferimos denegar de mas a ejecutar a ciegas. En
capacidades NO sensibles no se audita, asi que un fallo de auditoria no aplica.

Un fallo NUNCA concede: si el evaluador degrada (PolicyDegradedError) se usa su
capability set ya fail-closed; ante cualquier otra excepcion se construye un
DENY denied_not_recomputable. El gate jamas deja pasar por un error inesperado.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ce_v5.core.policy.audit import (
    SensitiveActionAudit,
    SensitiveActionRecord,
    build_context,
)
from ce_v5.core.policy.cached_evaluator import PolicyDegradedError
from ce_v5.core.policy.capabilities import is_sensitive
from ce_v5.core.policy.decisions import Decision, ReasonCode
from ce_v5.core.policy.evaluator import (
    CapabilityDecision,
    CapabilitySet,
    ResourceContext,
)
from ce_v5.core.policy.inputs import PolicyInputs

# Sin reglamento en vigor no hay policy_version; la traza (columna NOT NULL) la
# registra con este centinela, que dice la verdad: no habia reglamento vigente.
_NO_POLICY_VERSION = "unavailable"


@runtime_checkable
class GateEvaluator(Protocol):
    """Lo minimo que el gate necesita del evaluador (cacheado o directo)."""

    def evaluate(
        self,
        inputs: PolicyInputs,
        capability_ids: Sequence[str],
        resources: ResourceContext | None = None,
    ) -> CapabilitySet:
        """Evalua las capacidades para el sujeto de inputs."""
        ...


class PolicyDenied(RuntimeError):
    """La accion sensible NO se permite. Lleva la CapabilityDecision completa.

    Es una EXCEPCION a proposito: un DENY devuelto como valor de retorno acaba
    ignorandose; una excepcion hay que atenderla. El llamador la traduce (P06b la
    convertira en 403 en la API).
    """

    def __init__(self, decision: CapabilityDecision) -> None:
        super().__init__(
            f"capability {decision.capability_id!r} denegada: "
            f"{decision.reason_code.value}"
        )
        self.decision = decision


class PolicyGate:
    """Enforcement autoritativo (require) y vista de cortesia (capability_set).

    No recibe Clock: el instante de la traza lo pone el servidor con DEFAULT
    now() (precedente de P02b); el Clock inyectado es para tiempos de EVENTO
    (ADR-007), y una fila de auditoria es metadato tecnico, no un evento.
    """

    def __init__(
        self,
        evaluator: GateEvaluator,
        audit: SensitiveActionAudit,
    ) -> None:
        self._evaluator = evaluator
        self._audit = audit

    def require(
        self,
        inputs: PolicyInputs,
        capability_id: str,
        resources: ResourceContext | None = None,
    ) -> CapabilityDecision:
        """Exige permiso para una capability. DENY -> lanza PolicyDenied.

        1. Evalua. PolicyDegradedError -> se usa su set degradado (ya fail-closed)
           y se conserva la causa; cualquier otra excepcion -> DENY
           denied_not_recomputable (nunca se concede por un error inesperado).
        2. Si es SENSIBLE, se AUDITA SIEMPRE (ALLOW o DENY): auditar solo los
           bloqueos dejaria sin traza lo que mas importa, lo que SI se permitio.
        3. D8: si la auditoria FALLA en un sensible que iba a ALLOW, se DEGRADA a
           DENY denied_audit_unavailable (sin traza no se ejecuta). Si ya era
           DENY, se mantiene; el fallo de auditoria queda como causa, no se traga.
        4. Si la decision final no es ALLOW -> PolicyDenied; si es ALLOW -> se
           devuelve.
        """
        decision, cause = self._decide(inputs, capability_id, resources)
        if decision.sensitive:
            try:
                self._audit.record(self._record(inputs, decision))
            except Exception as exc:
                # Fail-closed ante cualquier fallo de auditoria (D8).
                if decision.decision is Decision.ALLOW:
                    decision = self._audit_failure_denial(decision)
                cause = exc
        if decision.decision is not Decision.ALLOW:
            raise PolicyDenied(decision) from cause
        return decision

    def capability_set(
        self,
        inputs: PolicyInputs,
        capability_ids: Sequence[str],
        resources: ResourceContext | None = None,
    ) -> CapabilitySet:
        """Vista de CORTESIA para la UI (D9). NO audita.

        Este capability set es INFORMATIVO; la UI oculta o deshabilita por
        CORTESIA. La decision AUTORITATIVA es siempre la reevaluacion en el punto
        sensible del backend (require). Un cliente que no llame a require no ha
        pasado por el gate.

        No se audita: auditar cada refresco de pantalla inundaria la traza de
        ruido y la volveria inutil justo cuando importa. Ante PolicyDegradedError
        se devuelve el set DEGRADADO (ya fail-closed): la UI debe poder pintar
        algo.
        """
        try:
            return self._evaluator.evaluate(inputs, capability_ids, resources)
        except PolicyDegradedError as exc:
            return exc.capability_set

    def _decide(
        self,
        inputs: PolicyInputs,
        capability_id: str,
        resources: ResourceContext | None,
    ) -> tuple[CapabilityDecision, BaseException | None]:
        try:
            capability_set = self._evaluator.evaluate(
                inputs, [capability_id], resources
            )
        except PolicyDegradedError as exc:
            return exc.capability_set.decision_for(capability_id), exc
        except Exception as exc:
            # Nunca se concede por un error inesperado: DENY fail-closed.
            return self._unexpected_denial(capability_id), exc
        return capability_set.decision_for(capability_id), None

    def _unexpected_denial(self, capability_id: str) -> CapabilityDecision:
        return CapabilityDecision(
            capability_id=capability_id,
            decision=Decision.DENY,
            reason_code=ReasonCode.DENIED_NOT_RECOMPUTABLE,
            policy_version=None,
            sensitive=is_sensitive(capability_id),
            kill_switch_id=None,
        )

    def _audit_failure_denial(self, decision: CapabilityDecision) -> CapabilityDecision:
        return CapabilityDecision(
            capability_id=decision.capability_id,
            decision=Decision.DENY,
            reason_code=ReasonCode.DENIED_AUDIT_UNAVAILABLE,
            policy_version=decision.policy_version,
            sensitive=True,
            kill_switch_id=decision.kill_switch_id,
        )

    def _record(
        self, inputs: PolicyInputs, decision: CapabilityDecision
    ) -> SensitiveActionRecord:
        return SensitiveActionRecord(
            tenant_id=inputs.subject_tenant_id,
            user_id=inputs.subject_user_id,
            capability_id=decision.capability_id,
            decision=decision.decision,
            reason_code=decision.reason_code,
            policy_version=decision.policy_version or _NO_POLICY_VERSION,
            sensitive=decision.sensitive,
            context=build_context(inputs, decision),
        )
