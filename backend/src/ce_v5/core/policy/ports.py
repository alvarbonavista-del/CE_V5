"""Puerto de lectura de politica para el motor (ADR-012).

Registros que reflejan las tablas de B2 (sin logica) y el Protocol PolicyStore
que el motor consume. La implementacion real vive en infra (B4b) y se cablea en
el composition root; el nucleo depende del puerto, no del driver (REST-15).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class PolicyRuleRecord:
    """Una regla del reglamento vigente (espejo de policy_rule, B2).

    Cada match_* None es un COMODIN (cualquiera). Un match_* no nulo con la
    entrada correspondiente DESCONOCIDA no encaja (lo decide el motor).
    """

    rule_id: str
    capability_id: str
    effect: str
    reason_code: str
    match_jurisdiction: str | None
    match_plan: str | None
    match_role: str | None
    match_kyc_status: str | None
    match_vpn: bool | None


@dataclass(frozen=True, slots=True)
class EntitlementRecord:
    """Una concesion a un sujeto (espejo de policy_entitlement, B2)."""

    capability_id: str
    source: str
    expires_at: int | None


@dataclass(frozen=True, slots=True)
class OverrideRecord:
    """Una excepcion por sujeto (espejo de policy_override, B2)."""

    capability_id: str
    effect: str
    reason_code: str
    expires_at: int | None


@dataclass(frozen=True, slots=True)
class KillSwitchRecord:
    """Un kill switch activo (espejo de kill_switch, B2)."""

    kill_switch_id: str
    scope: str
    target_ref: str | None
    tenant_id: str | None
    user_id: str | None


@runtime_checkable
class PolicyStore(Protocol):
    """Lectura de politica que el motor necesita de la persistencia (ADR-012).

    La implementacion real vive en infra (B4b) y se cablea en el composition
    root; el nucleo depende de este puerto, nunca del driver (REST-15).
    """

    def current_policy_version(self) -> str | None:
        """La policy_version en vigor (status='current'), o None si no hay."""
        ...

    def rules(self, policy_version: str) -> Sequence[PolicyRuleRecord]:
        """Reglas del reglamento de esa policy_version."""
        ...

    def entitlements(
        self, tenant_id: str, user_id: str | None
    ) -> Sequence[EntitlementRecord]:
        """Concesiones del sujeto; el motor filtra la caducidad."""
        ...

    def overrides(
        self, tenant_id: str, user_id: str | None
    ) -> Sequence[OverrideRecord]:
        """Overrides del sujeto; el motor filtra la caducidad."""
        ...

    def active_kill_switches(self) -> Sequence[KillSwitchRecord]:
        """Kill switches con active=true."""
        ...
