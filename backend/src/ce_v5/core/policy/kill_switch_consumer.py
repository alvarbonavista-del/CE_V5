"""Consumer que aisla componentes cuando se activa un kill switch (PASO 5).

FRONTERA CA-02 (regla dura, en codigo y no solo en prosa):
  policy.*    = CAUSA. El kill switch se activa y viaja como
                policy.kill_switch_activated.
  component.* = CONSECUENCIA. Este consumer lo consume y, si el switch afecta a
                una ComponentInstance VIVA, maneja al supervisor para aislarla;
                el supervisor emite component.quarantined con causation_id
                apuntando al event_id del policy.kill_switch_activated.

Este consumer NUNCA publica: solo maneja al supervisor. El kill switch JAMAS se
emite como component.*. Al desactivarse (policy.kill_switch_deactivated), libera
las instancias que ese mismo switch habia aislado, para que reintenten INITIALIZE.

Vive en core.policy (habla vocabulario de politica y maneja al supervisor de
core.component): la dependencia va core.policy -> core.component, sin ciclo.
"""

from __future__ import annotations

from ce_v5.core.component import ComponentInstance, Supervisor
from ce_v5.core.component.supervisor import component_capability_ids
from ce_v5.core.policy.decisions import ReasonCode
from ce_v5.core.policy.kill_switch_targeting import kill_switch_targets
from source.families.policy import KillSwitchPayload


class KillSwitchQuarantineConsumer:
    """Traduce activaciones/desactivaciones de kill switch a cuarentena (CA-02)."""

    def __init__(self, supervisor: Supervisor) -> None:
        self._supervisor = supervisor

    def on_activated(self, payload: KillSwitchPayload, *, event_id: str) -> None:
        """Aisla cada instancia VIVA afectada por el switch recien activado.

        causation_id = event_id del policy.kill_switch_activated (CA-02): la
        consecuencia (component.quarantined) apunta a su causa. reason_code
        denied_by_kill_switch: la cuarentena es depurable.
        """
        for instance in self._supervisor.live_instances():
            if self._affects(payload, instance):
                self._supervisor.quarantine(
                    instance.instance_id,
                    reason_code=ReasonCode.DENIED_BY_KILL_SWITCH.value,
                    causation_id=event_id,
                    switch_id=payload.kill_switch_id,
                )

    def on_deactivated(self, payload: KillSwitchPayload, *, event_id: str) -> None:
        """Libera las instancias que ESTE switch habia aislado (reintentan init).

        Solo libera las que quedaron en cuarentena por este kill_switch_id; otras
        causas de cuarentena (otro switch, denegacion de politica) siguen. El
        supervisor re-consulta el gate antes de arrancar.
        """
        for instance in self._supervisor.quarantined_instances():
            if instance.quarantine_switch_id == payload.kill_switch_id:
                self._supervisor.release_from_quarantine(
                    instance.instance_id, causation_id=event_id
                )

    @staticmethod
    def _affects(payload: KillSwitchPayload, instance: ComponentInstance) -> bool:
        return kill_switch_targets(
            scope=payload.scope.value,
            target_ref=payload.target_ref,
            switch_tenant_id=payload.tenant_id,
            switch_user_id=payload.user_id,
            component_capabilities=component_capability_ids(
                instance.definition.manifest
            ),
            component_tenant_id=instance.tenant_id,
            component_user_id=instance.user_id,
        )
