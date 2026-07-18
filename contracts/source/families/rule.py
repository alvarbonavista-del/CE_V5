"""Familia rule.* : el ciclo de evaluacion neutral (ADR-004, ADR-015, CA-P08-01).

rule.* es la FUENTE DE VERDAD NEUTRAL del evaluation lifecycle. Se emite SOLO por
TRANSICION de estado con deduplicacion (CA-P08-01, firmada): una evaluacion que deja la
regla en el mismo estado no emite nada. En una transicion a FIRING se emiten DOS
eventos: rule.evaluation_completed (EvaluationResult granular) y rule.firing (flanco
semantico, ancla causal de signal.*/alert.*). Idem RESOLVED con rule.resolved.

La distincion False vs NO-EVALUABLE por dato ausente (INFORME 6 sec 9.1) se conserva en
NodeOutcome. El causation_id y el tiempo viven en el ENVELOPE (ADR-003/007), no aqui.
"""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from source.envelope import EventPayload


class RuleEventType(StrEnum):
    """Tipos rule.* (CA-P08-01)."""

    EVALUATION_COMPLETED = "rule.evaluation_completed"
    FIRING = "rule.firing"
    RESOLVED = "rule.resolved"


class EvaluationLifecycleState(StrEnum):
    """Estados del ciclo de evaluacion (INFORME 6 sec 11.4)."""

    INACTIVE = "inactive"
    PENDING = "pending"
    FIRING = "firing"
    RESOLVED = "resolved"


class NodeOutcome(StrEnum):
    """Resultado de un nodo. NOT_EVALUABLE (dato ausente) NO es FALSE (INFORME 6 9.1).

    Conjunto cerrado: true / false / not_evaluable.
    """

    TRUE = "true"
    FALSE = "false"
    NOT_EVALUABLE = "not_evaluable"


class NodeResult(BaseModel):
    """Resultado granular de un nodo, por su node_id estable.

    observed = el valor usado, renderizado (None si NOT_EVALUABLE). Minimo viable:
    string; la captura estructurada es mejora progresiva.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: UUID
    outcome: NodeOutcome
    observed: str | None = None


class EvaluationResult(BaseModel):
    """Resultado granular de una evaluacion (INFORME 6 sec 8.4; CA-P08-01).

    matched = el arbol de condiciones (SIN veto) dio verdadero. veto_active = el veto
    bloqueo la transicion a FIRING. La regla dispara si matched y NO veto_active.
    diagnostics = codigos de diagnostico (ADR-016), minimo viable.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    matched: bool
    veto_active: bool
    node_results: tuple[NodeResult, ...]
    diagnostics: tuple[str, ...] = ()


class RuleEvaluationCompletedPayload(EventPayload):
    """rule.evaluation_completed: resultado granular asociado a una transicion."""

    model_config = ConfigDict(extra="forbid")

    rule_id: UUID
    tenant_id: UUID
    canonical_rule_hash: str
    previous_state: EvaluationLifecycleState
    new_state: EvaluationLifecycleState
    result: EvaluationResult
    reason_code: str

    @model_validator(mode="after")
    def _solo_en_transicion(self) -> "RuleEvaluationCompletedPayload":
        allowed = {EvaluationLifecycleState.FIRING, EvaluationLifecycleState.RESOLVED}
        if self.new_state not in allowed:
            msg = (
                "rule.evaluation_completed solo se emite en transicion a FIRING o "
                f"RESOLVED; new_state={self.new_state.value}."
            )
            raise ValueError(msg)
        if self.new_state is self.previous_state:
            msg = "rule.evaluation_completed exige transicion (previous != new)."
            raise ValueError(msg)
        return self


class RuleFiringPayload(EventPayload):
    """rule.firing: la regla entro en estado activo/proyectable (flanco de subida).

    Ancla causal: signal.*/alert.*.causation_id = event_id(rule.firing) (CA-P08-01 p.5),
    en el envelope.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: UUID
    tenant_id: UUID
    canonical_rule_hash: str
    previous_state: EvaluationLifecycleState

    @model_validator(mode="after")
    def _flanco_de_subida(self) -> "RuleFiringPayload":
        if self.previous_state is EvaluationLifecycleState.FIRING:
            msg = "rule.firing es flanco de subida: previous_state no puede ser firing."
            raise ValueError(msg)
        return self


class RuleResolvedPayload(EventPayload):
    """rule.resolved: la regla salio del estado activo (flanco de bajada).

    NO proyecta cierre especulativo (CA-P08-01 p.8): es el evento de desactivacion que
    v4 no tenia.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: UUID
    tenant_id: UUID
    canonical_rule_hash: str
    previous_state: EvaluationLifecycleState

    @model_validator(mode="after")
    def _flanco_de_bajada(self) -> "RuleResolvedPayload":
        if self.previous_state is not EvaluationLifecycleState.FIRING:
            msg = (
                "rule.resolved es flanco de bajada: sale de FIRING, asi que "
                f"previous_state debe ser firing; recibido {self.previous_state.value}."
            )
            raise ValueError(msg)
        return self
