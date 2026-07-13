"""Resolucion de las entradas de politica de un sujeto (P06b; puerto de P06).

P06 dejo SubjectInputsResolver como PUERTO con el docstring "impl en P06b". Esta es esa
implementacion: reune jurisdiccion, KYC, VPN, plan y rol de un sujeto y se los entrega
al evaluador.

LA IP SE TOMA DE LA CONEXION, NUNCA DE UNA CABECERA. X-Forwarded-For la escribe el
cliente: fiarse de ella sin un proxy de confianza configurado permitiria a cualquiera
FINGIR QUE LLAMA DESDE OTRO PAIS y saltarse el geo-bloqueo. Preferimos no saber la
jurisdiccion (y denegar lo sensible por ello) antes que creernos una mentira.

FAIL-CLOSED POR AUSENCIA DE PROVEEDOR: en v5.0 no hay proveedor real de geolocalizacion,
KYC ni deteccion de VPN (su seleccion es frontera comercial de Alvaro). Sin proveedor,
la jurisdiccion es DESCONOCIDA, el KYC es UNKNOWN y la VPN es INDETERMINADA: por D5/D6
de P06, eso DENIEGA toda capacidad sensible. Es la respuesta correcta: sin saber de
donde llama alguien, no se le deja ejecutar nada.

PLAN Y ROL: hoy no existe fuente. El plan lo introduce P11 (billing) y el rol
administrativo es via v5.1. Ambos van a None, lo que (sin entitlement explicito) tambien
DENIEGA lo sensible. Registrado como tarea con pieza duena; no se inventa una fuente.
"""

from __future__ import annotations

from ce_v5.core.policy.inputs import (
    EvidenceSource,
    JurisdictionEvidence,
    PolicyInputs,
    TrustHierarchy,
    resolve_jurisdiction,
)
from ce_v5.core.policy.providers import IpGeoProvider, KycProvider, VpnDetector


class ApiSubjectInputsResolver:
    """Cumple el puerto SubjectInputsResolver de P06 para una peticion concreta."""

    def __init__(
        self,
        client_ip: str | None,
        ip_geo: IpGeoProvider,
        kyc: KycProvider,
        vpn: VpnDetector,
        hierarchy: TrustHierarchy | None = None,
    ) -> None:
        self._client_ip = client_ip
        self._ip_geo = ip_geo
        self._kyc = kyc
        self._vpn = vpn
        self._hierarchy = TrustHierarchy.default() if hierarchy is None else hierarchy

    def resolve(self, tenant_id: str, user_id: str | None) -> PolicyInputs:
        """Entradas ya resueltas del sujeto. Lo desconocido NO se inventa."""
        kyc_status = self._kyc.status_for_subject(tenant_id, user_id)
        evidences = [
            JurisdictionEvidence(
                source=EvidenceSource.KYC,
                jurisdiction=self._kyc.jurisdiction_for_subject(tenant_id, user_id),
            )
        ]

        # Sin IP de conexion no hay evidencia geografica NI deteccion de VPN: ambas
        # quedan indeterminadas, y por D5 eso deniega lo sensible.
        vpn_detected: bool | None = None
        if self._client_ip is not None:
            evidences.append(
                JurisdictionEvidence(
                    source=EvidenceSource.IP_GEO,
                    jurisdiction=self._ip_geo.jurisdiction_for_ip(self._client_ip),
                )
            )
            vpn_detected = self._vpn.is_vpn(self._client_ip)

        jurisdiction = resolve_jurisdiction(evidences, self._hierarchy)
        return PolicyInputs(
            subject_tenant_id=tenant_id,
            subject_user_id=user_id,
            jurisdiction=jurisdiction,
            kyc_status=kyc_status,
            vpn_detected=vpn_detected,
            # Sin fuente hoy: el plan es de P11 (billing) y el rol administrativo es
            # v5.1. No se inventa una fuente; sin entitlement, lo sensible se deniega.
            plan=None,
            role=None,
        )
