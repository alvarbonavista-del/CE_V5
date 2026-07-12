"""Unit tests de kill_switch_targets: un caso por scope (P06-B8b)."""

from __future__ import annotations

from ce_v5.core.policy import kill_switch_targets


def _targets(
    *,
    scope: str,
    target_ref: str | None = None,
    switch_tenant_id: str | None = None,
    switch_user_id: str | None = None,
    component_capabilities: tuple[str, ...] = (),
    component_tenant_id: str | None = None,
    component_user_id: str | None = None,
) -> bool:
    return kill_switch_targets(
        scope=scope,
        target_ref=target_ref,
        switch_tenant_id=switch_tenant_id,
        switch_user_id=switch_user_id,
        component_capabilities=component_capabilities,
        component_tenant_id=component_tenant_id,
        component_user_id=component_user_id,
    )


def test_global_afecta_a_todo() -> None:
    assert _targets(scope="global") is True


def test_capability_afecta_si_apunta_a_una_capacidad() -> None:
    assert _targets(
        scope="capability",
        target_ref="execute_order",
        component_capabilities=("execute_order", "view_dashboard"),
    )


def test_capability_no_afecta_si_no_apunta() -> None:
    assert not _targets(
        scope="capability",
        target_ref="execute_order",
        component_capabilities=("view_dashboard",),
    )


def test_connector_afecta_por_capacidad_declarada() -> None:
    assert _targets(
        scope="connector",
        target_ref="binance",
        component_capabilities=("binance",),
    )


def test_tenant_afecta_si_comparte_tenant() -> None:
    assert _targets(scope="tenant", switch_tenant_id="t1", component_tenant_id="t1")
    assert not _targets(scope="tenant", switch_tenant_id="t1", component_tenant_id="t2")


def test_tenant_no_afecta_a_componente_global() -> None:
    # Componente sin tenant (global): un switch de tenant no muerde.
    assert not _targets(scope="tenant", switch_tenant_id="t1", component_tenant_id=None)


def test_user_afecta_si_comparte_tenant_y_usuario() -> None:
    assert _targets(
        scope="user",
        switch_tenant_id="t1",
        switch_user_id="u1",
        component_tenant_id="t1",
        component_user_id="u1",
    )
    assert not _targets(
        scope="user",
        switch_tenant_id="t1",
        switch_user_id="u1",
        component_tenant_id="t1",
        component_user_id="u2",
    )


def test_exchange_y_market_scope_no_muerden_en_lifecycle() -> None:
    # Recursos vivos que el lifecycle no conoce (asimetria del evaluator).
    assert not _targets(scope="exchange", target_ref="binance")
    assert not _targets(scope="market_scope", target_ref="spot")
