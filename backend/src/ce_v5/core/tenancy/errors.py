"""Errores de tenancy (ADR-011). Fail-closed: sin tenant resuelto, no hay operacion."""

from __future__ import annotations


class TenancyError(RuntimeError):
    """Error de tenancy."""


class TenantResolutionError(TenancyError):
    """No se puede resolver un tenant efectivo para el principal autenticado.

    Es un fallo CERRADO: la operacion no continua. Se produce si el usuario no
    tiene pertenencia valida, o si su pertenencia es ambigua.
    """
