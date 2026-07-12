"""Primitivas de operador: kill switch y publicacion de version (CA-04, CA-05).

Trabajan con una conexion del ROL DE OPERADOR (nunca la de runtime). REGLA DURA
(CA-04 p.2): cada operacion ocurre en UNA SOLA TRANSACCION que escribe las TRES
cosas o ninguna: (a) el cambio de estado en la tabla, (b) la fila de
operator_audit, (c) la fila de OUTBOX con el evento policy.*. Nunca "la DB dice
bloqueado pero los procesos no se enteran": eso seria un freno de emergencia que
falla EN ABIERTO. El ENVELOPE se construye en PYTHON desde contracts/source con
el tipo de payload CONCRETO (ADR-006); jamas en SQL, jamas con triggers.

Claves de evento (ADR-003), estables para que la idempotencia funcione:
- stream_key por ARTEFACTO: "policy:kill_switch:<id>" agrupa (y ordena)
  activacion y desactivacion del mismo switch; "policy:version" agrupa las
  publicaciones.
- idempotency_key por HECHO: "policy.kill_switch_activated:<id>",
  "policy.kill_switch_deactivated:<id>", "policy.version_published:<version>".
  Si el mismo hecho se reencola (reintento DB->bus), el consumidor lo deduplica
  (inbox de P02b) en vez de aplicar el efecto dos veces.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from ce_v5.core.clock import Clock
from ce_v5.infra.db.outbox import OutboxEvent, enqueue_event
from ce_v5.infra.db.ports import Database, Session
from source.envelope import Envelope, Scope
from source.families.policy import (
    KillSwitchPayload,
    KillSwitchScope,
    PolicyEventType,
    PolicyVersionPublishedPayload,
)

_EVENT_SCHEMA_VERSION = 1

# scope del kill switch -> scope del envelope (CA-02 p.3): los de plataforma van
# como system; los dirigidos, como tenant o user.
_SYSTEM_KILL_SWITCH_SCOPES = frozenset(
    {
        KillSwitchScope.GLOBAL,
        KillSwitchScope.EXCHANGE,
        KillSwitchScope.CONNECTOR,
        KillSwitchScope.MARKET_SCOPE,
        KillSwitchScope.CAPABILITY,
    }
)


class OperatorAdminError(RuntimeError):
    """Fallo ruidoso de una operacion de operador (CA-04): jamas exito vacio."""


@dataclass(frozen=True, slots=True)
class OperatorActionResult:
    """Identificadores de lo que hizo una operacion (para informe y trazas)."""

    action: str
    event_id: str
    correlation_id: str
    kill_switch_id: str | None = None
    policy_version: str | None = None
    previous_current: str | None = None
    new_current: str | None = None


@dataclass(frozen=True, slots=True)
class KillSwitchRow:
    """Fila de kill_switch para listar (solo lectura)."""

    kill_switch_id: str
    scope: str
    target_ref: str | None
    tenant_id: str | None
    user_id: str | None
    active: bool
    reason_code: str
    actor: str


def _str_or_none(value: object) -> str | None:
    return None if value is None else str(value)


def _envelope_scope(scope: KillSwitchScope) -> Scope:
    if scope in _SYSTEM_KILL_SWITCH_SCOPES:
        return Scope.SYSTEM
    if scope is KillSwitchScope.TENANT:
        return Scope.TENANT
    return Scope.USER


class OperatorAdmin:
    """Ejecuta las acciones de operador de forma atomica y auditada (CA-04)."""

    def __init__(
        self, db: Database, clock: Clock, *, source: str = "operator.cli"
    ) -> None:
        self._db = db
        self._clock = clock
        self._source = source

    def activate_kill_switch(
        self,
        *,
        scope: KillSwitchScope,
        target_ref: str | None,
        tenant_id: str | None,
        user_id: str | None,
        reason_code: str,
        actor: str,
        correlation_id: str,
    ) -> OperatorActionResult:
        """Activa un kill switch: kill_switch + outbox + operator_audit, atomico."""
        kill_switch_id = uuid4()
        with self._db.transaction() as session:
            policy_version = self._require_current_version(session)
            # El payload valida la coherencia de scope; su regla es el ESPEJO del
            # CHECK de la tabla (0007). Si divergieran, uno rechazaria y la
            # transaccion fallaria ruidoso; no hay divergencia silenciosa.
            payload = KillSwitchPayload(
                kill_switch_id=str(kill_switch_id),
                scope=scope,
                reason_code=reason_code,
                policy_version=policy_version,
                actor=actor,
                target_ref=target_ref,
                tenant_id=tenant_id,
                user_id=user_id,
            )
            session.execute(
                "INSERT INTO kill_switch (kill_switch_id, scope, target_ref, "
                "tenant_id, user_id, reason_code, actor) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    str(kill_switch_id),
                    scope.value,
                    target_ref,
                    tenant_id,
                    user_id,
                    reason_code,
                    actor,
                ),
            )
            event_id = self._emit_kill_switch(
                session,
                payload,
                event_type=PolicyEventType.KILL_SWITCH_ACTIVATED.value,
                idempotency_key=f"policy.kill_switch_activated:{kill_switch_id}",
                stream_key=f"policy:kill_switch:{kill_switch_id}",
                correlation_id=correlation_id,
            )
            self._insert_audit(
                session,
                action="kill_switch_activated",
                actor=actor,
                reason_code=reason_code,
                kill_switch_id=str(kill_switch_id),
                policy_version=policy_version,
                previous_current=None,
                new_current=None,
                correlation_id=correlation_id,
                event_id=event_id,
            )
        return OperatorActionResult(
            action="kill_switch_activated",
            event_id=event_id,
            correlation_id=correlation_id,
            kill_switch_id=str(kill_switch_id),
        )

    def deactivate_kill_switch(
        self,
        kill_switch_id: UUID,
        *,
        reason_code: str,
        actor: str,
        correlation_id: str,
    ) -> OperatorActionResult:
        """Desactiva un kill switch existente y activo (si no, falla ruidoso)."""
        with self._db.transaction() as session:
            policy_version = self._require_current_version(session)
            rows = session.fetchall(
                "UPDATE kill_switch SET active = false, deactivated_at = now() "
                "WHERE kill_switch_id = %s AND active = true "
                "RETURNING scope, target_ref, tenant_id, user_id",
                (str(kill_switch_id),),
            )
            if not rows:
                raise OperatorAdminError(
                    f"kill switch {kill_switch_id} no existe o ya estaba inactivo."
                )
            payload = KillSwitchPayload(
                kill_switch_id=str(kill_switch_id),
                scope=KillSwitchScope(str(rows[0][0])),
                reason_code=reason_code,
                policy_version=policy_version,
                actor=actor,
                target_ref=_str_or_none(rows[0][1]),
                tenant_id=_str_or_none(rows[0][2]),
                user_id=_str_or_none(rows[0][3]),
            )
            event_id = self._emit_kill_switch(
                session,
                payload,
                event_type=PolicyEventType.KILL_SWITCH_DEACTIVATED.value,
                idempotency_key=f"policy.kill_switch_deactivated:{kill_switch_id}",
                stream_key=f"policy:kill_switch:{kill_switch_id}",
                correlation_id=correlation_id,
            )
            self._insert_audit(
                session,
                action="kill_switch_deactivated",
                actor=actor,
                reason_code=reason_code,
                kill_switch_id=str(kill_switch_id),
                policy_version=policy_version,
                previous_current=None,
                new_current=None,
                correlation_id=correlation_id,
                event_id=event_id,
            )
        return OperatorActionResult(
            action="kill_switch_deactivated",
            event_id=event_id,
            correlation_id=correlation_id,
            kill_switch_id=str(kill_switch_id),
        )

    def publish_policy_version(
        self,
        policy_version: str,
        *,
        actor: str,
        reason_code: str,
        correlation_id: str,
    ) -> OperatorActionResult:
        """Publica una version 'draft' como 'current' y supersede la anterior."""
        with self._db.transaction() as session:
            current = session.fetchone(
                "SELECT policy_version FROM policy_version WHERE status = 'current'"
            )
            previous_current = None if current is None else str(current[0])
            if previous_current is not None:
                session.execute(
                    "UPDATE policy_version SET status = 'superseded' "
                    "WHERE policy_version = %s",
                    (previous_current,),
                )
            promoted = session.fetchall(
                "UPDATE policy_version SET status = 'current', published_at = now() "
                "WHERE policy_version = %s AND status = 'draft' "
                "RETURNING policy_version",
                (policy_version,),
            )
            if not promoted:
                raise OperatorAdminError(
                    f"policy_version {policy_version} no esta en 'draft': no se "
                    "puede publicar. El indice unico parcial de 0007 garantiza "
                    "que jamas queden dos 'current'."
                )
            payload = PolicyVersionPublishedPayload(
                policy_version=policy_version,
                actor=actor,
                previous_policy_version=previous_current,
                reason=reason_code,
            )
            now_ms = self._clock.now_ms()
            idempotency_key = f"policy.version_published:{policy_version}"
            stream_key = "policy:version"
            envelope = Envelope[PolicyVersionPublishedPayload](
                event_type=PolicyEventType.VERSION_PUBLISHED.value,
                event_schema_version=_EVENT_SCHEMA_VERSION,
                source=self._source,
                idempotency_key=idempotency_key,
                stream_key=stream_key,
                scope=Scope.SYSTEM,
                event_time=now_ms,
                processing_time=now_ms,
                correlation_id=correlation_id,
                payload=payload,
            )
            self._store_outbox(
                session,
                event_id=envelope.event_id,
                event_type=envelope.event_type,
                idempotency_key=idempotency_key,
                stream_key=stream_key,
                envelope_json=envelope.model_dump(mode="json"),
            )
            event_id = str(envelope.event_id)
            self._insert_audit(
                session,
                action="policy_version_published",
                actor=actor,
                reason_code=reason_code,
                kill_switch_id=None,
                policy_version=policy_version,
                previous_current=previous_current,
                new_current=policy_version,
                correlation_id=correlation_id,
                event_id=event_id,
            )
        return OperatorActionResult(
            action="policy_version_published",
            event_id=event_id,
            correlation_id=correlation_id,
            policy_version=policy_version,
            previous_current=previous_current,
            new_current=policy_version,
        )

    def list_kill_switches(self) -> list[KillSwitchRow]:
        """Lista los kill switches (solo lectura)."""
        with self._db.transaction() as session:
            rows = session.fetchall(
                "SELECT kill_switch_id, scope, target_ref, tenant_id, user_id, "
                "active, reason_code, actor FROM kill_switch ORDER BY activated_at"
            )
        return [
            KillSwitchRow(
                kill_switch_id=str(row[0]),
                scope=str(row[1]),
                target_ref=_str_or_none(row[2]),
                tenant_id=_str_or_none(row[3]),
                user_id=_str_or_none(row[4]),
                active=bool(row[5]),
                reason_code=str(row[6]),
                actor=str(row[7]),
            )
            for row in rows
        ]

    def _require_current_version(self, session: Session) -> str:
        row = session.fetchone(
            "SELECT policy_version FROM policy_version WHERE status = 'current'"
        )
        if row is None:
            raise OperatorAdminError(
                "no hay policy_version vigente: una accion de operador sin "
                "reglamento en vigor no tiene contexto (fail-loud)."
            )
        return str(row[0])

    def _emit_kill_switch(
        self,
        session: Session,
        payload: KillSwitchPayload,
        *,
        event_type: str,
        idempotency_key: str,
        stream_key: str,
        correlation_id: str,
    ) -> str:
        now_ms = self._clock.now_ms()
        envelope = Envelope[KillSwitchPayload](
            event_type=event_type,
            event_schema_version=_EVENT_SCHEMA_VERSION,
            source=self._source,
            idempotency_key=idempotency_key,
            stream_key=stream_key,
            scope=_envelope_scope(payload.scope),
            tenant_id=payload.tenant_id,
            user_id=payload.user_id,
            event_time=now_ms,
            processing_time=now_ms,
            correlation_id=correlation_id,
            payload=payload,
        )
        self._store_outbox(
            session,
            event_id=envelope.event_id,
            event_type=event_type,
            idempotency_key=idempotency_key,
            stream_key=stream_key,
            envelope_json=envelope.model_dump(mode="json"),
        )
        return str(envelope.event_id)

    def _store_outbox(
        self,
        session: Session,
        *,
        event_id: UUID,
        event_type: str,
        idempotency_key: str,
        stream_key: str,
        envelope_json: dict[str, object],
    ) -> None:
        enqueue_event(
            session,
            OutboxEvent(
                event_id=event_id,
                idempotency_key=idempotency_key,
                stream_key=stream_key,
                event_type=event_type,
                envelope=envelope_json,
            ),
        )

    def _insert_audit(
        self,
        session: Session,
        *,
        action: str,
        actor: str,
        reason_code: str,
        kill_switch_id: str | None,
        policy_version: str | None,
        previous_current: str | None,
        new_current: str | None,
        correlation_id: str,
        event_id: str,
    ) -> None:
        session.execute(
            "INSERT INTO operator_audit (audit_id, action, actor, reason_code, "
            "kill_switch_id, policy_version, previous_current, new_current, "
            "correlation_id, event_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                str(uuid4()),
                action,
                actor,
                reason_code,
                kill_switch_id,
                policy_version,
                previous_current,
                new_current,
                correlation_id,
                event_id,
            ),
        )
