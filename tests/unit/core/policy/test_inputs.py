"""Unit tests de las entradas del gate y la jerarquia de confianza (ADR-012)."""

from __future__ import annotations

import pytest

from ce_v5.core.policy import (
    EvidenceSource,
    JurisdictionEvidence,
    TrustHierarchy,
    resolve_jurisdiction,
)


def _ev(source: EvidenceSource, jurisdiction: str | None) -> JurisdictionEvidence:
    return JurisdictionEvidence(source=source, jurisdiction=jurisdiction)


def test_la_fuente_mas_fiable_con_dato_gana() -> None:
    evidences = [
        _ev(EvidenceSource.KYC, "ES"),
        _ev(EvidenceSource.IP_GEO, "FR"),
        _ev(EvidenceSource.DECLARED, "DE"),
    ]
    result = resolve_jurisdiction(evidences, TrustHierarchy.default())
    assert result.jurisdiction == "ES"
    assert result.source is EvidenceSource.KYC
    assert result.conflicting is True


def test_cae_a_la_siguiente_si_la_mas_fiable_no_tiene_dato() -> None:
    evidences = [
        _ev(EvidenceSource.KYC, None),
        _ev(EvidenceSource.IP_GEO, "FR"),
    ]
    result = resolve_jurisdiction(evidences, TrustHierarchy.default())
    assert result.jurisdiction == "FR"
    assert result.source is EvidenceSource.IP_GEO


def test_ninguna_fuente_con_dato_da_none() -> None:
    evidences = [
        _ev(EvidenceSource.KYC, None),
        _ev(EvidenceSource.IP_GEO, None),
    ]
    result = resolve_jurisdiction(evidences, TrustHierarchy.default())
    assert result.jurisdiction is None
    assert result.source is None
    assert result.conflicting is False


def test_conflicting_false_si_todas_coinciden() -> None:
    evidences = [
        _ev(EvidenceSource.KYC, "ES"),
        _ev(EvidenceSource.IP_GEO, "ES"),
    ]
    result = resolve_jurisdiction(evidences, TrustHierarchy.default())
    assert result.jurisdiction == "ES"
    assert result.conflicting is False


def test_fuente_fuera_de_la_jerarquia_se_ignora() -> None:
    # DECLARED no esta en la jerarquia: su dato se ignora aunque exista, y no
    # cuenta como conflicto.
    hierarchy = TrustHierarchy(order=(EvidenceSource.KYC, EvidenceSource.IP_GEO))
    evidences = [
        _ev(EvidenceSource.KYC, None),
        _ev(EvidenceSource.IP_GEO, None),
        _ev(EvidenceSource.DECLARED, "US"),
    ]
    result = resolve_jurisdiction(evidences, hierarchy)
    assert result.jurisdiction is None
    assert result.source is None
    assert result.conflicting is False


def test_cambiar_el_orden_cambia_el_ganador() -> None:
    evidences = [
        _ev(EvidenceSource.KYC, "ES"),
        _ev(EvidenceSource.DECLARED, "US"),
    ]
    declared_first = TrustHierarchy(
        order=(EvidenceSource.DECLARED, EvidenceSource.KYC, EvidenceSource.IP_GEO)
    )
    result = resolve_jurisdiction(evidences, declared_first)
    assert result.jurisdiction == "US"
    assert result.source is EvidenceSource.DECLARED
    assert result.conflicting is True


def test_trust_hierarchy_rechaza_tupla_vacia() -> None:
    with pytest.raises(ValueError):
        TrustHierarchy(order=())


def test_trust_hierarchy_rechaza_duplicados() -> None:
    with pytest.raises(ValueError):
        TrustHierarchy(order=(EvidenceSource.KYC, EvidenceSource.KYC))
