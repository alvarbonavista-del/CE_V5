"""Unit tests del registro event_type -> payload y su check (CA-06)."""

from __future__ import annotations

import pytest

import check_event_payload_registry
from source.envelope import EventPayload
from source.families import registry
from source.families.component import ComponentLifecyclePayload
from source.families.market import CandleClosedPayload
from source.families.policy import KillSwitchPayload, SubjectInvalidatedPayload
from source.families.registry import (
    DEFERRED_EVENT_TYPES,
    DEFERRED_STATUS,
    EVENT_PAYLOAD_REGISTRY,
    DeferredEventType,
    DeferredEventTypeError,
    UnknownEventTypePayloadError,
    payload_class_for,
)

# Tipo de ejemplo para probar la MECANICA del mapa de diferidos. Ya NO puede ser un
# market.*: desde P07 los tres estan REGISTRADOS con payload real, y usarlos aqui
# afirmaria un hecho falso. El mecanismo se prueba con un tipo de mentira.
_DEFERRED_ET = "datasource.demo_deferred"


class _Concrete(EventPayload):
    x: int


def _deferred(**overrides: str) -> DeferredEventType:
    """Una DeferredEventType valida; los tests sobreescriben el campo a romper."""
    base: dict[str, str] = {
        "event_type": _DEFERRED_ET,
        "family": "datasource",
        "motivo": "taxonomia declarada hoy",
        "owner_piece": "P08",
        "dependency_reason": "su payload y su productor los define una pieza futura",
        "exit_rule": "al cerrar la pieza duena se registra o se elimina",
        "status": DEFERRED_STATUS,
    }
    base.update(overrides)
    return DeferredEventType(**base)


def test_payload_class_for_tipo_registrado() -> None:
    assert payload_class_for("policy.subject_invalidated") is SubjectInvalidatedPayload
    assert payload_class_for("policy.kill_switch_activated") is KillSwitchPayload
    assert payload_class_for("component.running") is ComponentLifecyclePayload


def test_payload_class_for_tipo_diferido(monkeypatch: pytest.MonkeyPatch) -> None:
    # Desde P07 no queda NINGUN tipo diferido real (los tres market.* ya tienen
    # payload y productor). El MECANISMO sigue vivo y se prueba con un diferido
    # inyectado: si manana alguien difiere un tipo, payload_class_for debe seguir
    # negandose a resolverlo en vez de devolver un payload inventado.
    monkeypatch.setitem(registry.DEFERRED_EVENT_TYPES, _DEFERRED_ET, _deferred())
    with pytest.raises(DeferredEventTypeError, match="P08"):
        payload_class_for(_DEFERRED_ET)


def test_payload_class_for_market_ya_no_esta_diferido() -> None:
    # El hecho que cambio en P07: market.candle_closed pasa de diferido a
    # REGISTRADO con su payload concreto (tarea vinculante CA-06, pagada).
    assert payload_class_for("market.candle_closed") is CandleClosedPayload


def test_payload_class_for_tipo_desconocido() -> None:
    with pytest.raises(UnknownEventTypePayloadError):
        payload_class_for("component.demo")


def test_check_falla_si_falta_en_ambos_mapas() -> None:
    problems = check_event_payload_registry.check_registry({"foo.bar"}, {}, {})
    assert len(problems) == 1
    assert "sin entrada" in problems[0]


def test_check_falla_si_apunta_a_eventpayload_base() -> None:
    problems = check_event_payload_registry.check_registry(
        {"x.y"}, {"x.y": (EventPayload, 1)}, {}
    )
    assert any("EventPayload BASE" in problem for problem in problems)


def test_check_falla_si_apunta_a_dict() -> None:
    problems = check_event_payload_registry.check_registry(
        {"x.y"}, {"x.y": (dict, 1)}, {}
    )
    assert any("no es un modelo Pydantic" in problem for problem in problems)


def test_check_falla_si_en_ambos_mapas() -> None:
    problems = check_event_payload_registry.check_registry(
        {"x.y"}, {"x.y": (_Concrete, 1)}, {"x.y": _deferred(event_type="x.y")}
    )
    assert any("registro Y en los diferidos" in problem for problem in problems)


def test_check_verde_con_entrada_valida() -> None:
    problems = check_event_payload_registry.check_registry(
        {"x.y"}, {"x.y": (_Concrete, 1)}, {}
    )
    assert problems == []


def test_check_verde_con_diferido_valido() -> None:
    problems = check_event_payload_registry.check_registry(
        {_DEFERRED_ET}, {}, {_DEFERRED_ET: _deferred()}
    )
    assert problems == []


def test_check_falla_si_deferred_sin_owner_piece() -> None:
    # PRUEBA NEGATIVA que exige el CSA: un diferido sin duenno (owner_piece vacio)
    # no puede arrancar. Sin duenno, nadie lo pagara.
    problems = check_event_payload_registry.check_registry(
        {_DEFERRED_ET}, {}, {_DEFERRED_ET: _deferred(owner_piece="")}
    )
    assert any("owner_piece" in problem for problem in problems)


def test_check_falla_sin_dependency_reason() -> None:
    problems = check_event_payload_registry.check_registry(
        {_DEFERRED_ET}, {}, {_DEFERRED_ET: _deferred(dependency_reason="")}
    )
    assert any("dependency_reason" in problem for problem in problems)


def test_check_falla_sin_exit_rule() -> None:
    problems = check_event_payload_registry.check_registry(
        {_DEFERRED_ET}, {}, {_DEFERRED_ET: _deferred(exit_rule="   ")}
    )
    assert any("exit_rule" in problem for problem in problems)


def test_check_falla_con_status_incorrecto() -> None:
    problems = check_event_payload_registry.check_registry(
        {_DEFERRED_ET}, {}, {_DEFERRED_ET: _deferred(status="parked")}
    )
    assert any("status" in problem for problem in problems)


def test_check_falla_owner_piece_pieza_cerrada() -> None:
    # Diferir a una pieza YA CERRADA (P03) es deuda disfrazada: nadie lo pagara.
    problems = check_event_payload_registry.check_registry(
        {_DEFERRED_ET}, {}, {_DEFERRED_ET: _deferred(owner_piece="P03")}
    )
    assert any("YA CERRADA" in problem for problem in problems)


def test_check_falla_owner_piece_inexistente() -> None:
    problems = check_event_payload_registry.check_registry(
        {_DEFERRED_ET}, {}, {_DEFERRED_ET: _deferred(owner_piece="P99")}
    )
    assert any("no es una pieza del roadmap" in problem for problem in problems)


def test_check_falla_entrada_diferida_no_estructurada() -> None:
    problems = check_event_payload_registry.check_registry(
        {_DEFERRED_ET}, {}, {_DEFERRED_ET: "P07"}
    )
    assert any("DeferredEventType" in problem for problem in problems)


def test_check_falla_tipo_diferido_en_uso() -> None:
    # Un tipo diferido que el codigo ya usa es una mentira en el registro. Se
    # inyecta in_use (doble del escaneo) para probarlo sin ensuciar backend/src.
    problems = check_event_payload_registry.check_registry(
        {_DEFERRED_ET},
        {},
        {_DEFERRED_ET: _deferred()},
        in_use={_DEFERRED_ET},
    )
    assert any("EN USO" in problem for problem in problems)


def test_registro_real_pasa_el_check() -> None:
    # End-to-end: registro real + escaneo real de backend/src. Desde P07 el mapa de
    # diferidos esta VACIO (los tres market.* pasaron a EVENT_PAYLOAD_REGISTRY con
    # su payload real), asi que no hay nada que escanear y ningun event_type
    # declarado se queda sin payload.
    declared = check_event_payload_registry._declared_event_types()
    in_use = check_event_payload_registry.scan_in_use(
        set(DEFERRED_EVENT_TYPES), check_event_payload_registry._BACKEND_SRC
    )
    assert in_use == set()
    problems = check_event_payload_registry.check_registry(
        declared, EVENT_PAYLOAD_REGISTRY, DEFERRED_EVENT_TYPES, in_use=in_use
    )
    assert problems == []
