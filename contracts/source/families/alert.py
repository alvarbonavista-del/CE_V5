"""Familia alert.* : proyeccion de una AlertRule (ADR-004, CA-P08-01).

Proyeccion DERIVADA de rule.*: cuando una AlertRule pasa a FIRING se proyecta
alert.raised con causation_id = event_id(rule.firing) (envelope, ADR-003). Consumidor
primario: el router de notificacion (P09a). v5.0 declara SOLO alert.raised (CA-P08-01
p.8). alert.acknowledged pertenece al ciclo de ATENCION/ENTREGA (p.7): lo produce el
router de entrega y lo declara P09a, NO P08.
"""

from enum import StrEnum
from uuid import UUID

from pydantic import ConfigDict, Field

from source.envelope import EventPayload
from source.families.market import EXCHANGE_PATTERN, SYMBOL_PATTERN


class AlertEventType(StrEnum):
    """Tipos alert.* que produce P08 (v5.0: solo la proyeccion en flanco de subida)."""

    RAISED = "alert.raised"


class AlertRaisedPayload(EventPayload):
    """alert.raised: aviso proyectado desde una regla en FIRING."""

    model_config = ConfigDict(extra="forbid")

    alert_id: UUID
    rule_id: UUID
    tenant_id: UUID
    canonical_rule_hash: str
    exchange: str = Field(pattern=EXCHANGE_PATTERN)
    symbol: str = Field(pattern=SYMBOL_PATTERN)
    notification_policy_ref: UUID | None = None
