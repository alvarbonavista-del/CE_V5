"""Generador de JSON Schema desde la fuente Pydantic (ADR-006).

Cadena de contratos (DOC_ESTRUCTURA 2.5): contracts/source (Pydantic v2)
-> contracts/schemas (JSON Schema). Este script SOLO genera; el check de
regenerar-y-comparar (7.3/7.4) vive aparte. La salida es determinista
(claves ordenadas, sangria 2, salto final LF) para que la comparacion en
CI sea byte a byte.

Uso: python tools/gen_schemas.py
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = REPO_ROOT / "contracts" / "schemas"

sys.path.insert(0, str(REPO_ROOT / "contracts"))

from pydantic import TypeAdapter  # noqa: E402

from source.api import (  # noqa: E402
    ApiError,
    CapabilitiesResponse,
    LoginRequest,
    MeResponse,
    RealtimeAck,
    RealtimeAuth,
    RealtimeErrorMessage,
    RealtimeEvent,
    RealtimeSubscribe,
    RegisterRequest,
    SessionResponse,
)
from source.envelope import Envelope, EventPayload  # noqa: E402
from source.families import Family  # noqa: E402
from source.families.alert import AlertRaisedPayload  # noqa: E402
from source.families.component import ComponentLifecyclePayload  # noqa: E402
from source.families.footprint import (  # noqa: E402
    FootprintClosedPayload,
    FootprintCorrectedPayload,
)
from source.families.market import (  # noqa: E402
    CandleClosedPayload,
    CandleCorrectedPayload,
    CandleUpdatedPayload,
)
from source.families.policy import (  # noqa: E402
    KillSwitchPayload,
    PolicyVersionPublishedPayload,
    SubjectInvalidatedPayload,
)
from source.families.rule import (  # noqa: E402
    RuleEvaluationCompletedPayload,
    RuleFiringPayload,
    RuleQuarantinedPayload,
    RuleResolvedPayload,
)
from source.families.signal import SignalRaisedPayload  # noqa: E402
from source.families.user import UserRegisteredPayload  # noqa: E402


def serialize(schema: dict[str, object]) -> str:
    text = json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False)
    return text + "\n"


def _dump(path: Path, schema: dict[str, object]) -> None:
    path.write_text(serialize(schema), encoding="utf-8", newline="\n")


def build_schemas() -> dict[str, dict[str, object]]:
    envelope_schema = Envelope[EventPayload].model_json_schema()
    envelope_schema["title"] = "Envelope"
    family_schema = TypeAdapter(Family).json_schema()
    family_schema["title"] = "Family"
    component_schema = ComponentLifecyclePayload.model_json_schema()
    component_schema["title"] = "ComponentLifecyclePayload"
    kill_switch_schema = KillSwitchPayload.model_json_schema()
    kill_switch_schema["title"] = "KillSwitchPayload"
    version_published_schema = PolicyVersionPublishedPayload.model_json_schema()
    version_published_schema["title"] = "PolicyVersionPublishedPayload"
    subject_invalidated_schema = SubjectInvalidatedPayload.model_json_schema()
    subject_invalidated_schema["title"] = "SubjectInvalidatedPayload"
    candle_updated_schema = CandleUpdatedPayload.model_json_schema()
    candle_updated_schema["title"] = "CandleUpdatedPayload"
    candle_closed_schema = CandleClosedPayload.model_json_schema()
    candle_closed_schema["title"] = "CandleClosedPayload"
    candle_corrected_schema = CandleCorrectedPayload.model_json_schema()
    candle_corrected_schema["title"] = "CandleCorrectedPayload"
    footprint_closed_schema = FootprintClosedPayload.model_json_schema()
    footprint_closed_schema["title"] = "FootprintClosedPayload"
    footprint_corrected_schema = FootprintCorrectedPayload.model_json_schema()
    footprint_corrected_schema["title"] = "FootprintCorrectedPayload"
    register_request_schema = RegisterRequest.model_json_schema()
    register_request_schema["title"] = "RegisterRequest"
    login_request_schema = LoginRequest.model_json_schema()
    login_request_schema["title"] = "LoginRequest"
    session_schema = SessionResponse.model_json_schema()
    session_schema["title"] = "SessionResponse"
    me_schema = MeResponse.model_json_schema()
    me_schema["title"] = "MeResponse"
    api_error_schema = ApiError.model_json_schema()
    api_error_schema["title"] = "ApiError"
    capabilities_schema = CapabilitiesResponse.model_json_schema()
    capabilities_schema["title"] = "CapabilitiesResponse"
    realtime_auth_schema = RealtimeAuth.model_json_schema()
    realtime_auth_schema["title"] = "RealtimeAuth"
    realtime_subscribe_schema = RealtimeSubscribe.model_json_schema()
    realtime_subscribe_schema["title"] = "RealtimeSubscribe"
    realtime_ack_schema = RealtimeAck.model_json_schema()
    realtime_ack_schema["title"] = "RealtimeAck"
    realtime_error_schema = RealtimeErrorMessage.model_json_schema()
    realtime_error_schema["title"] = "RealtimeErrorMessage"
    realtime_event_schema = RealtimeEvent.model_json_schema()
    realtime_event_schema["title"] = "RealtimeEvent"
    user_registered_schema = UserRegisteredPayload.model_json_schema()
    user_registered_schema["title"] = "UserRegisteredPayload"
    rule_quarantined_schema = RuleQuarantinedPayload.model_json_schema()
    rule_quarantined_schema["title"] = "RuleQuarantinedPayload"
    rule_evaluation_completed_schema = (
        RuleEvaluationCompletedPayload.model_json_schema()
    )
    rule_evaluation_completed_schema["title"] = "RuleEvaluationCompletedPayload"
    rule_firing_schema = RuleFiringPayload.model_json_schema()
    rule_firing_schema["title"] = "RuleFiringPayload"
    rule_resolved_schema = RuleResolvedPayload.model_json_schema()
    rule_resolved_schema["title"] = "RuleResolvedPayload"
    signal_raised_schema = SignalRaisedPayload.model_json_schema()
    signal_raised_schema["title"] = "SignalRaisedPayload"
    alert_raised_schema = AlertRaisedPayload.model_json_schema()
    alert_raised_schema["title"] = "AlertRaisedPayload"
    return {
        "envelope.schema.json": envelope_schema,
        "family.schema.json": family_schema,
        "component_lifecycle.schema.json": component_schema,
        "policy_kill_switch.schema.json": kill_switch_schema,
        "policy_version_published.schema.json": version_published_schema,
        "policy_subject_invalidated.schema.json": subject_invalidated_schema,
        "market_candle_updated.schema.json": candle_updated_schema,
        "market_candle_closed.schema.json": candle_closed_schema,
        "market_candle_corrected.schema.json": candle_corrected_schema,
        "market_footprint_closed.schema.json": footprint_closed_schema,
        "market_footprint_corrected.schema.json": footprint_corrected_schema,
        "api_register_request.schema.json": register_request_schema,
        "api_login_request.schema.json": login_request_schema,
        "api_session.schema.json": session_schema,
        "api_me.schema.json": me_schema,
        "api_error.schema.json": api_error_schema,
        "api_capabilities.schema.json": capabilities_schema,
        "api_realtime_auth.schema.json": realtime_auth_schema,
        "api_realtime_subscribe.schema.json": realtime_subscribe_schema,
        "api_realtime_ack.schema.json": realtime_ack_schema,
        "api_realtime_error.schema.json": realtime_error_schema,
        "api_realtime_event.schema.json": realtime_event_schema,
        "user_registered.schema.json": user_registered_schema,
        "rule_quarantined.schema.json": rule_quarantined_schema,
        "rule_evaluation_completed.schema.json": rule_evaluation_completed_schema,
        "rule_firing.schema.json": rule_firing_schema,
        "rule_resolved.schema.json": rule_resolved_schema,
        "signal_raised.schema.json": signal_raised_schema,
        "alert_raised.schema.json": alert_raised_schema,
    }


def main() -> int:
    SCHEMAS.mkdir(parents=True, exist_ok=True)
    for name, schema in build_schemas().items():
        _dump(SCHEMAS / name, schema)
        print(f"generado contracts/schemas/{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
