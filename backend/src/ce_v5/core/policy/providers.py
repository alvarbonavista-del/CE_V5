"""Puertos de las fuentes externas del gate (ADR-012).

FRONTERA: la SELECCION E INTEGRACION de un proveedor comercial de deteccion de
VPN o de verificacion KYC es decision de negocio de Alvaro con su asesoria, NO
de esta pieza. Aqui se define el CONTRATO de entrada y una implementacion local
determinista para tests y validacion en caliente. Cambiar de proveedor no debe
tocar el motor: se implementa el Protocol.

Regla dura de los proveedores: lo NO CONOCIDO devuelve UNKNOWN/None, jamas un
valor optimista. Un proveedor que ante la duda dice "verificado" es un agujero.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from ce_v5.core.policy.inputs import KycStatus


@runtime_checkable
class IpGeoProvider(Protocol):
    """Geolocaliza una IP en una jurisdiccion (codigo tal cual lo da la fuente)."""

    def jurisdiction_for_ip(self, ip: str) -> str | None:
        """Jurisdiccion de la IP, o None si no se puede determinar."""
        ...


@runtime_checkable
class KycProvider(Protocol):
    """Estado KYC y jurisdiccion de un sujeto segun la verificacion."""

    def status_for_subject(self, tenant_id: str, user_id: str | None) -> KycStatus:
        """Estado KYC del sujeto. UNKNOWN si no se conoce (fail-closed)."""
        ...

    def jurisdiction_for_subject(
        self, tenant_id: str, user_id: str | None
    ) -> str | None:
        """Jurisdiccion del sujeto segun KYC, o None si no se conoce."""
        ...


@runtime_checkable
class VpnDetector(Protocol):
    """Detecta si una IP es una VPN/proxy."""

    def is_vpn(self, ip: str) -> bool | None:
        """True/False, o None si el detector no pudo determinarlo."""
        ...


class StaticIpGeoProvider:
    """IpGeoProvider deterministico para tests y validacion en caliente.

    Lo NO conocido devuelve None: nunca una jurisdiccion optimista.
    """

    def __init__(self, mapping: Mapping[str, str]) -> None:
        self._mapping = dict(mapping)

    def jurisdiction_for_ip(self, ip: str) -> str | None:
        return self._mapping.get(ip)


class StaticKycProvider:
    """KycProvider deterministico. Sujeto no conocido -> UNKNOWN / None.

    Fail-closed por omision: ante la duda jamas "verificado".
    """

    def __init__(
        self,
        statuses: Mapping[tuple[str, str | None], KycStatus],
        jurisdictions: Mapping[tuple[str, str | None], str],
    ) -> None:
        self._statuses = dict(statuses)
        self._jurisdictions = dict(jurisdictions)

    def status_for_subject(self, tenant_id: str, user_id: str | None) -> KycStatus:
        return self._statuses.get((tenant_id, user_id), KycStatus.UNKNOWN)

    def jurisdiction_for_subject(
        self, tenant_id: str, user_id: str | None
    ) -> str | None:
        return self._jurisdictions.get((tenant_id, user_id))


class StaticVpnDetector:
    """VpnDetector deterministico de tres valores.

    vpn_ips -> True (VPN conocida); clean_ips -> False (limpia conocida). Toda
    IP no listada en ninguno de los dos -> None: lo NO conocido es DESCONOCIDO
    por omision, jamas un False optimista. Un detector real debe hacer lo mismo:
    ante la duda None, nunca "no es VPN". Una IP en ambos conjuntos es un error
    de configuracion del doble (no se elige en silencio): ValueError.
    """

    def __init__(self, vpn_ips: frozenset[str], clean_ips: frozenset[str]) -> None:
        solapadas = vpn_ips & clean_ips
        if solapadas:
            msg = (
                "StaticVpnDetector: IPs en vpn_ips y clean_ips a la vez "
                f"(configuracion ambigua): {sorted(solapadas)}."
            )
            raise ValueError(msg)
        self._vpn_ips = frozenset(vpn_ips)
        self._clean_ips = frozenset(clean_ips)

    def is_vpn(self, ip: str) -> bool | None:
        if ip in self._vpn_ips:
            return True
        if ip in self._clean_ips:
            return False
        return None
