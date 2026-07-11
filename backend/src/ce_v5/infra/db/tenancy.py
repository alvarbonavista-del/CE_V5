"""Sesion transaccional con contexto de tenant sobre PostgreSQL (ADR-011).

Implementa la disciplina de RLS del ADR-011 en el camino del codigo:
- el tenant efectivo lo resuelve el BACKEND (TenantContextResolver) desde el
  principal autenticado y la pertenencia; el cliente nunca lo impone;
- el contexto se fija con SET LOCAL DENTRO de la transaccion (set_config con
  is_local=true es exactamente SET LOCAL, pero admite parametros, de modo que
  el identificador nunca se interpola en el SQL);
- sin pertenencia valida la transaccion NO se abre (fail-closed);
- el rol de conexion se verifica: si tuviera SUPERUSER o BYPASSRLS, el RLS
  seria decorativo, asi que se rechaza operar (fail-closed, no un aviso).

Defensa en profundidad: los repositorios de este modulo filtran ADEMAS por
tenant_id en la propia consulta, sin confiar solo en el RLS.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from uuid import UUID, uuid4

from ce_v5.core.tenancy.context import TenantContext
from ce_v5.core.tenancy.errors import TenancyError
from ce_v5.core.tenancy.resolver import TenantContextResolver
from ce_v5.infra.db.ports import Database, Session

# set_config(..., true) == SET LOCAL: el ajuste se descarta al terminar la
# transaccion (correcto con pools de conexiones).
_SET_CURRENT_USER = "SELECT set_config('app.current_user_id', %s, true)"
_SET_CURRENT_TENANT = "SELECT set_config('app.current_tenant_id', %s, true)"

_ROLE_ATTRS = "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"

_MEMBERSHIPS_OF_USER = "SELECT tenant_id FROM user_tenant_membership WHERE user_id = %s"


class AppRoleError(TenancyError):
    """El rol de conexion puede saltarse el RLS: no se opera (ADR-011)."""


def assert_app_role_cannot_bypass_rls(session: Session) -> None:
    """Verifica que el rol conectado no tiene SUPERUSER ni BYPASSRLS."""
    row = session.fetchone(_ROLE_ATTRS)
    if row is None:
        raise AppRoleError("No se pudo verificar el rol de conexion actual.")
    is_superuser, can_bypass_rls = bool(row[0]), bool(row[1])
    if is_superuser or can_bypass_rls:
        raise AppRoleError(
            "El rol de conexion tiene SUPERUSER o BYPASSRLS: las policies de "
            "RLS no le aplicarian y el aislamiento entre tenants seria "
            "decorativo. La aplicacion no opera con este rol (ADR-011)."
        )


class _SessionMembershipReader:
    """Lee la pertenencia del principal autenticado dentro de la transaccion.

    Cumple el puerto MembershipReader del nucleo. La policy de RLS de
    user_tenant_membership permite al principal leer SUS filas (por
    app.current_user_id, fijado por el backend), que es justo lo que el
    resolver necesita antes de conocer el tenant.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def tenants_for_user(self, user_id: UUID) -> Sequence[UUID]:
        rows = self._session.fetchall(_MEMBERSHIPS_OF_USER, (str(user_id),))
        return [UUID(str(row[0])) for row in rows]


class TenantScopedSession:
    """Sesion dentro de una transaccion con tenant efectivo ya fijado."""

    def __init__(self, session: Session, context: TenantContext) -> None:
        self._session = session
        self._context = context

    @property
    def context(self) -> TenantContext:
        """Principal autenticado y tenant efectivo de esta transaccion."""
        return self._context

    @property
    def session(self) -> Session:
        """Sesion SQL subyacente, ya bajo contexto de tenant."""
        return self._session


class TenantScopedDatabase:
    """Abre transacciones con el contexto de tenant resuelto por el backend."""

    def __init__(self, database: Database) -> None:
        self._database = database

    @contextmanager
    def transaction(self, user_id: UUID) -> Iterator[TenantScopedSession]:
        """Abre una transaccion para un principal YA AUTENTICADO.

        No admite un tenant como argumento a proposito: el cliente no puede
        imponerlo. Si el usuario no tiene pertenencia valida, lanza
        TenantResolutionError y la transaccion se deshace sin tocar nada.
        """
        with self._database.transaction() as session:
            assert_app_role_cannot_bypass_rls(session)
            session.execute(_SET_CURRENT_USER, (str(user_id),))
            resolver = TenantContextResolver(_SessionMembershipReader(session))
            context = resolver.resolve(user_id)
            session.execute(_SET_CURRENT_TENANT, (str(context.tenant_id),))
            yield TenantScopedSession(session, context)


class MembershipRepository:
    """Pertenencias del tenant en curso.

    Defensa en profundidad (ADR-011): filtra por tenant_id en la consulta
    ADEMAS de estar protegido por RLS. Si un dia fallara una policy, el
    filtro de aplicacion sigue en pie, y viceversa.
    """

    def __init__(self, scoped: TenantScopedSession) -> None:
        self._scoped = scoped

    def members(self) -> list[UUID]:
        """Usuarios que pertenecen al tenant en curso."""
        rows = self._scoped.session.fetchall(
            "SELECT user_id FROM user_tenant_membership WHERE tenant_id = %s",
            (str(self._scoped.context.tenant_id),),
        )
        return [UUID(str(row[0])) for row in rows]

    def add_member(self, user_id: UUID) -> None:
        """Anade un usuario al tenant en curso (nunca a otro tenant)."""
        self._scoped.session.execute(
            "INSERT INTO user_tenant_membership (user_id, tenant_id) VALUES (%s, %s)",
            (str(user_id), str(self._scoped.context.tenant_id)),
        )


def provision_tenant_for_user(database: Database, user_id: UUID) -> UUID:
    """Crea el tenant de un usuario nuevo y su pertenencia unica (ADR-011).

    En v5.0 el tenant coincide 1:1 con el usuario (B2C): la pertenencia se
    inicializa automaticamente, sin soportar organizaciones en producto. La
    costura queda abierta en el modelo de datos, no en este alta.

    Se ejecuta con el rol de aplicacion, bajo el contexto del tenant que se
    esta creando: por eso las policies (WITH CHECK) lo aceptan sin excepcion
    alguna al RLS. Si el usuario ya tuviera pertenencia, falla: crear un
    segundo tenant lo dejaria en resolucion ambigua.
    """
    tenant_id = uuid4()
    with database.transaction() as session:
        assert_app_role_cannot_bypass_rls(session)
        session.execute(_SET_CURRENT_USER, (str(user_id),))
        existing = session.fetchall(_MEMBERSHIPS_OF_USER, (str(user_id),))
        if existing:
            raise TenancyError(
                f"El usuario {user_id} ya tiene pertenencia a un tenant: no se "
                "crea otro (la resolucion quedaria ambigua)."
            )
        session.execute(_SET_CURRENT_TENANT, (str(tenant_id),))
        session.execute("INSERT INTO tenant (tenant_id) VALUES (%s)", (str(tenant_id),))
        session.execute(
            "INSERT INTO user_tenant_membership (user_id, tenant_id) VALUES (%s, %s)",
            (str(user_id), str(tenant_id)),
        )
    return tenant_id
