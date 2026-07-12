"""Unit tests del contexto de auditoria de accion sensible (CA-05)."""

from __future__ import annotations

import json

from ce_v5.core.policy import (
    Decision,
    EvidenceSource,
    KycStatus,
    PolicyInputs,
    ReasonCode,
    ResolvedJurisdiction,
    build_context,
)
from ce_v5.core.policy.evaluator import CapabilityDecision

_EXPECTED_KEYS = {
    "jurisdiction",
    "jurisdiction_source",
    "jurisdiction_conflicting",
    "kyc_status",
    "vpn_detected",
    "plan",
    "role",
    "kill_switch_id",
}


def _inputs() -> PolicyInputs:
    return PolicyInputs(
        subject_tenant_id="t1",
        subject_user_id="u1",
        jurisdiction=ResolvedJurisdiction(
            "AA", EvidenceSource.IP_GEO, conflicting=True
        ),
        kyc_status=KycStatus.VERIFIED,
        vpn_detected=False,
        plan="plan_x",
        role="trader",
    )


def _decision() -> CapabilityDecision:
    return CapabilityDecision(
        capability_id="execute_order",
        decision=Decision.DENY,
        reason_code=ReasonCode.DENIED_BY_KILL_SWITCH,
        policy_version="v1",
        sensitive=True,
        kill_switch_id="ks-1",
    )


def test_build_context_incluye_veredictos_y_referencias() -> None:
    context = build_context(_inputs(), _decision())
    assert context["jurisdiction"] == "AA"
    assert context["jurisdiction_source"] == "ip_geo"
    assert context["jurisdiction_conflicting"] is True
    assert context["kyc_status"] == "verified"
    assert context["vpn_detected"] is False
    assert context["plan"] == "plan_x"
    assert context["role"] == "trader"
    assert context["kill_switch_id"] == "ks-1"


def test_build_context_no_incluye_ip_ni_datos_crudos() -> None:
    # La jurisdiccion se resolvio DESDE ip_geo, pero la IP nunca llega a
    # PolicyInputs (vive en la capa de proveedores, B3): el contexto guarda el
    # VEREDICTO (jurisdiccion 'AA', fuente 'ip_geo'), jamas la IP.
    context = build_context(_inputs(), _decision())
    assert set(context) == _EXPECTED_KEYS  # conjunto CERRADO de claves
    assert not any("ip" == key.lower() for key in context)
    serialized = json.dumps(context)
    assert "192.168" not in serialized
    assert "@" not in serialized  # ni correos ni credenciales crudas
