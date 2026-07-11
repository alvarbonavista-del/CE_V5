"""TenantContextResolver: el tenant efectivo lo resuelve el BACKEND (ADR-011).

Regla dura: el tenant se deriva de la identidad autenticada y de la pertenencia
registrada. El cliente NUNCA lo impone; por eso resolve() no acepta un tenant
como argumento: no hay forma de pasarselo. Sin pertenencia valida, la operacion
FALLA CERRADA (TenantResolutionError), nunca degrada a un tenant por defecto.
"""

from __future__ import annotations

from uuid import UUID

from ce_v5.core.tenancy.context import TenantContext
from ce_v5.core.tenancy.errors import TenantResolutionError
from ce_v5.core.tenancy.ports import MembershipReader


class TenantContextResolver:
    """Resuelve el TenantContext de un principal ya autenticado."""

    def __init__(self, memberships: MembershipReader) -> None:
        self._memberships = memberships

    def resolve(self, user_id: UUID) -> TenantContext:
        """Resuelve el tenant efectivo del usuario autenticado.

        Falla cerrado si no hay pertenencia (ninguna) o si es ambigua (varias).
        En v5.0 el tenant coincide 1:1 con el usuario: una pertenencia unica.
        Las organizaciones (varias pertenencias con seleccion explicita) no se
        soportan en producto; la costura queda abierta en el modelo de datos.
        """
        tenants = tuple(self._memberships.tenants_for_user(user_id))
        if not tenants:
            raise TenantResolutionError(
                f"El usuario {user_id} no tiene pertenencia valida a ningun "
                "tenant: la operacion falla cerrada (ADR-011)."
            )
        if len(tenants) > 1:
            raise TenantResolutionError(
                f"El usuario {user_id} tiene {len(tenants)} pertenencias: la "
                "resolucion es ambigua y falla cerrada. v5.0 no soporta "
                "organizaciones (ADR-011)."
            )
        return TenantContext(user_id=user_id, tenant_id=tenants[0])
