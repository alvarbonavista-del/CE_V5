"""Familia rule.* : el ciclo de evaluacion neutral (ADR-004, ADR-015, CA-P08-01).

rule.* es la FUENTE DE VERDAD NEUTRAL del evaluation lifecycle. Se emite SOLO por
TRANSICION de estado con deduplicacion (CA-P08-01, firmada): una evaluacion que deja la
regla en el mismo estado no emite nada. En una transicion a FIRING se emiten DOS
eventos: rule.evaluation_completed (EvaluationResult granular) y rule.firing (flanco
semantico, ancla causal de signal.*/alert.*). Idem RESOLVED con rule.resolved.

La distincion False vs NO-EVALUABLE por dato ausente (INFORME 6 sec 9.1) se conserva en
NodeOutcome. El causation_id y el tiempo viven en el ENVELOPE (ADR-003/007), no aqui.
"""

import re
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from source.envelope import EventPayload


class RuleEventType(StrEnum):
    """Tipos rule.* (CA-P08-01)."""

    EVALUATION_COMPLETED = "rule.evaluation_completed"
    FIRING = "rule.firing"
    RESOLVED = "rule.resolved"
    QUARANTINED = "rule.quarantined"


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


class VetoOutcome(StrEnum):
    """Resultado del bloque veto: CUATRO valores DISTINTOS (CA-P08-05).

    NO_VETO (la regla no tiene veto), FALSE, TRUE, NOT_EVALUABLE. PROHIBIDO colapsar a
    un veto_active:bool: colapsaria las filas 3 y 4 de la tabla de transiciones (V=TRUE
    bloquea Y RESUELVE; V=NOT_EVALUABLE bloquea pero deja STALE, no resuelve). Es el
    mismo tipo de hueco que costo P03: un bool que esconde dos casos que la FSM separa.
    """

    NO_VETO = "no_veto"
    FALSE = "false"
    TRUE = "true"
    NOT_EVALUABLE = "not_evaluable"


class ResolvedReason(StrEnum):
    """Por que una regla salio de FIRING a RESOLVED (CA-P08-05).

    condition_false = el arbol (sin veto) dejo de cumplirse. veto_true = un veto se
    activo y bloqueo. data_correction = una correccion de vela cambio el resultado (D5).
    data_correction se USA en el Bloque 7, pero el enum se cierra aqui: es valor firmado
    con uso conocido, no un "por si acaso" (regla 5.11 admite el valor con uso pactado).
    """

    CONDITION_FALSE = "condition_false"
    VETO_TRUE = "veto_true"
    DATA_CORRECTION = "data_correction"


class QuarantineReason(StrEnum):
    """Por que una regla quedo en CUARENTENA (operacional, robustez; CA-P08-04 D3).

    ENUM UNICO (CA-P08-06 p.3): el MISMO enum sirve a la columna
    rule_lifecycle_state.quarantine_reason y al payload del evento rule.quarantined; el
    runtime (platform.rules.runtime) lo importa de aqui, no lo redefine. Sin strings
    libres: una razon nueva se versiona por ADR-005 (anadir valor es compatible). Vive
    en contracts porque un evento del bus lo referencia; StaleReason, sin evento, se
    queda como enum operacional en el runtime.
    """

    PLAN_NOT_RECOMPUTABLE = "plan_not_recomputable"
    REPEATED_EXCEPTIONS = "repeated_exceptions"


class NodeResult(BaseModel):
    """Resultado granular de un nodo, por su node_id estable.

    observed = el VALOR CONCRETO usado, renderizado (None si NOT_EVALUABLE). Minimo
    viable: string; la captura estructurada es mejora progresiva.

    not_evaluable_reason = el MOTIVO cuando outcome es NOT_EVALUABLE (dato ausente,
    historia insuficiente, o hijos indecidibles); None en otro caso. La distincion
    FALSE vs NO-EVALUABLE (INFORME 6 sec 9.1) exige que el porque quede registrado, no
    solo el que: sin el, un NOT_EVALUABLE es opaco al historial y al operador (ADR-016).
    Campo OPCIONAL con default (evolucion aditiva ADR-005): rule.* es pre-consumidor.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: UUID
    outcome: NodeOutcome
    observed: str | None = None
    not_evaluable_reason: str | None = None


class EvaluationResult(BaseModel):
    """Resultado granular de una evaluacion (INFORME 6 sec 8.4; CA-P08-01, CA-P08-05).

    rule_outcome = el resultado K3 del arbol de condiciones SIN veto, a nivel de regla
    (TRUE/FALSE/NOT_EVALUABLE): es el eje R de la tabla de transiciones. matched es su
    proyeccion booleana de conveniencia (= rule_outcome es TRUE).

    veto_outcome = el resultado del veto (NO_VETO/FALSE/TRUE/NOT_EVALUABLE): es el eje V
    y el campo AUTORITATIVO para la FSM. veto_active es una conveniencia DERIVADA
    (= veto_outcome in {TRUE, NOT_EVALUABLE}); el runtime NO debe decidir sobre ella,
    porque colapsa V=TRUE (bloquea y resuelve) con V=NOT_EVALUABLE (bloquea; stale).

    matched_suppressed_by_veto (la regla habria disparado pero el veto lo impidio) NO es
    un campo: es OBSERVABLE de forma derivada (rule_outcome es TRUE y veto_outcome es
    TRUE). Un validador exige que matched y veto_active sean coherentes con sus ejes: no
    puede construirse un resultado inconsistente. diagnostics = codigos de diagnostico
    (ADR-016); el campo tipado SUPERA al diagnostico de texto para el runtime.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    matched: bool
    rule_outcome: NodeOutcome
    veto_outcome: VetoOutcome
    veto_active: bool
    node_results: tuple[NodeResult, ...]
    diagnostics: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _derivados_coherentes(self) -> "EvaluationResult":
        if self.matched != (self.rule_outcome is NodeOutcome.TRUE):
            msg = (
                "matched debe derivar de rule_outcome (= rule_outcome es TRUE): "
                f"matched={self.matched}, rule_outcome={self.rule_outcome.value}."
            )
            raise ValueError(msg)
        blocks = self.veto_outcome in {VetoOutcome.TRUE, VetoOutcome.NOT_EVALUABLE}
        if self.veto_active != blocks:
            msg = (
                "veto_active debe derivar de veto_outcome (in {TRUE, NOT_EVALUABLE}): "
                f"veto_active={self.veto_active}, veto={self.veto_outcome.value}."
            )
            raise ValueError(msg)
        return self


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
    v4 no tenia. resolved_reason (aditivo, CA-P08-05) dice POR QUE se resolvio
    (condition_false / veto_true / data_correction); el porque es observable, no se
    infiere del estado.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: UUID
    tenant_id: UUID
    canonical_rule_hash: str
    previous_state: EvaluationLifecycleState
    resolved_reason: ResolvedReason

    @model_validator(mode="after")
    def _flanco_de_bajada(self) -> "RuleResolvedPayload":
        if self.previous_state is not EvaluationLifecycleState.FIRING:
            msg = (
                "rule.resolved es flanco de bajada: sale de FIRING, asi que "
                f"previous_state debe ser firing; recibido {self.previous_state.value}."
            )
            raise ValueError(msg)
        return self


# technical_detail ACOTADO (CA-P08-06 p.4): texto tecnico CORTO (preferible code +
# params), nunca un payload completo ni un stacktrace gigante; los detalles largos van a
# logs internos saneados, no al evento tenant.
_TECHNICAL_DETAIL_MAX_LEN = 280

# Patrones de secreto que NUNCA deben viajar en un evento tenant (CA-P08-06 p.4). No es
# exhaustivo -- es una red fail-loud contra los descuidos frecuentes: claves privadas,
# keys de nube, JWT, pares clave=valor de credencial y tokens largos de alta entropia
# (incluido un hash de 40+).
_SENSITIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    re.compile(
        r"(?i)(password|passwd|secret|api[_-]?key|apikey|access[_-]?key|token"
        r"|credential|authorization)\s*[:=]"
    ),
    re.compile(r"[A-Za-z0-9_\-]{40,}"),
)


def reject_sensitive_detail(detail: str) -> None:
    """Falla si technical_detail contiene un patron de secreto (CA-P08-06 p.4)."""
    for pattern in _SENSITIVE_PATTERNS:
        if pattern.search(detail):
            msg = (
                "technical_detail parece contener un secreto o token (CA-P08-06 p.4): "
                "el evento tenant lleva code + params o texto tecnico corto, jamas "
                "credenciales, keys, tokens ni un stacktrace/payload completo."
            )
            raise ValueError(msg)


class RuleQuarantinedPayload(EventPayload):
    """rule.quarantined: la regla paso a CUARENTENA (OPERACIONAL; CA-P08-06, D3).

    NO es una transicion de evaluacion: no pasa por el validador de flanco de CA-P08-01
    (sin previous_state), NO proyecta signal.*/alert.* ni sustituye firing/resolved.
    Se emite SOLO en la transicion operacional is_quarantined false->true (el runtime lo
    decide; no en bucle si ya estaba quarantined) y en la MISMA transaccion que la
    escritura del estado (atomicidad, CA-P08-02).

    Familia rule.* (NUNCA component.*): una Regla es DATO evaluado tenant-scoped, no un
    Componente con manifest/discovery/lifecycle; component.quarantined (ADR-010) es para
    instancias de Componente (CA-P08-06 p.6).

    quarantine_reason usa el ENUM UNICO compartido con rule_lifecycle_state.quarantine_
    reason (p.3). technical_detail es OPCIONAL, acotado por schema y sin secretos (p.4).
    tenant_id viaja en el payload ademas del envelope; su coherencia con el tenant
    autoritativo del envelope la exige el productor (p.2), server-authoritative.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: UUID
    tenant_id: UUID
    quarantine_reason: QuarantineReason
    technical_detail: str | None = Field(
        default=None, max_length=_TECHNICAL_DETAIL_MAX_LEN
    )

    @field_validator("technical_detail")
    @classmethod
    def _detalle_sin_secretos(cls, value: str | None) -> str | None:
        if value is not None:
            reject_sensitive_detail(value)
        return value
