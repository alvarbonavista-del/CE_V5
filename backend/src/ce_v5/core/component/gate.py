"""Puerto del gate de lifecycle (ADR-010 + ADR-012, inversion de dependencia).

El supervisor (P04) consulta la politica ANTES de INITIALIZE, pero NO puede
depender de core.policy: el PASO 5 de P06 hace que core.policy MANEJE al
supervisor (evento de politica -> quarantine del componente), asi que un import
directo core.component -> core.policy cerraria un CICLO. Por eso el PUERTO vive
aqui (lo consume el supervisor) y el ADAPTADOR concreto (PolicyLifecycleGate)
vive en core.policy; la dependencia fluye en un solo sentido: core.policy ->
core.component. Se cablea en el composition root (P06b).

El puerto habla en el vocabulario del lifecycle, NO en el de la politica: recibe
una peticion neutra (scope, sujeto, capacidades requeridas, criticidad) y
devuelve un veredicto neutro (permitido/denegado + motivo + causante). El
supervisor no sabe si detras hay reglas, planes o kill switches; solo si puede
inicializar o no.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from source.families.component import LifecycleScope


@dataclass(frozen=True, slots=True)
class LifecycleGateRequest:
    """Lo que el gate necesita para decidir si una instancia puede INITIALIZE.

    Es un valor NEUTRO (sin ComponentInstance ni manifest) para que el adaptador
    de politica no dependa de las interioridades del supervisor. tenant_id y
    user_id son None en una instancia GLOBAL: una instancia global NO tiene
    sujeto, y el adaptador debe tratarla como tal (no fingir uno).
    """

    scope: LifecycleScope
    tenant_id: str | None
    user_id: str | None
    required_capabilities: tuple[str, ...]
    critical: bool


@dataclass(frozen=True, slots=True)
class LifecycleVerdict:
    """Resultado del gate. DENY lleva SIEMPRE el motivo real de la decision.

    reason_code es el reason_code autoritativo de la politica (p.ej.
    denied_by_kill_switch, denied_by_plan): viaja tal cual al evento
    component.quarantined para que la cuarentena sea depurable. causation_id
    apunta al event_id del evento de politica que provoco la denegacion, cuando
    lo hubo (en el gate previo a INITIALIZE no suele haberlo: el kill switch es
    un registro, no un evento; el enlace causal vive en el consumer del PASO 5).
    """

    allowed: bool
    reason_code: str | None = None
    causation_id: str | None = None

    @classmethod
    def allow(cls) -> LifecycleVerdict:
        return cls(allowed=True)

    @classmethod
    def deny(
        cls, reason_code: str, *, causation_id: str | None = None
    ) -> LifecycleVerdict:
        return cls(allowed=False, reason_code=reason_code, causation_id=causation_id)


@runtime_checkable
class LifecycleGate(Protocol):
    """Puerto que el supervisor consulta antes de INITIALIZE (ADR-010)."""

    def check_initialize(self, request: LifecycleGateRequest) -> LifecycleVerdict:
        """ALLOW deja inicializar; DENY manda la instancia a QUARANTINED."""
        ...
