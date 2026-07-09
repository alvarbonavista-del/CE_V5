import pytest
from pydantic import ValidationError

from source.envelope import Envelope, Scope
from source.families import validate_event_type
from source.families.market import MarketCandleEventType
from source.families.maturity import MaturityAwarePayload
from source.time import MaturityState


def test_provisional_sin_referencia() -> None:
    p = MaturityAwarePayload(maturity_state=MaturityState.PROVISIONAL)
    assert p.corrects_idempotency_key is None


def test_provisional_prohibe_referencia() -> None:
    with pytest.raises(ValidationError):
        MaturityAwarePayload(
            maturity_state=MaturityState.CLOSED, corrects_idempotency_key="x"
        )


def test_correction_exige_referencia() -> None:
    with pytest.raises(ValidationError):
        MaturityAwarePayload(maturity_state=MaturityState.CORRECTION)
    p = MaturityAwarePayload(
        maturity_state=MaturityState.CORRECTION, corrects_idempotency_key="orig"
    )
    assert p.corrects_idempotency_key == "orig"


def test_reemission_referencia_opcional() -> None:
    a = MaturityAwarePayload(maturity_state=MaturityState.REEMISSION)
    b = MaturityAwarePayload(
        maturity_state=MaturityState.REEMISSION, corrects_idempotency_key="orig"
    )
    assert a.corrects_idempotency_key is None
    assert b.corrects_idempotency_key == "orig"


def test_candle_event_types_validos() -> None:
    for tipo in MarketCandleEventType:
        assert validate_event_type(tipo.value) == tipo.value
    assert MarketCandleEventType.CANDLE_CLOSED.value == "market.candle_closed"


def test_maturity_vive_en_payload_no_en_envelope() -> None:
    env = Envelope[MaturityAwarePayload](
        event_type=MarketCandleEventType.CANDLE_CLOSED.value,
        event_schema_version=1,
        source="test",
        idempotency_key="idem",
        stream_key="market:BTCUSDT:candle:1m",
        scope=Scope.PUBLIC_MARKET,
        correlation_id="corr",
        payload=MaturityAwarePayload(maturity_state=MaturityState.CLOSED),
    )
    assert env.payload.maturity_state is MaturityState.CLOSED
    assert "maturity_state" not in env.model_dump()
