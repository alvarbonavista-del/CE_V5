"""Entradas resueltas del gate y jerarquia de confianza (ADR-012).

Define el VOCABULARIO y las ESTRUCTURAS de entrada que el PolicyEvaluator (B4)
consumira; aqui NO se decide nada. La jurisdiccion se resuelve segun una
jerarquia de confianza CONFIGURABLE por despliegue (ADR-012): la fuente mas
fiable con dato gana, sin votacion ni mayoria.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class KycStatus(StrEnum):
    """Estado KYC del sujeto. UNKNOWN cuando no se ha podido determinar."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    UNKNOWN = "unknown"


class EvidenceSource(StrEnum):
    """Fuente de una evidencia de jurisdiccion."""

    KYC = "kyc"
    IP_GEO = "ip_geo"
    DECLARED = "declared"


@dataclass(frozen=True, slots=True)
class JurisdictionEvidence:
    """Una evidencia de jurisdiccion tal cual la aporta una fuente.

    jurisdiction es el codigo de pais/region tal cual lo da la fuente; NO se
    valida contra ningun catalogo: el catalogo de jurisdicciones es DATO de
    negocio de Alvaro, no codigo (ADR-012). None (o cadena vacia) = sin dato.
    """

    source: EvidenceSource
    jurisdiction: str | None


@dataclass(frozen=True, slots=True)
class ResolvedJurisdiction:
    """Jurisdiccion resuelta segun la jerarquia de confianza (ADR-012).

    jurisdiction y source son None si ninguna fuente confiable aporto dato. El
    motor (B4) tratara jurisdiction=None como DESCONOCIDO y, en capacidades
    sensibles, resolvera DENY (fail-closed, ADR-012). Esa consecuencia NO se
    implementa aqui: este modulo solo aporta la entrada.

    conflicting=True si alguna fuente de la jerarquia aporto una jurisdiccion
    DISTINTA de la ganadora. Es informacion para la auditoria y para que una
    regla pueda ENDURECERSE; por si sola NO cambia la decision.
    """

    jurisdiction: str | None
    source: EvidenceSource | None
    conflicting: bool


@dataclass(frozen=True, slots=True)
class TrustHierarchy:
    """Orden de confianza de las fuentes, de MAS a MENOS fiable (ADR-012).

    CONFIGURABLE por despliegue: default() da un orden POR DEFECTO documentado,
    no una verdad. Una fuente ausente de la tupla se considera NO CONFIABLE: su
    evidencia se ignora aunque traiga dato. La tupla no puede estar vacia ni
    tener fuentes duplicadas.
    """

    order: tuple[EvidenceSource, ...]

    def __post_init__(self) -> None:
        if not self.order:
            msg = "TrustHierarchy.order no puede estar vacia."
            raise ValueError(msg)
        if len(set(self.order)) != len(self.order):
            msg = "TrustHierarchy.order no puede tener fuentes duplicadas."
            raise ValueError(msg)

    @classmethod
    def default(cls) -> TrustHierarchy:
        """Orden por defecto documentado: KYC > IP_GEO > DECLARED (no es verdad)."""
        return cls(
            order=(EvidenceSource.KYC, EvidenceSource.IP_GEO, EvidenceSource.DECLARED)
        )


def resolve_jurisdiction(
    evidences: Sequence[JurisdictionEvidence],
    hierarchy: TrustHierarchy,
) -> ResolvedJurisdiction:
    """Resuelve la jurisdiccion segun la jerarquia de confianza (ADR-012).

    Recorre la jerarquia de MAS fiable a MENOS y devuelve la PRIMERA fuente que
    aporte jurisdiccion no vacia: la fuente mas fiable con dato GANA, aunque las
    demas la contradigan (no hay votacion ni mayoria). Una fuente ausente de la
    jerarquia se ignora aunque traiga dato. Si ninguna fuente confiable aporta
    dato, jurisdiction=None y source=None (DESCONOCIDO para el motor, que en
    capacidades sensibles resolvera DENY; eso lo hace B4, no este modulo).
    """
    # Indexa por fuente; si una fuente repite evidencia, la primera manda.
    by_source: dict[EvidenceSource, str | None] = {}
    for evidence in evidences:
        if evidence.source not in by_source:
            by_source[evidence.source] = evidence.jurisdiction

    winner_source: EvidenceSource | None = None
    winner_jurisdiction: str | None = None
    for source in hierarchy.order:
        value = by_source.get(source)
        if value:
            winner_source = source
            winner_jurisdiction = value
            break

    conflicting = False
    if winner_jurisdiction is not None:
        for source in hierarchy.order:
            value = by_source.get(source)
            if value and value != winner_jurisdiction:
                conflicting = True
                break

    return ResolvedJurisdiction(
        jurisdiction=winner_jurisdiction,
        source=winner_source,
        conflicting=conflicting,
    )


@dataclass(frozen=True, slots=True)
class PolicyInputs:
    """Las entradas ya resueltas que el motor (B4) consumira.

    plan y role los APORTA EL LLAMADOR. Hoy no existen cuentas, planes ni
    sesion (P06b y P11); cuando existan, P06b los rellenara desde la identidad
    autenticada. Los entitlements NO van aqui: los lee el motor de la DB (B4).
    vpn_detected=None significa que el detector no pudo determinarlo.
    """

    subject_tenant_id: str
    subject_user_id: str | None
    jurisdiction: ResolvedJurisdiction
    kyc_status: KycStatus
    vpn_detected: bool | None
    plan: str | None
    role: str | None
