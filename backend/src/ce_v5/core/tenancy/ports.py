"""Puertos de tenancy: contratos que el nucleo necesita de la persistencia (ADR-011)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID


class MembershipReader(Protocol):
    """Lectura de la pertenencia user -> tenant (user_tenant_membership)."""

    def tenants_for_user(self, user_id: UUID) -> Sequence[UUID]:
        """Devuelve los tenants a los que pertenece el usuario. Vacio si ninguno."""
        ...
