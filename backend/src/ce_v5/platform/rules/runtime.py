"""Maquina de transiciones pura del ciclo de evaluacion (CA-P08-05, ADR-015).

Codigo PURO de plataforma (sin DB, sin outbox, sin reloj). Dado el estado que sobrevive
entre velas (RuntimeState) y el resultado de UNA evaluacion (EvalOutcome), decide la
transicion de la FSM: el estado siguiente, que eventos de ciclo emitir (evaluation_
completed / firing / resolved, con su motivo), si hay que PROYECTAR (solo al ENTRAR en
FIRING) y los OBSERVABLES operacionales (stale / quarantine). NO toca DB ni outbox: la
persistencia (migracion 0014) y el cableado del bus son la 6.3/Bloque 7.

FSM efectiva v5.0 (6.1): INACTIVE -> FIRING -> RESOLVED, disparo al cierre de vela; sin
anti-rebote "for" (PENDING queda reservado en el contrato, D4). RESOLVED puede volver a
FIRING (re-disparo): FIRING es flanco de subida desde no-FIRING.

DEDUP (CA-P08-01). Una evaluacion que deja la regla en el MISMO estado no emite nada:
seguir en FIRING vela tras vela no reemite firing. Solo las TRANSICIONES emiten.

STALE vs QUARANTINE (D3). STALE (dato ausente persistente) es TRANSITORIO y se AUTO-
LIMPIA cuando vuelve la evaluabilidad. QUARANTINE (plan no recomputable, o excepciones
repetidas) NO se auto-limpia: la reactivacion la hace el USUARIO (fuera de aqui). Un
NOT_EVALUABLE nunca deshabilita; solo mantiene estado y cuenta hacia STALE.

FRONTERAS DE CAPA. platform: importa solo de contracts (source.*) y su capa; NUNCA de
infra (check 7.1). StaleReason y CycleEvent viven AQUI (estado de runtime sin evento en
el bus). QuarantineReason, en cambio, se importa de contracts: es el ENUM UNICO que
comparten rule_lifecycle_state.quarantine_reason y el evento rule.quarantined (CA-P08-06
p.3), asi que su fuente de verdad esta en el contrato, no aqui.
"""

from dataclasses import dataclass, replace
from enum import StrEnum

from source.families.rule import (
    EvaluationLifecycleState,
    EvaluationResult,
    NodeOutcome,
    QuarantineReason,
    ResolvedReason,
    VetoOutcome,
)

# Umbrales parametrizables (nunca enterrados): se pasan como argumentos con estos
# defaults. STALE = velas NOT_EVALUABLE consecutivas antes de marcar stale; QUARANTINE =
# excepciones de evaluacion consecutivas antes de cuarentena (N=3, paridad v4, D3).
STALE_THRESHOLD_DEFAULT = 3
QUARANTINE_EXCEPTION_THRESHOLD_DEFAULT = 3


class StaleReason(StrEnum):
    """Por que una regla quedo STALE (dato ausente persistente, D3). Operacional."""

    RULE_NOT_EVALUABLE = "rule_not_evaluable"
    VETO_NOT_EVALUABLE = "veto_not_evaluable"


class EvalOutcomeKind(StrEnum):
    """Los tres casos de entrada a la FSM (CA-P08-05)."""

    OK = "ok"
    EXCEPTION = "exception"
    COMPILATION_ERROR = "compilation_error"


class CycleEventType(StrEnum):
    """Tipo de evento de ciclo a emitir. Neutral respecto al payload (ese es la 6.3)."""

    EVALUATION_COMPLETED = "evaluation_completed"
    FIRING = "firing"
    RESOLVED = "resolved"


@dataclass(frozen=True, slots=True)
class RuntimeState:
    """Estado que persiste entre velas: espejo 1:1 de rule_lifecycle_state (0014).

    eval_state = estado de la FSM. not_evaluable_count = velas consecutivas contando
    hacia STALE. consecutive_exceptions = excepciones consecutivas contando hacia
    CUARENTENA. is_stale (transitorio, auto-limpia) / is_quarantined (pegajoso, rearme
    del usuario), cada uno con su MOTIVO (stale_reason / quarantine_reason) para que el
    porque quede persistido, no solo el que. last_technical_error = diagnostico corto
    del ultimo fallo tecnico (acotado en la base; nunca un secreto). La 6.3 persiste el
    estado completo; la primitiva de infra recibe escalares (infra no importa platform).
    """

    eval_state: EvaluationLifecycleState
    not_evaluable_count: int = 0
    consecutive_exceptions: int = 0
    is_stale: bool = False
    stale_reason: StaleReason | None = None
    is_quarantined: bool = False
    quarantine_reason: QuarantineReason | None = None
    last_technical_error: str | None = None


@dataclass(frozen=True, slots=True)
class EvalOutcome:
    """Union discriminada de entrada a la FSM: OK(result) / EXCEPTION / COMP_ERROR."""

    kind: EvalOutcomeKind
    result: EvaluationResult | None = None
    message: str | None = None  # diagnostico tecnico (solo EXCEPTION/COMPILATION_ERROR)

    def __post_init__(self) -> None:
        has_result = self.result is not None
        if (self.kind is EvalOutcomeKind.OK) != has_result:
            msg = (
                "EvalOutcome: OK exige result; el resto no lo lleva. "
                f"kind={self.kind.value}, has_result={has_result}."
            )
            raise ValueError(msg)

    @staticmethod
    def ok(result: EvaluationResult) -> "EvalOutcome":
        return EvalOutcome(EvalOutcomeKind.OK, result)

    @staticmethod
    def exception(message: str | None = None) -> "EvalOutcome":
        return EvalOutcome(EvalOutcomeKind.EXCEPTION, message=message)

    @staticmethod
    def compilation_error(message: str | None = None) -> "EvalOutcome":
        return EvalOutcome(EvalOutcomeKind.COMPILATION_ERROR, message=message)


@dataclass(frozen=True, slots=True)
class CycleEvent:
    """Un evento de ciclo a emitir. resolved_reason solo en RESOLVED."""

    event_type: CycleEventType
    resolved_reason: ResolvedReason | None = None

    def __post_init__(self) -> None:
        is_resolved = self.event_type is CycleEventType.RESOLVED
        if (self.resolved_reason is not None) != is_resolved:
            msg = (
                "CycleEvent: resolved_reason es obligatorio y exclusivo de RESOLVED. "
                f"event_type={self.event_type.value}, reason={self.resolved_reason}."
            )
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class TransitionResult:
    """Salida de la FSM para UNA vela. No toca DB ni outbox.

    emitted = eventos de ciclo (vacio si no hubo transicion: dedup). project_raised =
    True SOLO al entrar en FIRING (proyectar signal.*/alert.*). matched_suppressed_by_
    veto = la regla habria disparado pero el veto lo impidio. stale_reason /
    quarantine_reason = observables cuando la regla queda stale / en cuarentena.
    """

    next: RuntimeState
    emitted: tuple[CycleEvent, ...] = ()
    project_raised: bool = False
    matched_suppressed_by_veto: bool = False
    stale_reason: StaleReason | None = None
    quarantine_reason: QuarantineReason | None = None


def next_transition(
    prev: RuntimeState,
    outcome: EvalOutcome,
    stale_threshold: int = STALE_THRESHOLD_DEFAULT,
    quarantine_threshold: int = QUARANTINE_EXCEPTION_THRESHOLD_DEFAULT,
) -> TransitionResult:
    """Codifica la tabla de transiciones CA-P08-05. Pura y determinista."""
    if outcome.kind is EvalOutcomeKind.COMPILATION_ERROR:
        return _on_compilation_error(prev, outcome.message)
    if outcome.kind is EvalOutcomeKind.EXCEPTION:
        return _on_exception(prev, quarantine_threshold, outcome.message)
    assert outcome.result is not None  # garantizado por EvalOutcome.__post_init__
    return _on_ok(prev, outcome.result, stale_threshold)


def quarantine_event_needed(
    prev: RuntimeState, result: TransitionResult
) -> QuarantineReason | None:
    """rule.quarantined se emite SOLO en el flanco is_quarantined false->true (p.8/p.9).

    Devuelve el motivo (para construir el evento) SOLO cuando la regla ENTRA en
    cuarentena; None si ya estaba quarantined (no se reemite en bucle) o si no lo esta.
    STALE y NOT_EVALUABLE nunca entran aqui: no ponen is_quarantined. El runtime (6.4)
    usa esto para decidir si encolar rule.quarantined junto al estado.
    """
    if result.next.is_quarantined and not prev.is_quarantined:
        return result.next.quarantine_reason
    return None


def _on_compilation_error(prev: RuntimeState, message: str | None) -> TransitionResult:
    """Plan no recomputable (ADR-017): cuarentena inmediata, sin transicion de eval."""
    return TransitionResult(
        next=replace(
            prev,
            is_quarantined=True,
            quarantine_reason=QuarantineReason.PLAN_NOT_RECOMPUTABLE,
            last_technical_error=message
            if message is not None
            else prev.last_technical_error,
        ),
        quarantine_reason=QuarantineReason.PLAN_NOT_RECOMPUTABLE,
    )


def _on_exception(
    prev: RuntimeState, quarantine_threshold: int, message: str | None
) -> TransitionResult:
    """Excepcion de evaluacion: cuenta hacia cuarentena; mantiene el estado de eval."""
    exceptions = prev.consecutive_exceptions + 1
    reached = exceptions >= quarantine_threshold
    next_quarantine_reason: QuarantineReason | None
    if reached:
        next_quarantine_reason = QuarantineReason.REPEATED_EXCEPTIONS
    else:
        # No alcanza el umbral: conserva el motivo previo (pegajoso) o None.
        next_quarantine_reason = prev.quarantine_reason
    return TransitionResult(
        next=replace(
            prev,
            consecutive_exceptions=exceptions,
            is_quarantined=prev.is_quarantined or reached,
            quarantine_reason=next_quarantine_reason,
            last_technical_error=message
            if message is not None
            else prev.last_technical_error,
        ),
        quarantine_reason=QuarantineReason.REPEATED_EXCEPTIONS if reached else None,
    )


def _on_ok(
    prev: RuntimeState, result: EvaluationResult, stale_threshold: int
) -> TransitionResult:
    """Evaluacion OK: aplica R=rule_outcome y V=veto_outcome contra el estado (K3)."""
    firing = prev.eval_state is EvaluationLifecycleState.FIRING
    r = result.rule_outcome
    v = result.veto_outcome

    next_state, emitted, project, suppressed = _decide(firing, prev.eval_state, r, v)

    # STALE: cuenta velas de indecidibilidad (regla NOT_EVALUABLE, o veto NOT_EVALUABLE
    # mientras estaba FIRING); cualquier evaluacion decidible la resetea.
    veto_stale = r is NodeOutcome.TRUE and v is VetoOutcome.NOT_EVALUABLE and firing
    if r is NodeOutcome.NOT_EVALUABLE or veto_stale:
        stale_count = prev.not_evaluable_count + 1
    else:
        stale_count = 0
    is_stale = stale_count >= stale_threshold
    stale_reason: StaleReason | None = None
    if is_stale:
        stale_reason = (
            StaleReason.RULE_NOT_EVALUABLE
            if r is NodeOutcome.NOT_EVALUABLE
            else StaleReason.VETO_NOT_EVALUABLE
        )

    next_state_final = RuntimeState(
        eval_state=next_state,
        not_evaluable_count=stale_count,
        consecutive_exceptions=0,  # una evaluacion OK rompe la racha de excepciones
        is_stale=is_stale,
        stale_reason=stale_reason,
        # OK no saca de cuarentena (rearme del usuario): conserva bandera y motivo.
        is_quarantined=prev.is_quarantined,
        quarantine_reason=prev.quarantine_reason,
        last_technical_error=prev.last_technical_error,
    )
    return TransitionResult(
        next=next_state_final,
        emitted=emitted,
        project_raised=project,
        matched_suppressed_by_veto=suppressed,
        stale_reason=stale_reason,
    )


def _decide(
    firing: bool,
    current: EvaluationLifecycleState,
    r: NodeOutcome,
    v: VetoOutcome,
) -> tuple[EvaluationLifecycleState, tuple[CycleEvent, ...], bool, bool]:
    """Las filas de CA-P08-05: (estado, emitted, project_raised, suppressed)."""
    firing_state = EvaluationLifecycleState.FIRING
    resolved_state = EvaluationLifecycleState.RESOLVED

    # Fila 1: R=NOT_EVALUABLE -> mantiene, sin emitir, sin proyectar.
    if r is NodeOutcome.NOT_EVALUABLE:
        return current, (), False, False

    # Fila 5: R=FALSE -> si FIRING, RESOLVED(condition_false); si no, sin cambio.
    if r is NodeOutcome.FALSE:
        if firing:
            return (
                resolved_state,
                _resolved(ResolvedReason.CONDITION_FALSE),
                False,
                False,
            )
        return current, (), False, False

    # R=TRUE: el veto decide.
    # Fila 2: V in {NO_VETO, FALSE} -> entra en FIRING (o dedup si ya FIRING).
    if v in {VetoOutcome.NO_VETO, VetoOutcome.FALSE}:
        if firing:
            return firing_state, (), False, False  # dedup: sigue FIRING, no reemite
        return firing_state, _firing(), True, False

    # Fila 3: V=TRUE -> si FIRING, RESOLVED(veto_true); si no, suprimido (no dispara).
    if v is VetoOutcome.TRUE:
        if firing:
            return resolved_state, _resolved(ResolvedReason.VETO_TRUE), False, False
        return current, (), False, True

    # Fila 4: V=NOT_EVALUABLE -> mantiene, proyeccion suprimida (fail-safe, no dispara).
    return current, (), False, False


def _firing() -> tuple[CycleEvent, ...]:
    """Emision al entrar en FIRING: evaluation_completed + firing (CA-P08-01)."""
    return (
        CycleEvent(CycleEventType.EVALUATION_COMPLETED),
        CycleEvent(CycleEventType.FIRING),
    )


def _resolved(reason: ResolvedReason) -> tuple[CycleEvent, ...]:
    """Emision al salir a RESOLVED: evaluation_completed + resolved(reason)."""
    return (
        CycleEvent(CycleEventType.EVALUATION_COMPLETED),
        CycleEvent(CycleEventType.RESOLVED, resolved_reason=reason),
    )
