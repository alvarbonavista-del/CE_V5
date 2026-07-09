import pytest

from families import Family, validate_event_type


def test_diez_familias_cerradas() -> None:
    esperadas = {
        "market",
        "datasource",
        "rule",
        "signal",
        "alert",
        "execution",
        "notification",
        "user",
        "component",
        "billing",
    }
    assert {f.value for f in Family} == esperadas


def test_event_type_valido() -> None:
    assert validate_event_type("market.tick") == "market.tick"
    assert validate_event_type("execution.order_placed") == "execution.order_placed"


def test_event_type_familia_desconocida() -> None:
    with pytest.raises(ValueError):
        validate_event_type("desconocido.accion")


def test_event_type_forma_invalida() -> None:
    for malo in ("market", "market.", "Market.Tick", "market.Tick", ".tick"):
        with pytest.raises(ValueError):
            validate_event_type(malo)
