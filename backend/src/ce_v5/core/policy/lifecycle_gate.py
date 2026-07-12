"""Adaptador de politica del gate de lifecycle (ADR-010 + ADR-012).

Implementa el puerto LifecycleGate (definido en core.component) traduciendo una
peticion neutra de lifecycle a la evaluacion de politica. Vive en core.policy:
la dependencia va core.policy -> core.component, nunca al reves (evita el ciclo
que cerraria el consumer del PASO 5). Se cablea en el composition root (P06b).

ASIMETRIA (regla dura). Una instancia GLOBAL de plataforma NO tiene sujeto:
fingir un tenant para poder "evaluar plan/jurisdiccion/KYC" seria mentir. Por
eso el camino se bifurca:

  - GLOBAL: NO se resuelven entradas de sujeto (el resolver NO se llama). Solo
    aplican los KILL SWITCHES DE PLATAFORMA (global, connector, capability) que
    apunten a las capacidades del componente. Un switch de tenant/user no puede
    morder algo sin tenant/user.
  - TENANT / USER: SI hay sujeto. Se resuelven sus entradas (resolver, cableado
    en P06b) y se evalua CADA capacidad requerida con el motor, que ya aplica
    kill switches, reglas, entitlements y overrides. Cualquier DENY deniega el
    arranque con SU reason_code real.

El resolver de entradas de sujeto es un PUERTO: su implementacion real (leer
plan/jurisdiccion/KYC de la identidad autenticada) es P06b; aqui solo se declara.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ce_v5.core.component.gate import LifecycleGateRequest, LifecycleVerdict
from ce_v5.core.policy.decisions import Decision, ReasonCode
from ce_v5.core.policy.gate import GateEvaluator
from ce_v5.core.policy.inputs import PolicyInputs
from ce_v5.core.policy.kill_switch_targeting import kill_switch_targets
from ce_v5.core.policy.ports import KillSwitchRecord
from source.families.component import LifecycleScope


@runtime_checkable
class SubjectInputsResolver(Protocol):
    """Resuelve las entradas de politica de un sujeto (PUERTO, impl en P06b)."""

    def resolve(self, tenant_id: str, user_id: str | None) -> PolicyInputs:
        """PolicyInputs (plan/jurisdiccion/KYC/...) del sujeto (tenant, user)."""
        ...


@runtime_checkable
class KillSwitchSource(Protocol):
    """Lo minimo que el camino GLOBAL necesita: los kill switches activos."""

    def active_kill_switches(self) -> Sequence[KillSwitchRecord]:
        """Kill switches con active=true (lo cumple PolicyStore)."""
        ...


class PolicyLifecycleGate:
    """Adaptador que resuelve el gate de INITIALIZE con la politica (ADR-012)."""

    def __init__(
        self,
        evaluator: GateEvaluator,
        kill_switches: KillSwitchSource,
        resolver: SubjectInputsResolver,
    ) -> None:
        self._evaluator = evaluator
        self._kill_switches = kill_switches
        self._resolver = resolver

    def check_initialize(self, request: LifecycleGateRequest) -> LifecycleVerdict:
        """ALLOW deja inicializar; DENY manda la instancia a QUARANTINED."""
        if request.scope is LifecycleScope.GLOBAL:
            return self._check_global(request)
        return self._check_subject(request)

    def _check_global(self, request: LifecycleGateRequest) -> LifecycleVerdict:
        # Sin sujeto: NO se llama al resolver. Solo kill switches de plataforma.
        for switch in self._kill_switches.active_kill_switches():
            if kill_switch_targets(
                scope=switch.scope,
                target_ref=switch.target_ref,
                switch_tenant_id=switch.tenant_id,
                switch_user_id=switch.user_id,
                component_capabilities=request.required_capabilities,
                component_tenant_id=None,
                component_user_id=None,
            ):
                return LifecycleVerdict.deny(ReasonCode.DENIED_BY_KILL_SWITCH.value)
        return LifecycleVerdict.allow()

    def _check_subject(self, request: LifecycleGateRequest) -> LifecycleVerdict:
        if request.tenant_id is None:
            # Instancia con sujeto pero sin tenant: no se puede evaluar ->
            # fail-closed (no se concede por un dato que falta).
            return LifecycleVerdict.deny(ReasonCode.DENIED_POLICY_UNAVAILABLE.value)
        inputs = self._resolver.resolve(request.tenant_id, request.user_id)
        capability_set = self._evaluator.evaluate(inputs, request.required_capabilities)
        for capability_id in request.required_capabilities:
            decision = capability_set.decision_for(capability_id)
            if decision.decision is Decision.DENY:
                return LifecycleVerdict.deny(decision.reason_code.value)
        return LifecycleVerdict.allow()
