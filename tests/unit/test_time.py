from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from source.time import (
    EpochMillis,
    LateEventPolicy,
    MaturityState,
    OutOfOrderPolicy,
    StreamTimePolicy,
    Watermark,
    to_iso8601,
)

_ms = TypeAdapter(EpochMillis)


def test_epoch_millis_acepta_valores_validos() -> None:
    assert _ms.validate_python(0) == 0
    assert _ms.validate_python(1_700_000_000_000) == 1_700_000_000_000


def test_epoch_millis_rechaza_negativo() -> None:
    with pytest.raises(ValidationError):
        _ms.validate_python(-1)


def test_epoch_millis_rechaza_mayor_que_int64() -> None:
    with pytest.raises(ValidationError):
        _ms.validate_python(2**63)


def test_to_iso8601_epoch_cero() -> None:
    assert to_iso8601(0) == "1970-01-01T00:00:00+00:00"


def test_to_iso8601_conserva_milisegundos() -> None:
    assert to_iso8601(1500) == "1970-01-01T00:00:01.500000+00:00"


def test_to_iso8601_coincide_con_datetime_aware() -> None:
    dt = datetime(2026, 7, 9, 12, 0, 0, tzinfo=UTC)
    ms = int(dt.timestamp()) * 1000
    assert to_iso8601(ms) == dt.isoformat()


def test_maturity_states_cerrados() -> None:
    assert {s.value for s in MaturityState} == {
        "provisional",
        "closed",
        "correction",
        "reemission",
    }


def test_politicas_valores() -> None:
    assert LateEventPolicy.REJECT_AFTER_WATERMARK.value == "reject_after_watermark"
    assert OutOfOrderPolicy.DROP_OLDER.value == "drop_older"


def test_watermark_valido_y_frozen() -> None:
    wm = Watermark(
        stream_key="market:BTCUSDT:candle:1m", watermark_time=1_700_000_000_000
    )
    assert wm.watermark_time == 1_700_000_000_000
    campo = "watermark_time"
    with pytest.raises(ValidationError):
        setattr(wm, campo, 0)


def test_watermark_rechaza_stream_key_vacio() -> None:
    with pytest.raises(ValidationError):
        Watermark(stream_key="", watermark_time=0)


def test_stream_time_policy_valido() -> None:
    pol = StreamTimePolicy(
        stream_key="market:BTCUSDT:trades",
        late_event_policy=LateEventPolicy.ACCEPT,
        out_of_order_policy=OutOfOrderPolicy.BEST_EFFORT,
    )
    assert pol.late_event_policy is LateEventPolicy.ACCEPT
    assert pol.out_of_order_policy is OutOfOrderPolicy.BEST_EFFORT
