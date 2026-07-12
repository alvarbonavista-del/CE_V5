"""PolicyStore sobre PostgreSQL, SOLO LECTURA (ADR-012, B4b).

Implementa el Protocol PolicyStore de core/policy: alimenta al PolicyEvaluator
leyendo el reglamento vigente, concesiones, overrides y kill switches. NUNCA
escribe: la escritura es de las migraciones (catalogo), del rol de aplicacion
bajo RLS (entitlements/overrides) o del operador (kill switches), en otras
piezas.

Disciplina de P05 (regla dura): las lecturas TENANT-SCOPED (entitlements,
overrides) van dentro de una transaccion con el contexto de tenant/usuario
fijado por el resolver (TenantScopedDatabase), jamas conexion cruda. Las
lecturas de SISTEMA (policy_version vigente, reglas, kill switches activos)
usan el rol de aplicacion, que tiene SELECT sobre esas tablas (policies
USING true, B2).

D7 (fail-loud): si una fila trae un effect o un reason_code fuera del catalogo,
se lanza PolicyDataError con la tabla, el id y el valor ofensivo. No se corrige,
no se ignora, no se adivina. El driver solo lo conoce el adapter (REST-15).
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from ce_v5.core.policy.decisions import ReasonCode
from ce_v5.core.policy.ports import (
    EntitlementRecord,
    KillSwitchRecord,
    OverrideRecord,
    PolicyRuleRecord,
)
from ce_v5.infra.db.ports import Database
from ce_v5.infra.db.tenancy import TenantScopedDatabase

_VALID_EFFECTS = frozenset({"allow", "deny"})
_VALID_REASON_CODES = frozenset(code.value for code in ReasonCode)

_CURRENT_VERSION_SQL = (
    "SELECT policy_version FROM policy_version WHERE status = 'current'"
)

_RULES_SQL = """
SELECT rule_id, capability_id, effect, reason_code, match_jurisdiction,
       match_plan, match_role, match_kyc_status, match_vpn
FROM policy_rule
WHERE policy_version = %s
"""

# expires_at (timestamptz) -> epoch ms int (ADR-007); NULL se conserva como NULL.
_ENTITLEMENTS_SQL = """
SELECT capability_id, source,
       (extract(epoch from expires_at) * 1000)::bigint
FROM policy_entitlement
WHERE tenant_id = %s AND (user_id IS NULL OR user_id = %s)
"""

_OVERRIDES_SQL = """
SELECT override_id, capability_id, effect, reason_code,
       (extract(epoch from expires_at) * 1000)::bigint
FROM policy_override
WHERE tenant_id = %s AND (user_id IS NULL OR user_id = %s)
"""

_KILL_SWITCHES_SQL = """
SELECT kill_switch_id, scope, target_ref, tenant_id, user_id
FROM kill_switch
WHERE active = true
"""


class PolicyDataError(RuntimeError):
    """Una fila de politica tiene un valor fuera del catalogo (D7, fail-loud)."""


def _text_or_none(value: object) -> str | None:
    return None if value is None else str(value)


def _bool_or_none(value: object) -> bool | None:
    return None if value is None else bool(value)


def _epoch_ms_or_none(value: object) -> int | None:
    if value is None:
        return None
    assert isinstance(value, int)
    return value


def _require_effect(effect: str, table: str, row_id: str) -> None:
    if effect not in _VALID_EFFECTS:
        raise PolicyDataError(
            f"{table} {row_id}: effect {effect!r} fuera del catalogo "
            "('allow'|'deny'). El motor es fail-loud ante datos invalidos (D7)."
        )


def _require_reason_code(reason_code: str, table: str, row_id: str) -> None:
    if reason_code not in _VALID_REASON_CODES:
        raise PolicyDataError(
            f"{table} {row_id}: reason_code {reason_code!r} fuera del catalogo "
            "ReasonCode. El motor es fail-loud ante datos invalidos (D7)."
        )


def _rule_from_row(row: tuple[object, ...]) -> PolicyRuleRecord:
    rule_id = str(row[0])
    effect = str(row[2])
    reason_code = str(row[3])
    _require_effect(effect, "policy_rule", rule_id)
    _require_reason_code(reason_code, "policy_rule", rule_id)
    return PolicyRuleRecord(
        rule_id=rule_id,
        capability_id=str(row[1]),
        effect=effect,
        reason_code=reason_code,
        match_jurisdiction=_text_or_none(row[4]),
        match_plan=_text_or_none(row[5]),
        match_role=_text_or_none(row[6]),
        match_kyc_status=_text_or_none(row[7]),
        match_vpn=_bool_or_none(row[8]),
    )


def _entitlement_from_row(row: tuple[object, ...]) -> EntitlementRecord:
    return EntitlementRecord(
        capability_id=str(row[0]),
        source=str(row[1]),
        expires_at=_epoch_ms_or_none(row[2]),
    )


def _override_from_row(row: tuple[object, ...]) -> OverrideRecord:
    override_id = str(row[0])
    effect = str(row[2])
    reason_code = str(row[3])
    _require_effect(effect, "policy_override", override_id)
    _require_reason_code(reason_code, "policy_override", override_id)
    return OverrideRecord(
        capability_id=str(row[1]),
        effect=effect,
        reason_code=reason_code,
        expires_at=_epoch_ms_or_none(row[4]),
    )


def _kill_switch_from_row(row: tuple[object, ...]) -> KillSwitchRecord:
    return KillSwitchRecord(
        kill_switch_id=str(row[0]),
        scope=str(row[1]),
        target_ref=_text_or_none(row[2]),
        tenant_id=_text_or_none(row[3]),
        user_id=_text_or_none(row[4]),
    )


class PostgresPolicyStore:
    """Lectura de politica sobre PostgreSQL (cumple el Protocol PolicyStore)."""

    def __init__(self, database: Database) -> None:
        self._database = database
        self._scoped = TenantScopedDatabase(database)

    def current_policy_version(self) -> str | None:
        with self._database.transaction() as session:
            row = session.fetchone(_CURRENT_VERSION_SQL)
        return None if row is None else str(row[0])

    def rules(self, policy_version: str) -> Sequence[PolicyRuleRecord]:
        with self._database.transaction() as session:
            rows = session.fetchall(_RULES_SQL, (policy_version,))
        return [_rule_from_row(row) for row in rows]

    def entitlements(
        self, tenant_id: str, user_id: str | None
    ) -> Sequence[EntitlementRecord]:
        user_uuid = _require_user(user_id, "entitlements")
        with self._scoped.transaction(user_uuid) as scoped:
            rows = scoped.session.fetchall(_ENTITLEMENTS_SQL, (tenant_id, user_id))
        return [_entitlement_from_row(row) for row in rows]

    def overrides(
        self, tenant_id: str, user_id: str | None
    ) -> Sequence[OverrideRecord]:
        user_uuid = _require_user(user_id, "overrides")
        with self._scoped.transaction(user_uuid) as scoped:
            rows = scoped.session.fetchall(_OVERRIDES_SQL, (tenant_id, user_id))
        return [_override_from_row(row) for row in rows]

    def active_kill_switches(self) -> Sequence[KillSwitchRecord]:
        with self._database.transaction() as session:
            rows = session.fetchall(_KILL_SWITCHES_SQL)
        return [_kill_switch_from_row(row) for row in rows]


def _require_user(user_id: str | None, operation: str) -> UUID:
    """Las lecturas tenant-scoped exigen un usuario (RLS de P05, tenant 1:1)."""
    if user_id is None:
        raise PolicyDataError(
            f"{operation}: falta user_id; una lectura tenant-scoped no puede "
            "resolver el contexto de tenant sin un principal (P05)."
        )
    return UUID(user_id)
