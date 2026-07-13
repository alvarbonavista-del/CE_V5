"""Tests de integracion de AISLAMIENTO entre tenants (ADR-011).

Ejercitan el RLS real con el ROL DE APLICACION (app_db): lecturas y
escrituras cross-tenant bloqueadas, y resolucion fail-closed. El driver solo
lo conoce el adapter, asi que aqui no se importa psycopg (REST-15). NUNCA
datos reales: base de juguete (DOC_ENTREGABLES sec.5).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from ce_v5.core.tenancy.errors import TenancyError, TenantResolutionError
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.infra.db.tenancy import (
    MembershipRepository,
    TenantScopedDatabase,
    provision_tenant_for_user,
)

_DSN = os.environ.get("CE_V5_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    _DSN is None, reason="requiere CE_V5_DATABASE_URL (PostgreSQL local)"
)


@dataclass(frozen=True, slots=True)
class _TwoTenants:
    """Dos tenants recien creados, cada uno con su usuario. Aislados por uuid4."""

    user_a: UUID
    tenant_a: UUID
    user_b: UUID
    tenant_b: UUID


@pytest.fixture
def tenants(app_db: PsycopgDatabase, crear_usuario: Callable[[], UUID]) -> _TwoTenants:
    user_a, user_b = crear_usuario(), crear_usuario()
    tenant_a = provision_tenant_for_user(app_db, user_a)
    tenant_b = provision_tenant_for_user(app_db, user_b)
    return _TwoTenants(user_a, tenant_a, user_b, tenant_b)


def test_provision_crea_pertenencia_unica_y_rechaza_segundo(
    app_db: PsycopgDatabase, crear_usuario: Callable[[], UUID]
) -> None:
    user = crear_usuario()
    provision_tenant_for_user(app_db, user)

    scoped_db = TenantScopedDatabase(app_db)
    with scoped_db.transaction(user) as scoped:
        assert MembershipRepository(scoped).members() == [user]

    with pytest.raises(TenancyError):
        provision_tenant_for_user(app_db, user)


def test_lectura_aislada_por_tenant(
    app_db: PsycopgDatabase, tenants: _TwoTenants
) -> None:
    scoped_db = TenantScopedDatabase(app_db)
    with scoped_db.transaction(tenants.user_a) as scoped:
        assert MembershipRepository(scoped).members() == [tenants.user_a]
        rows = scoped.session.fetchall("SELECT tenant_id FROM tenant")
        visible = [UUID(str(row[0])) for row in rows]
        assert visible == [tenants.tenant_a]


def test_fuga_lectura_y_borrado_cross_tenant_bloqueada(
    app_db: PsycopgDatabase, tenants: _TwoTenants
) -> None:
    scoped_db = TenantScopedDatabase(app_db)
    with scoped_db.transaction(tenants.user_a) as scoped:
        leak = scoped.session.fetchall(
            "SELECT tenant_id FROM tenant WHERE tenant_id = %s",
            (str(tenants.tenant_b),),
        )
        assert leak == []
        # El DELETE no ve la fila de B (RLS): no borra nada, sin error.
        scoped.session.execute(
            "DELETE FROM tenant WHERE tenant_id = %s", (str(tenants.tenant_b),)
        )

    # La fila de B sigue existiendo, comprobado bajo el contexto de B.
    with scoped_db.transaction(tenants.user_b) as scoped:
        rows = scoped.session.fetchall(
            "SELECT tenant_id FROM tenant WHERE tenant_id = %s",
            (str(tenants.tenant_b),),
        )
        assert [UUID(str(row[0])) for row in rows] == [tenants.tenant_b]


def test_escritura_cross_tenant_bloqueada(
    app_db: PsycopgDatabase, tenants: _TwoTenants
) -> None:
    scoped_db = TenantScopedDatabase(app_db)
    with pytest.raises(Exception) as excinfo:
        with scoped_db.transaction(tenants.user_a) as scoped:
            # Insertar una pertenencia al tenant de B viola WITH CHECK del RLS.
            scoped.session.execute(
                "INSERT INTO user_tenant_membership (user_id, tenant_id) "
                "VALUES (%s, %s)",
                (str(uuid4()), str(tenants.tenant_b)),
            )
    assert "row-level security" in str(excinfo.value).lower()


def test_sin_pertenencia_falla_cerrado(app_db: PsycopgDatabase) -> None:
    scoped_db = TenantScopedDatabase(app_db)
    huerfano = uuid4()
    with pytest.raises(TenantResolutionError):
        with scoped_db.transaction(huerfano):
            pytest.fail("no debe ejecutarse el cuerpo: falla al resolver")
