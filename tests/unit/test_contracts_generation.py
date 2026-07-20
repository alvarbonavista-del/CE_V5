from check_generated import _problems
from gen_schemas import build_schemas, serialize


def test_build_schemas_incluye_todos_los_contratos() -> None:
    # Lista CERRADA a proposito: un contrato nuevo no se cuela sin que alguien lo vea
    # en el diff de este test.
    schemas = build_schemas()
    assert set(schemas) == {
        "envelope.schema.json",
        "family.schema.json",
        "component_lifecycle.schema.json",
        "policy_kill_switch.schema.json",
        "policy_version_published.schema.json",
        "policy_subject_invalidated.schema.json",
        "market_candle_updated.schema.json",
        "market_candle_closed.schema.json",
        "market_candle_corrected.schema.json",
        "api_register_request.schema.json",
        "api_login_request.schema.json",
        "api_session.schema.json",
        "api_me.schema.json",
        "api_error.schema.json",
        "api_capabilities.schema.json",
        "api_realtime_auth.schema.json",
        "api_realtime_subscribe.schema.json",
        "api_realtime_ack.schema.json",
        "api_realtime_error.schema.json",
        "api_realtime_event.schema.json",
        "user_registered.schema.json",
        "rule_quarantined.schema.json",
        "rule_evaluation_completed.schema.json",
        "rule_firing.schema.json",
        "rule_resolved.schema.json",
        "signal_raised.schema.json",
        "alert_raised.schema.json",
    }


def test_envelope_schema_propiedades_y_requeridos() -> None:
    envelope = build_schemas()["envelope.schema.json"]
    props = envelope["properties"]
    assert isinstance(props, dict)
    assert len(props) == 19
    required = envelope["required"]
    assert isinstance(required, list)
    assert set(required) == {
        "event_type",
        "event_schema_version",
        "source",
        "idempotency_key",
        "stream_key",
        "scope",
        "correlation_id",
        "payload",
    }


def test_serialize_determinista() -> None:
    primero = {n: serialize(s) for n, s in build_schemas().items()}
    segundo = {n: serialize(s) for n, s in build_schemas().items()}
    assert primero == segundo


def test_artefactos_en_sincronia_con_la_fuente() -> None:
    assert _problems() == []
