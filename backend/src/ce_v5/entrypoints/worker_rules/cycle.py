"""Orquestador del ciclo de UNA regla para UNA vela (CA-P08-01, CA-P08-04 D6).

Capa entrypoints: cose platform (evaluador + FSM pura) con infra (constructores de
evento + primitiva atomica) y contracts (7.1). Procesa UNA regla contra UNA vela:
evalua -> decide la transicion -> monta la secuencia de eventos de la transicion ->
persiste el RuntimeState y encola los eventos EN LA MISMA TRANSACCION (atomico). NO
suscribe al bus ni hace bucle: eso es el Bloque 7.

ORDEN CAUSAL (CA-P08-01). Al entrar en FIRING se emite rule.evaluation_completed +
rule.firing, y SOLO entonces -si no hay veto- la proyeccion por producto (alert.raised /
signal.raised) con causation_id = event_id(rule.firing): la proyeccion NUNCA se emite
saltandose el firing (ADR-015). Al salir a RESOLVED se emite evaluation_completed +
rule.resolved con su motivo, y NADA de proyeccion (p.8). Un hold/dedup no emite evento
pero SI persiste el estado (contadores/stale pueden haber cambiado).
"""

from __future__ import annotations

from uuid import UUID

from ce_v5.infra.db.outbox import OutboxEvent
from ce_v5.infra.db.rules import (
    CorrectionMark,
    LifecycleOperational,
    build_evaluation_completed_event,
    build_firing_event,
    build_projection_event,
    build_quarantined_event,
    build_resolved_event,
    record_transition,
)
from ce_v5.infra.db.tenancy import SystemScopedDatabase
from ce_v5.platform.rules.canonical import canonical_rule_hash
from ce_v5.platform.rules.compiler import ExecutionPlan
from ce_v5.platform.rules.evaluator import Series, evaluate
from ce_v5.platform.rules.runtime import (
    QUARANTINE_EXCEPTION_THRESHOLD_DEFAULT,
    STALE_THRESHOLD_DEFAULT,
    CycleEventType,
    EvalOutcome,
    RuntimeState,
    next_transition,
    quarantine_event_needed,
)
from source.families.rule import ResolvedReason, RuleQuarantinedPayload
from source.rules.market_rules import AnyRule


def _reason_code(ordinario: str, correction: CorrectionMark | None) -> str:
    """El motivo del ciclo: data_correction si la emision viene de una correccion."""
    if correction is None:
        return ordinario
    return ResolvedReason.DATA_CORRECTION.value


def _operational_of(state: RuntimeState) -> LifecycleOperational:
    """Mapea el RuntimeState al carrier escalar de infra (enums -> .value o None)."""
    return LifecycleOperational(
        not_evaluable_count=state.not_evaluable_count,
        consecutive_exceptions=state.consecutive_exceptions,
        is_stale=state.is_stale,
        stale_reason=None if state.stale_reason is None else state.stale_reason.value,
        is_quarantined=state.is_quarantined,
        quarantine_reason=(
            None if state.quarantine_reason is None else state.quarantine_reason.value
        ),
        last_technical_error=state.last_technical_error,
    )


def process_rule_cycle(
    scoped_db: SystemScopedDatabase,
    rule: AnyRule,
    plan: ExecutionPlan | None,
    data: Series,
    prev_state: RuntimeState,
    trigger_open_time: int,
    *,
    tenant_id: UUID,
    rule_id: UUID,
    outcome_override: EvalOutcome | None = None,
    correction: CorrectionMark | None = None,
    stale_threshold: int = STALE_THRESHOLD_DEFAULT,
    quarantine_threshold: int = QUARANTINE_EXCEPTION_THRESHOLD_DEFAULT,
) -> RuntimeState:
    """Procesa una regla admitida contra una vela y devuelve su nuevo RuntimeState.

    tenant_id y rule_id son AUTORITATIVOS y OBLIGATORIOS: los pasa el llamador desde la
    COLUMNA de servidor (la que devuelve la ventanilla rules_for_market), NUNCA desde el
    JSON de la definicion. NO se derivan del plan: compile() copia plan.tenant_id del
    JSON de la regla, y ese JSON puede llevar un tenant FALSO -- es exactamente el caso
    que cierra CA-P08-03 p.9 (la columna manda sobre el JSON). Derivarlo del plan
    escribiria el estado y proyectaria la senal bajo el tenant EQUIVOCADO.

    plan es el ExecutionPlan ya compilado, o None si la compilacion FALLO. En ese caso
    el llamador pasa outcome_override=EvalOutcome.compilation_error(...) y el ciclo lo
    lleva a CUARENTENA sin evaluar (ADR-017): una regla cuyo plan no es recomputable no
    se evalua, se cuarentena.

    outcome_override sustituye la evaluacion (compilation_error). Si es None se evalua
    normalmente y un fallo de EVAL se convierte en EvalOutcome.exception.

    correction marca la emision como derivada de una CORRECCION de vela (CA-P08-08):
    cualifica la idempotency_key para no colisionar con la emision del candle_closed
    original, ancla el causation al evento de correccion y hace que el motivo del ciclo
    sea data_correction en vez del motivo ordinario. La REGLA DE FLANCO no cambia: si la
    reevaluacion deja el mismo estado, no se emite nada (tambien bajo correccion).
    """
    if plan is not None and plan.rule_id != rule_id:
        msg = (
            "process_rule_cycle: el plan compilado no es de esta regla "
            f"({plan.rule_id} != {rule_id}); el ciclo no procesa un plan ajeno."
        )
        raise ValueError(msg)
    rule_hash = canonical_rule_hash(rule)

    # 1. Evaluar (K3). Un fallo de evaluacion NO rompe el ciclo: es una excepcion para
    #    la FSM (cuenta hacia cuarentena), no una traza que aborte el proceso.
    if outcome_override is not None:
        outcome = outcome_override
    else:
        try:
            outcome = EvalOutcome.ok(evaluate(rule, data))
        except Exception as exc:  # noqa: BLE001
            outcome = EvalOutcome.exception(str(exc))

    # 2. Decidir la transicion (pura).
    transition = next_transition(
        prev_state, outcome, stale_threshold, quarantine_threshold
    )
    previous_state = prev_state.eval_state
    new_state = transition.next.eval_state
    cycle_types = {event.event_type for event in transition.emitted}

    # 3. Montar la secuencia de eventos de la transicion.
    events: list[OutboxEvent] = []
    if CycleEventType.FIRING in cycle_types:
        assert outcome.result is not None  # firing solo llega de una evaluacion OK
        events.append(
            build_evaluation_completed_event(
                rule_id=rule_id,
                tenant_id=tenant_id,
                canonical_rule_hash=rule_hash,
                previous_state=previous_state,
                new_state=new_state,
                result=outcome.result,
                reason_code=_reason_code("firing", correction),
                open_time=trigger_open_time,
                correction=correction,
            )
        )
        firing_event = build_firing_event(
            rule_id=rule_id,
            tenant_id=tenant_id,
            canonical_rule_hash=rule_hash,
            previous_state=previous_state,
            open_time=trigger_open_time,
            correction=correction,
        )
        events.append(firing_event)
        if transition.project_raised:
            events.append(
                build_projection_event(
                    rule,
                    tenant_id=tenant_id,
                    canonical_rule_hash=rule_hash,
                    firing_event_id=firing_event.event_id,
                    open_time=trigger_open_time,
                    correction=correction,
                )
            )
    elif CycleEventType.RESOLVED in cycle_types:
        assert outcome.result is not None
        resolved_reason = next(
            event.resolved_reason
            for event in transition.emitted
            if event.event_type is CycleEventType.RESOLVED
        )
        assert resolved_reason is not None
        if correction is not None:
            # Bajo correccion el POR QUE de la salida de FIRING es el dato corregido, no
            # la condicion que la FSM vio al reevaluar: ese es exactamente el valor que
            # ResolvedReason.DATA_CORRECTION reservaba (CA-P08-05 D5).
            resolved_reason = ResolvedReason.DATA_CORRECTION
        events.append(
            build_evaluation_completed_event(
                rule_id=rule_id,
                tenant_id=tenant_id,
                canonical_rule_hash=rule_hash,
                previous_state=previous_state,
                new_state=new_state,
                result=outcome.result,
                reason_code=resolved_reason.value,
                open_time=trigger_open_time,
                correction=correction,
            )
        )
        events.append(
            build_resolved_event(
                rule_id=rule_id,
                tenant_id=tenant_id,
                canonical_rule_hash=rule_hash,
                previous_state=previous_state,
                resolved_reason=resolved_reason,
                open_time=trigger_open_time,
                correction=correction,
            )
        )

    # Flanco de cuarentena is_quarantined false->true: evento operacional, no proyecta.
    # El detalle tecnico va al estado persistido (last_technical_error), NO al evento
    # tenant (CA-P08-06 p.4): el payload solo lleva la razon tipada.
    quarantine_reason = quarantine_event_needed(prev_state, transition)
    if quarantine_reason is not None:
        events.append(
            build_quarantined_event(
                RuleQuarantinedPayload(
                    rule_id=rule_id,
                    tenant_id=tenant_id,
                    quarantine_reason=quarantine_reason,
                    technical_detail=None,
                ),
                source="ce_v5_rules_engine",
                correlation_id=f"{tenant_id}:{trigger_open_time}",
                authoritative_tenant_id=tenant_id,
            )
        )

    # 4. Persistir estado + encolar eventos EN LA MISMA TRANSACCION (atomico). Sin
    #    eventos (hold/dedup) se persiste igual: los contadores pueden haber cambiado.
    record_transition(
        scoped_db,
        tenant_id=tenant_id,
        rule_id=rule_id,
        new_state=new_state.value,
        last_evaluated_open_time=trigger_open_time,
        operational=_operational_of(transition.next),
        events=events,
    )
    return transition.next
