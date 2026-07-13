"""Auditor de autenticacion cableado (P06b, dictamen CSA N).

PRE-IDENTIDAD -> LOG. Un login fallido no tiene dueno: no hay tenant al que atarlo, y
guardar el email que falla seria construir la lista de emails atacados. Va al log, con
HUELLAS. Estos metodos NO TOCAN LA BASE.

POST-IDENTIDAD -> sensitive_action_audit, por el adaptador de P06. Ese adaptador fija
POR SI MISMO el contexto de tenant (abre la transaccion con TenantScopedDatabase a
partir del user_id del registro), asi que aqui NO se envuelve en otra transaccion; lo
unico que hace falta antes es RESOLVER el tenant, porque la fila lo lleva.

TENSION RESUELTA (CA-11 firmada): antes se FORZABAN motivos de politica para hechos de
auth, es decir, se escribia una traza que MENTIA en su columna de motivo. Ya no. Ahora:
  - cada fila declara su audit_kind ('auth' aqui, 'policy' en el gate), y cada tipo usa
    SU vocabulario de reason_code (los AUTH_* de CA-11). PROHIBIDO tomar prestado un
    motivo de politica para un hecho de autenticacion;
  - policy_version, para audit_kind=auth, es la version que estaba VIGENTE en ese
    instante: CONTEXTO, no fundamento (un login no lo decide la politica). Si no hay
    ninguna vigente se escribe el centinela explicito "none".

FALLO DE ESCRITURA: si la auditoria POST-identidad falla, NO se rompe el login: se
registra un error en el log con su correlation_id. Aqui NO aplica el "si no se puede
auditar, no se permite" de P06/D8, porque ese principio protege ACCIONES SENSIBLES
(dinero, claves de exchange); negar la entrada a TODO EL MUNDO porque falla una
escritura de traza seria un fallo de disponibilidad autoinfligido. La accion sensible
sigue con su regla dura intacta en el gate.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from ce_v5.core.auth.audit import (
    CAPABILITY_LOGIN,
    CAPABILITY_LOGOUT,
    CAPABILITY_REFRESH,
    CAPABILITY_REFRESH_REUSED,
    CAPABILITY_REGISTER,
)
from ce_v5.core.policy.audit import (
    AUDIT_KIND_AUTH,
    SensitiveActionAudit,
    SensitiveActionRecord,
)
from ce_v5.core.policy.decisions import Decision, ReasonCode
from ce_v5.core.policy.ports import PolicyStore
from ce_v5.entrypoints.api.observability import log_event
from ce_v5.infra.db.ports import Database
from ce_v5.infra.db.tenancy import TenantScopedDatabase

# Centinela EXPLICITO cuando no hay reglamento vigente. Dice la verdad: no habia
# ninguno.
NO_POLICY_VERSION = "none"


class ApiAuthAuditor:
    """Cumple AuthAuditor: log para lo anonimo, tabla para lo que tiene dueno."""

    def __init__(
        self,
        database: Database,
        audit: SensitiveActionAudit,
        policy_versions: PolicyStore | None = None,
    ) -> None:
        self._scoped = TenantScopedDatabase(database)
        self._audit = audit
        # De quien se aprende que version estaba VIGENTE (contexto, no fundamento). Es
        # opcional para no obligar a cablearlo donde la traza no lo necesita; sin el, se
        # escribe el centinela explicito.
        self._policy_versions = policy_versions

    # --- PRE-identidad: LOG. Nunca tocan la base. ---------------------------------

    def login_failed(
        self, *, account_digest: str, ip_digest: str | None, reason: str
    ) -> None:
        # El motivo tecnico se queda AQUI: al cliente le llega el mensaje generico.
        log_event(
            "auth.login_failed",
            account=account_digest,
            ip=ip_digest,
            reason=reason,
        )

    def rate_limited(
        self,
        *,
        action: str,
        account_digest: str | None,
        ip_digest: str | None,
        dimension: str,
    ) -> None:
        log_event(
            "auth.rate_limited",
            action=action,
            account=account_digest,
            ip=ip_digest,
            dimension=dimension,
        )

    def limiter_unavailable(self, *, action: str) -> None:
        log_event("auth.limiter_unavailable", action=action)

    def csrf_rejected(self, *, path: str) -> None:
        log_event("auth.csrf_rejected", path=path)

    # --- POST-identidad: sensitive_action_audit ------------------------------------

    def registered(self, *, user_id: UUID) -> None:
        # Una cuenta que nace y opera sin dejar rastro seria un agujero de auditoria. El
        # alta es atomica: hay usuario y tenant desde el primer instante, asi que el
        # hecho tiene dueno y le corresponde su fila.
        self._record(
            user_id,
            CAPABILITY_REGISTER,
            Decision.ALLOW,
            ReasonCode.AUTH_REGISTERED,
            {"event": "register"},
        )

    def login_succeeded(self, *, user_id: UUID) -> None:
        self._record(
            user_id,
            CAPABILITY_LOGIN,
            Decision.ALLOW,
            ReasonCode.AUTH_LOGIN_SUCCEEDED,
            {"event": "login"},
        )

    def refresh_rotated(self, *, user_id: UUID) -> None:
        self._record(
            user_id,
            CAPABILITY_REFRESH,
            Decision.ALLOW,
            ReasonCode.AUTH_REFRESH_ROTATED,
            {"event": "refresh_rotated"},
        )

    def refresh_reused(self, *, user_id: UUID) -> None:
        # Un token gastado que reaparece significa ROBO: la familia entera cae.
        self._record(
            user_id,
            CAPABILITY_REFRESH_REUSED,
            Decision.DENY,
            ReasonCode.AUTH_REFRESH_REUSED,
            {"event": "refresh_reused"},
        )

    def logged_out(self, *, user_id: UUID, sessions_revoked: int) -> None:
        self._record(
            user_id,
            CAPABILITY_LOGOUT,
            Decision.ALLOW,
            ReasonCode.AUTH_LOGGED_OUT,
            {"event": "logout", "sessions_revoked": sessions_revoked},
        )

    def _policy_version(self) -> str:
        """La version VIGENTE, como CONTEXTO. Un login no lo decide la politica."""
        if self._policy_versions is None:
            return NO_POLICY_VERSION
        try:
            return self._policy_versions.current_policy_version() or NO_POLICY_VERSION
        except Exception:  # noqa: BLE001
            return NO_POLICY_VERSION

    def _record(
        self,
        user_id: UUID,
        capability_id: str,
        decision: Decision,
        reason_code: ReasonCode,
        context: Mapping[str, object],
    ) -> None:
        try:
            # El adaptador de P06 fija el contexto de tenant por si mismo; lo unico que
            # hay que resolver antes es el tenant, porque la fila lo lleva.
            with self._scoped.transaction(user_id) as scoped:
                tenant_id = str(scoped.context.tenant_id)
            self._audit.record(
                SensitiveActionRecord(
                    tenant_id=tenant_id,
                    user_id=str(user_id),
                    capability_id=capability_id,
                    decision=decision,
                    # Motivo del vocabulario de AUTH (CA-11). PROHIBIDO tomar
                    # prestado un motivo de politica para un hecho de autenticacion.
                    reason_code=reason_code,
                    policy_version=self._policy_version(),
                    # No son capacidades sensibles del catalogo (D1 de P06).
                    sensitive=False,
                    context=context,
                    audit_kind=AUDIT_KIND_AUTH,
                )
            )
        except Exception as exc:  # noqa: BLE001
            # No se rompe la sesion por un fallo de traza (ver docstring del modulo).
            log_event(
                "auth.audit_write_failed",
                capability_id=capability_id,
                error=type(exc).__name__,
            )
