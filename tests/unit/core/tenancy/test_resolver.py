"""Unit tests del TenantContextResolver: resolucion fail-closed (ADR-011)."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

import pytest

from ce_v5.core.tenancy import (
    TenantContextResolver,
    TenantResolutionError,
)

_USER = UUID("00000000-0000-0000-0000-0000000000a1")
_TENANT = UUID("00000000-0000-0000-0000-0000000000b1")
_OTRO_TENANT = UUID("00000000-0000-0000-0000-0000000000b2")


class _FakeMembershipReader:
    """Doble en memoria de MembershipReader: devuelve tenants prefijados."""

    def __init__(self, tenants: Sequence[UUID]) -> None:
        self._tenants = tuple(tenants)

    def tenants_for_user(self, user_id: UUID) -> Sequence[UUID]:
        return self._tenants


def test_resuelve_contexto_con_una_pertenencia() -> None:
    resolver = TenantContextResolver(_FakeMembershipReader((_TENANT,)))
    context = resolver.resolve(_USER)
    assert context.user_id == _USER
    assert context.tenant_id == _TENANT


def test_sin_pertenencia_falla_cerrado() -> None:
    resolver = TenantContextResolver(_FakeMembershipReader(()))
    with pytest.raises(TenantResolutionError):
        resolver.resolve(_USER)


def test_pertenencia_ambigua_falla_cerrado() -> None:
    resolver = TenantContextResolver(_FakeMembershipReader((_TENANT, _OTRO_TENANT)))
    with pytest.raises(TenantResolutionError):
        resolver.resolve(_USER)
