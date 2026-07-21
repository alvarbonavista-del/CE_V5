"""rule.quarantined: contrato, enum unico y decision de emision (CA-P08-06).

Cubre las pruebas de la DoD CA-P08-06 que son PURAS (sin DB): 1, 3, 4, 5, 6, 7, 8-13,
19-24. Las de comportamiento con DB (14-18, 25-29) viven en tools/validate_rules_hot.py;
las de generacion/registro (2, 30) las cierran gen_schemas + check_generated + el check
del registro.
"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from ce_v5.infra.db.rules import build_quarantined_event
from ce_v5.platform.rules.runtime import (
    EvalOutcome,
    RuntimeState,
    next_transition,
    quarantine_event_needed,
)
from source.envelope.enums import Scope
from source.families.component import ComponentEventType, ComponentLifecyclePayload
from source.families.registry import (
    expected_event_schema_version,
    payload_class_for,
)
from source.families.rule import (
    EvaluationLifecycleState,
    EvaluationResult,
    NodeOutcome,
    QuarantineReason,
    RuleEventType,
    RuleFiringPayload,
    RuleQuarantinedPayload,
    VetoOutcome,
)

TENANT = uuid4()
RULE = uuid4()


def _payload(**over: object) -> RuleQuarantinedPayload:
    base: dict[str, object] = {
        "rule_id": RULE,
        "tenant_id": TENANT,
        "quarantine_reason": QuarantineReason.PLAN_NOT_RECOMPUTABLE,
    }
    base.update(over)
    return RuleQuarantinedPayload(**base)


def _result(r: NodeOutcome, v: VetoOutcome) -> EvaluationResult:
    return EvaluationResult(
        matched=(r is NodeOutcome.TRUE),
        rule_outcome=r,
        veto_outcome=v,
        veto_active=v in {VetoOutcome.TRUE, VetoOutcome.NOT_EVALUABLE},
        node_results=(),
        diagnostics=(),
    )


# --- 1: payload versionado en contracts -------------------------------------
def test_1_payload_existe_y_versionado() -> None:
    payload = _payload()
    assert payload.rule_id == RULE
    assert expected_event_schema_version(RuleEventType.QUARANTINED.value) == 1


# --- 3: el registry contiene rule.quarantined -------------------------------
def test_3_registry_contiene_rule_quarantined() -> None:
    assert payload_class_for("rule.quarantined") is RuleQuarantinedPayload


# --- 4: payload erroneo falla fail-loud -------------------------------------
def test_4_payload_erroneo_falla() -> None:
    with pytest.raises(ValidationError):
        RuleQuarantinedPayload(rule_id=RULE, tenant_id=TENANT)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        _payload(quarantine_reason="motivo_libre")  # no es del enum


# --- 5: MISMO enum en evento y en rule_lifecycle_state ----------------------
def test_5_enum_unico() -> None:
    # El payload del evento y el runtime usan EXACTAMENTE el enum del contrato (el
    # runtime lo importa, no lo redefine): misma fuente de verdad que la columna
    # rule_lifecycle_state.quarantine_reason.
    assert (
        RuleQuarantinedPayload.model_fields["quarantine_reason"].annotation
        is QuarantineReason
    )
    prev = RuntimeState(EvaluationLifecycleState.INACTIVE)
    reason = quarantine_event_needed(
        prev, next_transition(prev, EvalOutcome.compilation_error())
    )
    assert isinstance(reason, QuarantineReason)


# --- 6: technical_detail rechaza longitud excesiva --------------------------
def test_6_technical_detail_longitud() -> None:
    # Relleno benigno (palabras cortas separadas por espacios): no dispara la guarda de
    # secretos, asi la prueba aisla el limite de LONGITUD (280).
    benigno = "abcdefgh " * 40  # tokens de 8, sin racha de 40+
    _payload(technical_detail=benigno[:280])  # el maximo permitido pasa
    with pytest.raises(ValidationError):
        _payload(technical_detail=benigno[:281])


# --- 7: technical_detail rechaza patrones sensibles -------------------------
@pytest.mark.parametrize(
    "detalle",
    [
        "api_key=abc123",
        "AKIAABCDEFGHIJKLMNOP",
        "token: " + "a" * 45,
        "-----BEGIN PRIVATE KEY-----",
    ],
)
def test_7_technical_detail_sin_secretos(detalle: str) -> None:
    with pytest.raises(ValidationError):
        _payload(technical_detail=detalle)


def test_7b_detalle_benigno_pasa() -> None:
    payload = _payload(technical_detail="code=E_PLAN_NORECOMP tf=1h src=market.close")
    assert payload.technical_detail is not None


# --- 8/9/10/11/12/13: decision de emision (flanco false->true) --------------
def test_8_emite_al_entrar_en_cuarentena() -> None:
    prev = RuntimeState(EvaluationLifecycleState.FIRING)
    result = next_transition(prev, EvalOutcome.compilation_error())
    assert (
        quarantine_event_needed(prev, result) is QuarantineReason.PLAN_NOT_RECOMPUTABLE
    )


def test_9_no_reemite_si_ya_quarantined() -> None:
    prev = RuntimeState(EvaluationLifecycleState.FIRING, is_quarantined=True)
    result = next_transition(prev, EvalOutcome.exception())
    assert quarantine_event_needed(prev, result) is None


def test_10_compilation_error_produce_plan_not_recomputable() -> None:
    prev = RuntimeState(EvaluationLifecycleState.INACTIVE)
    result = next_transition(prev, EvalOutcome.compilation_error())
    assert (
        quarantine_event_needed(prev, result) is QuarantineReason.PLAN_NOT_RECOMPUTABLE
    )


def test_11_excepciones_repetidas_produce_repeated_exceptions() -> None:
    state = RuntimeState(EvaluationLifecycleState.FIRING)
    emitted: list[QuarantineReason] = []
    for _ in range(3):
        result = next_transition(state, EvalOutcome.exception())
        reason = quarantine_event_needed(state, result)
        if reason is not None:
            emitted.append(reason)
        state = result.next
    assert emitted == [QuarantineReason.REPEATED_EXCEPTIONS]  # SOLO una vez (flanco)


def test_12_not_evaluable_no_produce_evento() -> None:
    prev = RuntimeState(EvaluationLifecycleState.FIRING)
    result = next_transition(
        prev, EvalOutcome.ok(_result(NodeOutcome.NOT_EVALUABLE, VetoOutcome.NO_VETO))
    )
    assert quarantine_event_needed(prev, result) is None


def test_13_stale_no_produce_evento() -> None:
    state = RuntimeState(EvaluationLifecycleState.FIRING)
    for _ in range(4):  # llega a stale sin tocar is_quarantined
        result = next_transition(
            state,
            EvalOutcome.ok(_result(NodeOutcome.NOT_EVALUABLE, VetoOutcome.NO_VETO)),
        )
        assert quarantine_event_needed(state, result) is None
        state = result.next
    assert state.is_stale is True and state.is_quarantined is False


# --- 19: no proyecta signal/alert -------------------------------------------
def test_19_no_proyecta() -> None:
    prev = RuntimeState(EvaluationLifecycleState.INACTIVE)
    result = next_transition(prev, EvalOutcome.compilation_error())
    # una transicion a cuarentena NO proyecta (project_raised solo al entrar en FIRING)
    assert result.project_raised is False
    assert result.emitted == ()


# --- 20: no pasa por dedup/flanco de evaluacion -----------------------------
def test_20_sin_flanco() -> None:
    campos = set(RuleQuarantinedPayload.model_fields)
    assert "previous_state" not in campos  # no lleva flanco (contraste con firing)
    assert "previous_state" in RuleFiringPayload.model_fields


# --- 21/22: familia rule.*, NUNCA component.* -------------------------------
def test_21_no_existe_component_quarantined_para_regla() -> None:
    assert payload_class_for("rule.quarantined") is RuleQuarantinedPayload
    # component.quarantined es de OTRA familia (Componente), no la regla.
    assert payload_class_for(ComponentEventType.QUARANTINED.value) is (
        ComponentLifecyclePayload
    )


def test_22_cuarentena_de_regla_no_mapea_a_component() -> None:
    assert RuleEventType.QUARANTINED.value.startswith("rule.")
    # component.quarantined NO resuelve al payload de regla: familias separadas.
    assert payload_class_for("component.quarantined") is not RuleQuarantinedPayload


# --- 23/24: envelope tenant-scoped y coherencia payload==envelope -----------
def test_23_envelope_lleva_tenant_id() -> None:
    event = build_quarantined_event(
        _payload(),
        source="ce_v5_rules_engine",
        correlation_id="corr-1",
        authoritative_tenant_id=TENANT,
    )
    assert event.envelope["scope"] == Scope.TENANT.value
    assert event.envelope["tenant_id"] == str(TENANT)
    assert event.event_type == "rule.quarantined"


def test_24_payload_tenant_debe_coincidir_con_envelope() -> None:
    otro_tenant = uuid4()
    with pytest.raises(ValueError, match="tenant"):
        build_quarantined_event(
            _payload(),  # tenant_id = TENANT
            source="ce_v5_rules_engine",
            correlation_id="corr-1",
            authoritative_tenant_id=otro_tenant,  # divergente
        )
