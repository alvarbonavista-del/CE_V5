"""PostgresSensitiveActionAudit: escribe la auditoria por sujeto (ADR-012, CA-05).

Implementa el puerto SensitiveActionAudit escribiendo en sensitive_action_audit
BAJO TenantScopedDatabase (regla dura de P05: jamas conexion cruda). El rol de
aplicacion tiene INSERT; NO tiene UPDATE ni DELETE, y eso es deliberado: una
auditoria editable no es una auditoria (append-only, hecho cumplir por el motor,
no por el codigo).

Fail-loud: si no puede escribir (rol sin permiso, RLS, sujeto sin tenant), la
excepcion PROPAGA. Una decision sensible sin traza es un agujero de cumplimiento,
no un detalle; el gate (B8) decidira que hacer con ese fallo, aqui solo se eleva.
El driver solo lo conoce el adapter (REST-15).
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from ce_v5.core.policy.audit import SensitiveActionRecord
from ce_v5.infra.db.ports import Database
from ce_v5.infra.db.tenancy import TenantScopedDatabase

_INSERT_SQL = (
    "INSERT INTO sensitive_action_audit "
    "(audit_id, tenant_id, user_id, capability_id, decision, reason_code, "
    "policy_version, sensitive, context, audit_kind) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)"
)


class SensitiveAuditError(RuntimeError):
    """No se pudo trazar una accion sensible (fail-loud, CA-05)."""


class PostgresSensitiveActionAudit:
    """Escribe la auditoria de accion sensible bajo RLS (cumple el puerto)."""

    def __init__(self, database: Database) -> None:
        self._scoped = TenantScopedDatabase(database)

    def record(self, entry: SensitiveActionRecord) -> None:
        if entry.user_id is None:
            raise SensitiveAuditError(
                "sensitive_action_audit exige user_id para fijar el contexto de "
                "tenant (RLS de P05): una decision sin sujeto no se puede trazar."
            )
        with self._scoped.transaction(UUID(entry.user_id)) as scoped:
            scoped.session.execute(
                _INSERT_SQL,
                (
                    str(uuid4()),
                    entry.tenant_id,
                    entry.user_id,
                    entry.capability_id,
                    entry.decision.value,
                    entry.reason_code.value,
                    entry.policy_version,
                    entry.sensitive,
                    json.dumps(dict(entry.context)),
                    entry.audit_kind,
                ),
            )
