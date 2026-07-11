"""Contexto de tenancy resuelto por el backend (ADR-011)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Principal autenticado y tenant efectivo resuelto para el.

    Lo construye SIEMPRE el backend a partir de la identidad autenticada y la
    pertenencia. El cliente nunca lo aporta ni lo influye.
    """

    user_id: UUID
    tenant_id: UUID
