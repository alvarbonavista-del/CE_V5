"""Familia signal.* : proyeccion de una TradingSignalRule (ADR-004, CA-P08-01).

Proyeccion DERIVADA de rule.*: cuando una TradingSignalRule pasa a FIRING se proyecta
signal.raised con causation_id = event_id(rule.firing) (envelope, ADR-003). Consumidor
primario: el overlay grafico universal (INFORME 6 sec 16); el drill-down al
EvaluationResult se sigue por correlation_id hasta rule.evaluation_completed, no se
duplica aqui. v5.0 declara SOLO signal.raised (CA-P08-01 p.8).
"""

from enum import StrEnum
from uuid import UUID

from pydantic import ConfigDict, Field

from source.envelope import EventPayload
from source.families.market import EXCHANGE_PATTERN, SYMBOL_PATTERN


class SignalEventType(StrEnum):
    """Tipos signal.* (v5.0: solo la proyeccion en flanco de subida)."""

    RAISED = "signal.raised"


class SignalRaisedPayload(EventPayload):
    """signal.raised: senal de trading proyectada desde una regla en FIRING."""

    model_config = ConfigDict(extra="forbid")

    signal_id: UUID
    rule_id: UUID
    tenant_id: UUID
    canonical_rule_hash: str
    exchange: str = Field(pattern=EXCHANGE_PATTERN)
    symbol: str = Field(pattern=SYMBOL_PATTERN)
