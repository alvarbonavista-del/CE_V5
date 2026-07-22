"""Round-trip de payload por cada event_type del registro (guardarrail 5.21).

Mitad DINAMICA del mecanismo del guardarrail 5.21 (la estatica es
tools/check_envelope_base_usage.py). Garantiza que CADA event_type registrado, al
construirse con su envelope CONCRETO (Envelope[type(payload)]), serializa un payload NO
VACIO que revalida contra su clase y contra el JSON Schema registrado (el que
model_json_schema produce, del que salen los artefactos de contracts/schemas).

COMPLETITUD SIN HUECOS. SAMPLE_PAYLOADS mapea UN payload valido por event_type, y un
test afirma que sus claves == las de EVENT_PAYLOAD_REGISTRY. Una familia nueva que se
registre sin muestra hace fallar el test: no se puede anadir un event_type y olvidar
probar que serializa no-vacio.

CONTROL NEGATIVO. test_control_negativo prueba que el sobre BASE Envelope[EventPayload]
serializa payload={} -- el modo de fallo que el mecanismo previene (defecto B6.5) --, de
modo que queda demostrado que este test cazaria una regresion a la base.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

import pytest

from source.envelope import Envelope, EventPayload
from source.envelope.enums import Scope
from source.families.alert import AlertEventType, AlertRaisedPayload
from source.families.component import (
    ComponentEventType,
    ComponentLifecyclePayload,
    HealthStatus,
    LifecycleScope,
    LifecycleState,
    ReadinessStatus,
)
from source.families.footprint import (
    FootprintCell,
    FootprintClosedPayload,
    FootprintCorrectedPayload,
    MarketFootprintEventType,
)
from source.families.market import (
    CandleClosedPayload,
    CandleCorrectedPayload,
    CandleUpdatedPayload,
    MarketCandleEventType,
    MarketType,
    Timeframe,
)
from source.families.policy import (
    InvalidationReason,
    KillSwitchPayload,
    KillSwitchScope,
    PolicyEventType,
    PolicyVersionPublishedPayload,
    SubjectInvalidatedPayload,
)
from source.families.registry import (
    EVENT_PAYLOAD_REGISTRY,
    expected_event_schema_version,
    payload_class_for,
)
from source.families.rule import (
    EvaluationLifecycleState,
    EvaluationResult,
    NodeOutcome,
    QuarantineReason,
    ResolvedReason,
    RuleEvaluationCompletedPayload,
    RuleEventType,
    RuleFiringPayload,
    RuleQuarantinedPayload,
    RuleResolvedPayload,
    VetoOutcome,
)
from source.families.signal import SignalEventType, SignalRaisedPayload
from source.families.user import UserEventType, UserRegisteredPayload
from source.time import MaturityState

# Ventana 1h alineada a frontera exacta (el contrato market exige alineacion).
_OPEN = 1_784_073_600_000
_CLOSE = _OPEN + 3_600_000
_HASH = "a" * 40


def _component() -> ComponentLifecyclePayload:
    """UN payload de lifecycle sirve para los 11 event_type de component.*.

    El payload no cruza new_state con el event_type (el event_type vive en el envelope,
    ADR-003), asi que una sola instancia con scope=global cubre todos.
    """
    return ComponentLifecyclePayload(
        component_id="sample",
        component_version="1.0.0",
        component_instance_id="inst-1",
        lifecycle_scope=LifecycleScope.GLOBAL,
        new_state=LifecycleState.RUNNING,
        health_status=HealthStatus.HEALTHY,
        readiness_status=ReadinessStatus.READY,
    )


def _candle(maturity: MaturityState, **extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "maturity_state": maturity,
        "exchange": "binance",
        "market_type": MarketType.SPOT,
        "symbol": "BTC-USDT",
        "timeframe": Timeframe.H1,
        "open_time": _OPEN,
        "close_time": _CLOSE,
        "open": Decimal("100"),
        "high": Decimal("110"),
        "low": Decimal("95"),
        "close": Decimal("105"),
        "volume": Decimal("12.5"),
    }
    base.update(extra)
    return base


def _footprint(maturity: MaturityState, **extra: object) -> dict[str, object]:
    cells = (
        FootprintCell(
            price=Decimal("100"),
            buy_volume=Decimal("3"),
            sell_volume=Decimal("1"),
            delta=Decimal("2"),
        ),
        FootprintCell(
            price=Decimal("101"),
            buy_volume=Decimal("2"),
            sell_volume=Decimal("2"),
            delta=Decimal("0"),
        ),
    )
    base: dict[str, object] = {
        "maturity_state": maturity,
        "exchange": "binance",
        "market_type": MarketType.SPOT,
        "symbol": "BTC-USDT",
        "timeframe": Timeframe.H1,
        "open_time": _OPEN,
        "close_time": _CLOSE,
        "cells": cells,
        "bar_buy_volume": Decimal("5"),
        "bar_sell_volume": Decimal("3"),
        "is_complete": True,
        "bar_delta": Decimal("2"),
        "trade_count": 8,
    }
    base.update(extra)
    return base


def _eval_result() -> EvaluationResult:
    return EvaluationResult(
        matched=True,
        rule_outcome=NodeOutcome.TRUE,
        veto_outcome=VetoOutcome.NO_VETO,
        veto_active=False,
        node_results=(),
    )


# UN payload VALIDO por cada event_type del registro. La completitud (claves ==
# EVENT_PAYLOAD_REGISTRY) la exige test_completitud: sin muestra, el test falla.
SAMPLE_PAYLOADS: dict[str, EventPayload] = {
    # component.* (11): misma clase, distinta transicion.
    **{et.value: _component() for et in ComponentEventType},
    # policy.*
    PolicyEventType.KILL_SWITCH_ACTIVATED.value: KillSwitchPayload(
        kill_switch_id="ks-1",
        scope=KillSwitchScope.GLOBAL,
        reason_code="incident",
        policy_version="v1",
        actor="operator",
    ),
    PolicyEventType.KILL_SWITCH_DEACTIVATED.value: KillSwitchPayload(
        kill_switch_id="ks-1",
        scope=KillSwitchScope.GLOBAL,
        reason_code="resolved",
        policy_version="v1",
        actor="operator",
    ),
    PolicyEventType.VERSION_PUBLISHED.value: PolicyVersionPublishedPayload(
        policy_version="v2",
        actor="operator",
    ),
    PolicyEventType.SUBJECT_INVALIDATED.value: SubjectInvalidatedPayload(
        tenant_id="t1",
        reason=InvalidationReason.PLAN_CHANGED,
        policy_version="v2",
    ),
    # user.*
    UserEventType.REGISTERED.value: UserRegisteredPayload(
        user_id=str(uuid4()), tenant_id=str(uuid4())
    ),
    # market.* (cada tipo FIJA su maturity_state por validador).
    MarketCandleEventType.CANDLE_UPDATED.value: CandleUpdatedPayload(
        **_candle(MaturityState.PROVISIONAL)
    ),
    MarketCandleEventType.CANDLE_CLOSED.value: CandleClosedPayload(
        **_candle(MaturityState.CLOSED)
    ),
    MarketCandleEventType.CANDLE_CORRECTED.value: CandleCorrectedPayload(
        **_candle(
            MaturityState.CORRECTION,
            correction_revision=1,
            corrects_idempotency_key="orig-1",
        )
    ),
    # market.footprint_* (P07b): footprint por barra (celdas + delta).
    MarketFootprintEventType.FOOTPRINT_CLOSED.value: FootprintClosedPayload(
        **_footprint(MaturityState.CLOSED)
    ),
    MarketFootprintEventType.FOOTPRINT_CORRECTED.value: FootprintCorrectedPayload(
        **_footprint(
            MaturityState.CORRECTION,
            correction_revision=1,
            corrects_idempotency_key="orig-fp-1",
        )
    ),
    # rule.* (transiciones + operacional).
    RuleEventType.EVALUATION_COMPLETED.value: RuleEvaluationCompletedPayload(
        rule_id=uuid4(),
        tenant_id=uuid4(),
        canonical_rule_hash=_HASH,
        previous_state=EvaluationLifecycleState.INACTIVE,
        new_state=EvaluationLifecycleState.FIRING,
        result=_eval_result(),
        reason_code="firing",
    ),
    RuleEventType.FIRING.value: RuleFiringPayload(
        rule_id=uuid4(),
        tenant_id=uuid4(),
        canonical_rule_hash=_HASH,
        previous_state=EvaluationLifecycleState.INACTIVE,
    ),
    RuleEventType.RESOLVED.value: RuleResolvedPayload(
        rule_id=uuid4(),
        tenant_id=uuid4(),
        canonical_rule_hash=_HASH,
        previous_state=EvaluationLifecycleState.FIRING,
        resolved_reason=ResolvedReason.CONDITION_FALSE,
    ),
    RuleEventType.QUARANTINED.value: RuleQuarantinedPayload(
        rule_id=uuid4(),
        tenant_id=uuid4(),
        quarantine_reason=QuarantineReason.REPEATED_EXCEPTIONS,
    ),
    # signal.*/alert.* (proyecciones).
    SignalEventType.RAISED.value: SignalRaisedPayload(
        signal_id=uuid4(),
        rule_id=uuid4(),
        tenant_id=uuid4(),
        canonical_rule_hash=_HASH,
        exchange="binance",
        symbol="BTC-USDT",
    ),
    AlertEventType.RAISED.value: AlertRaisedPayload(
        alert_id=uuid4(),
        rule_id=uuid4(),
        tenant_id=uuid4(),
        canonical_rule_hash=_HASH,
        exchange="binance",
        symbol="BTC-USDT",
    ),
}


def _build_envelope(event_type: str, payload: EventPayload) -> Envelope[EventPayload]:
    """Construye el envelope CONCRETO parametrizado por la clase RUNTIME del payload.

    Envelope[type(payload)] es la forma correcta: pydantic serializa por el tipo
    declarado, y aqui el tipo declarado es la clase concreta, no la base. scope=SYSTEM
    con tenant/user None es valido para cualquier payload (el scope del sobre es
    independiente del contenido del payload). El subscript dinamico se cablea con Any
    porque mypy no puede resolver Envelope[type(payload)] estaticamente; el round-trip
    del test es exactamente lo que verifica que la clase concreta es la correcta.
    """
    # __class_getitem__ es el equivalente runtime de Envelope[type(payload)]; se usa la
    # forma de llamada (no el subscript) porque mypy no admite un subscript de tipo con
    # un valor runtime, y aqui el tipo se decide en ejecucion a proposito.
    concrete_cls: Any = Envelope.__class_getitem__(type(payload))
    envelope = concrete_cls(
        event_type=event_type,
        event_schema_version=expected_event_schema_version(event_type),
        source="test",
        idempotency_key=f"{event_type}:sample",
        stream_key="stream-demo",
        scope=Scope.SYSTEM,
        correlation_id="corr-1",
        payload=payload,
    )
    return cast("Envelope[EventPayload]", envelope)


def test_completitud_sample_cubre_todo_el_registro() -> None:
    """SAMPLE_PAYLOADS == EVENT_PAYLOAD_REGISTRY: ninguna familia sin muestra."""
    assert set(SAMPLE_PAYLOADS) == set(EVENT_PAYLOAD_REGISTRY), (
        "toda entrada del registro necesita una muestra en SAMPLE_PAYLOADS; una "
        "familia nueva sin muestra debe hacer fallar este test (sin huecos)."
    )


@pytest.mark.parametrize("event_type", sorted(EVENT_PAYLOAD_REGISTRY))
def test_payload_serializa_no_vacio_y_revalida(event_type: str) -> None:
    """El payload concreto serializa NO VACIO y revalida contra su clase y su schema."""
    payload = SAMPLE_PAYLOADS[event_type]
    envelope = _build_envelope(event_type, payload)
    dumped = envelope.model_dump(mode="json")

    # 1) NO VACIO: la garantia central del guardarrail 5.21.
    dumped_payload = dumped["payload"]
    assert isinstance(dumped_payload, dict)
    assert dumped_payload != {}, (
        f"{event_type}: el payload serializado esta VACIO; la emision debe usar "
        "Envelope[PayloadConcreto], no la base (guardarrail 5.21)."
    )

    # 2) Revalida contra la CLASE concreta registrada (validacion profunda: tipos,
    #    nested, validadores del contrato).
    payload_cls = payload_class_for(event_type)
    revalidado = payload_cls.model_validate(dumped_payload)
    assert revalidado.model_dump(mode="json") == dumped_payload  # round-trip estable

    # 3) Conformidad con el JSON Schema REGISTRADO (el que model_json_schema produce,
    #    del que salen los artefactos de contracts/schemas). Los payload son planos con
    #    extra=forbid, asi que basta required subset + additionalProperties=false.
    schema = payload_cls.model_json_schema()
    required = set(schema.get("required", ()))
    properties = set(schema.get("properties", {}))
    assert required <= set(dumped_payload), (
        f"{event_type}: faltan campos requeridos por el schema: "
        f"{required - set(dumped_payload)}"
    )
    assert schema.get("additionalProperties") is False
    assert set(dumped_payload) <= properties, (
        f"{event_type}: el payload lleva campos fuera del schema: "
        f"{set(dumped_payload) - properties}"
    )


def test_control_negativo_base_serializa_payload_vacio() -> None:
    """El sobre BASE serializa payload={}: el modo de fallo que el mecanismo previene.

    Prueba explicita de que este test cazaria una regresion a Envelope[EventPayload]:
    la MISMA instancia de payload, envuelta con la base en vez de con su clase concreta,
    se vacia al serializar. Es el defecto B6.5 reproducido en miniatura.
    """
    payload = SAMPLE_PAYLOADS[RuleEventType.FIRING.value]
    base_envelope = Envelope[EventPayload](
        event_type=RuleEventType.FIRING.value,
        event_schema_version=1,
        source="test",
        idempotency_key="k",
        stream_key="s",
        scope=Scope.SYSTEM,
        correlation_id="c",
        payload=payload,  # payload concreto REAL, pero el tipo declarado es la base
    )
    assert base_envelope.model_dump(mode="json")["payload"] == {}
