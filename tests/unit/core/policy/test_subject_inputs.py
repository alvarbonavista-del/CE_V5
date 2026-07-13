"""Tests del resolver de entradas de sujeto (P06b; puerto de P06). Sin infraestructura.

Lo NO conocido se queda DESCONOCIDO: el resolver no inventa jurisdiccion, ni KYC, ni
deteccion de VPN. Esa es la entrada que hace que el motor deniegue lo sensible (D5/D6).
"""

from ce_v5.core.policy.inputs import EvidenceSource, KycStatus
from ce_v5.core.policy.providers import (
    StaticIpGeoProvider,
    StaticKycProvider,
    StaticVpnDetector,
)
from ce_v5.core.policy.subject_inputs import ApiSubjectInputsResolver

# IP de documentacion (TEST-NET-3): claramente ficticia, jamas una IP real.
_IP = "203.0.113.10"
_TENANT = "tenant-de-test"
_USER = "user-de-test"


def _vacio(client_ip: str | None = _IP) -> ApiSubjectInputsResolver:
    return ApiSubjectInputsResolver(
        client_ip=client_ip,
        ip_geo=StaticIpGeoProvider({}),
        kyc=StaticKycProvider({}, {}),
        vpn=StaticVpnDetector(frozenset(), frozenset()),
    )


def test_sin_proveedores_todo_queda_desconocido() -> None:
    inputs = _vacio().resolve(_TENANT, _USER)

    assert inputs.jurisdiction.jurisdiction is None
    assert inputs.jurisdiction.source is None
    assert inputs.kyc_status is KycStatus.UNKNOWN
    assert inputs.vpn_detected is None
    assert inputs.plan is None
    assert inputs.role is None


def test_la_jurisdiccion_puede_salir_de_la_ip() -> None:
    resolver = ApiSubjectInputsResolver(
        client_ip=_IP,
        ip_geo=StaticIpGeoProvider({_IP: "AA"}),
        kyc=StaticKycProvider({}, {}),
        vpn=StaticVpnDetector(frozenset(), frozenset({_IP})),
    )
    inputs = resolver.resolve(_TENANT, _USER)

    assert inputs.jurisdiction.jurisdiction == "AA"
    assert inputs.jurisdiction.source is EvidenceSource.IP_GEO


def test_la_jurisdiccion_de_kyc_prevalece_sobre_la_de_la_ip() -> None:
    # Jerarquia de confianza por defecto: KYC > IP_GEO > DECLARED. La fuente mas fiable
    # con dato GANA, aunque las demas la contradigan (no hay votacion).
    resolver = ApiSubjectInputsResolver(
        client_ip=_IP,
        ip_geo=StaticIpGeoProvider({_IP: "BB"}),
        kyc=StaticKycProvider(
            statuses={(_TENANT, _USER): KycStatus.VERIFIED},
            jurisdictions={(_TENANT, _USER): "AA"},
        ),
        vpn=StaticVpnDetector(frozenset(), frozenset({_IP})),
    )
    inputs = resolver.resolve(_TENANT, _USER)

    assert inputs.jurisdiction.jurisdiction == "AA"
    assert inputs.jurisdiction.source is EvidenceSource.KYC
    assert inputs.jurisdiction.conflicting is True
    assert inputs.kyc_status is KycStatus.VERIFIED


def test_sin_ip_no_hay_evidencia_de_ip_ni_deteccion_de_vpn() -> None:
    resolver = ApiSubjectInputsResolver(
        client_ip=None,
        ip_geo=StaticIpGeoProvider({_IP: "BB"}),
        kyc=StaticKycProvider({}, {}),
        vpn=StaticVpnDetector(frozenset({_IP}), frozenset()),
    )
    inputs = resolver.resolve(_TENANT, _USER)

    # La IP conocida del proveedor NO se usa: no hay IP de conexion que geolocalizar.
    assert inputs.jurisdiction.jurisdiction is None
    assert inputs.vpn_detected is None


def test_una_ip_de_vpn_se_detecta() -> None:
    resolver = ApiSubjectInputsResolver(
        client_ip=_IP,
        ip_geo=StaticIpGeoProvider({}),
        kyc=StaticKycProvider({}, {}),
        vpn=StaticVpnDetector(vpn_ips=frozenset({_IP}), clean_ips=frozenset()),
    )
    assert resolver.resolve(_TENANT, _USER).vpn_detected is True
