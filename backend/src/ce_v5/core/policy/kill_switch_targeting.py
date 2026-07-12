"""A que COMPONENTE afecta un kill switch (P06, frontera CA-02).

Funcion pura, espejo de evaluator._kill_switch_applies pero a granularidad de
ComponentInstance en vez de a una pregunta de capability con recurso. La usan
dos sitios: el gate de lifecycle (instancia GLOBAL: solo kill switches de
plataforma) y el consumer que aisla instancias vivas cuando se activa un switch.

Vive en core.policy porque interpreta vocabulario de politica (KillSwitchScope);
core.component solo conoce el puerto neutro. Asi la dependencia fluye
core.policy -> core.component, sin ciclo.
"""

from __future__ import annotations

from collections.abc import Sequence

from source.families.policy import KillSwitchScope


def kill_switch_targets(
    *,
    scope: str,
    target_ref: str | None,
    switch_tenant_id: str | None,
    switch_user_id: str | None,
    component_capabilities: Sequence[str],
    component_tenant_id: str | None,
    component_user_id: str | None,
) -> bool:
    """True si el kill switch afecta a un componente con estos rasgos.

    - global: afecta a TODO.
    - capability / connector: afecta si target_ref esta entre las capacidades
      del componente. (Un connector declarado en los requisitos del componente
      encaja; el emparejamiento por capacidades PROVISTAS llega con P06b, cuando
      se cablee ese dato.)
    - tenant: afecta si el switch y el componente comparten tenant.
    - user: afecta si comparten tenant Y usuario.
    - exchange / market_scope: recursos vivos que el lifecycle no conoce; misma
      asimetria que el evaluator (sin recurso, el switch no muerde) -> no afecta.
    """
    if scope == KillSwitchScope.GLOBAL.value:
        return True
    if scope in (KillSwitchScope.CAPABILITY.value, KillSwitchScope.CONNECTOR.value):
        return target_ref is not None and target_ref in component_capabilities
    if scope == KillSwitchScope.TENANT.value:
        return switch_tenant_id is not None and switch_tenant_id == component_tenant_id
    if scope == KillSwitchScope.USER.value:
        return (
            switch_tenant_id is not None
            and switch_tenant_id == component_tenant_id
            and switch_user_id is not None
            and switch_user_id == component_user_id
        )
    return False
