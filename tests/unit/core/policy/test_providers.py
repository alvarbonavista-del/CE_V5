"""Unit tests de los proveedores estaticos del gate (ADR-012).

Regla dura: lo NO conocido devuelve UNKNOWN/None, jamas un valor optimista.
"""

from __future__ import annotations

import pytest

from ce_v5.core.policy import (
    KycStatus,
    StaticIpGeoProvider,
    StaticKycProvider,
    StaticVpnDetector,
)


def test_ip_geo_conocida_y_desconocida() -> None:
    provider = StaticIpGeoProvider({"1.2.3.4": "ES"})
    assert provider.jurisdiction_for_ip("1.2.3.4") == "ES"
    # IP no conocida: None, nunca una jurisdiccion optimista.
    assert provider.jurisdiction_for_ip("9.9.9.9") is None


def test_kyc_sujeto_desconocido_da_unknown_y_none() -> None:
    provider = StaticKycProvider(
        statuses={("t1", "u1"): KycStatus.VERIFIED},
        jurisdictions={("t1", "u1"): "ES"},
    )
    assert provider.status_for_subject("t1", "u1") is KycStatus.VERIFIED
    assert provider.jurisdiction_for_subject("t1", "u1") == "ES"
    # Sujeto no conocido: fail-closed, jamas "verificado".
    assert provider.status_for_subject("t9", "u9") is KycStatus.UNKNOWN
    assert provider.jurisdiction_for_subject("t9", "u9") is None


def test_vpn_detector_tres_valores() -> None:
    detector = StaticVpnDetector(
        vpn_ips=frozenset({"1.1.1.1"}),
        clean_ips=frozenset({"2.2.2.2"}),
    )
    assert detector.is_vpn("1.1.1.1") is True
    assert detector.is_vpn("2.2.2.2") is False
    # IP no listada: DESCONOCIDO -> None, nunca un False optimista.
    assert detector.is_vpn("3.3.3.3") is None


def test_vpn_detector_ip_en_ambos_conjuntos_es_error() -> None:
    with pytest.raises(ValueError):
        StaticVpnDetector(
            vpn_ips=frozenset({"1.1.1.1"}),
            clean_ips=frozenset({"1.1.1.1"}),
        )
