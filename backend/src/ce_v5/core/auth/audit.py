"""Auditoria de autenticacion, PARTIDA EN DOS (P06b, dictamen CSA N).

LOS HECHOS PRE-IDENTIDAD NO TIENEN DUENO: un login fallido, una denegacion del limitador
o un CSRF rechazado ocurren cuando todavia NO SABEMOS QUIEN LLAMA (esa es justo la
cuestion). No se pueden meter en la auditoria por sujeto (sensitive_action_audit), que
esta protegida por tenant: NO HAY TENANT QUE PONERLES. Y guardar ahi los emails que
fallan seria construir una lista de emails atacados. Van a LOGS ESTRUCTURADOS, con
huellas, nunca con el email.

LOS HECHOS POST-IDENTIDAD SI TIENEN DUENO: un login correcto, un refresh rotado, un
logout o una sesion revocada tienen usuario y tenant. Esos SI van a
sensitive_action_audit.

Esta frontera no es estetica: mezclarlas habria envenenado la RLS de la tabla de sujeto,
que es el mismo error que P06 evito al separar sus tres auditorias (CA-05).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

# capability_id de los hechos de autenticacion. NO son capacidades del catalogo de
# politica (no las evalua el gate): son el nombre del hecho en la traza.
CAPABILITY_REGISTER = "auth.register"
CAPABILITY_LOGIN = "auth.login"
CAPABILITY_REFRESH = "auth.refresh"
CAPABILITY_LOGOUT = "auth.logout"
CAPABILITY_REFRESH_REUSED = "auth.refresh_reused"


@runtime_checkable
class AuthAuditor(Protocol):
    """Los cuatro primeros son PRE-identidad; los demas, POST-identidad."""

    def login_failed(
        self, *, account_digest: str, ip_digest: str | None, reason: str
    ) -> None: ...

    def rate_limited(
        self,
        *,
        action: str,
        account_digest: str | None,
        ip_digest: str | None,
        dimension: str,
    ) -> None: ...

    def limiter_unavailable(self, *, action: str) -> None: ...

    def csrf_rejected(self, *, path: str) -> None: ...

    def registered(self, *, user_id: UUID) -> None:
        """POST-identidad: el alta es ATOMICA, asi que en cuanto existe el usuario
        existe su tenant, y el hecho tiene dueno desde el primer instante."""
        ...

    def login_succeeded(self, *, user_id: UUID) -> None: ...

    def refresh_rotated(self, *, user_id: UUID) -> None: ...

    def refresh_reused(self, *, user_id: UUID) -> None: ...

    def logged_out(self, *, user_id: UUID, sessions_revoked: int) -> None: ...
