"""Unit tests del cache del capability set y su invalidacion (ADR-012)."""

from __future__ import annotations

from ce_v5.core.clock import SimulatedClock
from ce_v5.core.policy import (
    CacheKey,
    CapabilitySet,
    CapabilitySetCache,
    PolicyCacheInvalidator,
    ResourceContext,
    capabilities_digest,
    resources_digest,
)
from source.families.policy import (
    InvalidationReason,
    KillSwitchPayload,
    KillSwitchScope,
    PolicyVersionPublishedPayload,
    SubjectInvalidatedPayload,
)


def _set(
    tenant: str = "t1",
    user: str | None = "u1",
    version: str = "v1",
    evaluated_at: int = 0,
) -> CapabilitySet:
    return CapabilitySet(
        tenant_id=tenant,
        user_id=user,
        policy_version=version,
        evaluated_at=evaluated_at,
        decisions={},
    )


def test_la_clave_incluye_tenant_id() -> None:
    cache = CapabilitySetCache(SimulatedClock(), max_staleness_ms=1000)
    key_a = CacheKey("t1", "u1", "v1", "d", "c")
    key_b = CacheKey("t2", "u1", "v1", "d", "c")
    cache.put(key_a, _set(tenant="t1"))
    assert cache.get(key_a) is not None
    assert cache.get(key_b) is None


def test_resource_context_distinto_no_comparte_entrada() -> None:
    d1 = resources_digest(ResourceContext(exchange="binance"))
    d2 = resources_digest(ResourceContext(exchange="okx"))
    assert d1 != d2
    cache = CapabilitySetCache(SimulatedClock(), max_staleness_ms=1000)
    cache.put(CacheKey("t1", "u1", "v1", d1, "c"), _set())
    assert cache.get(CacheKey("t1", "u1", "v1", d2, "c")) is None


def test_capabilities_digest_estable_frente_a_orden_y_duplicados() -> None:
    # El digest debe ser el MISMO para la misma pregunta escrita de otra forma.
    assert capabilities_digest(["a", "b"]) == capabilities_digest(["b", "a"])
    assert capabilities_digest(["a", "a", "b"]) == capabilities_digest(["a", "b"])
    assert capabilities_digest(["a"]) != capabilities_digest(["b"])


def test_capabilities_distintas_no_comparten_entrada() -> None:
    # REGRESION B9: una respuesta a una pregunta NO vale para OTRA pregunta.
    c1 = capabilities_digest(["execute_order"])
    c2 = capabilities_digest(["view_dashboard"])
    cache = CapabilitySetCache(SimulatedClock(), max_staleness_ms=1000)
    cache.put(CacheKey("t1", "u1", "v1", "d", c1), _set())
    assert cache.get(CacheKey("t1", "u1", "v1", "d", c1)) is not None
    assert cache.get(CacheKey("t1", "u1", "v1", "d", c2)) is None


def test_ttl_pasado_max_staleness_devuelve_none() -> None:
    clock = SimulatedClock(start_ms=0)
    cache = CapabilitySetCache(clock, max_staleness_ms=1000)
    key = CacheKey("t1", "u1", "v1", "d", "c")
    cache.put(key, _set(evaluated_at=0))
    clock.set(1000)
    assert cache.get(key) is not None  # 1000 - 0 = 1000, no supera 1000
    clock.set(1001)
    assert cache.get(key) is None


def test_invalidate_subject_tira_solo_al_sujeto() -> None:
    cache = CapabilitySetCache(SimulatedClock(), max_staleness_ms=10000)
    a = CacheKey("t1", "u1", "v1", "d", "c")
    b = CacheKey("t2", "u2", "v1", "d", "c")
    cache.put(a, _set())
    cache.put(b, _set(tenant="t2", user="u2"))
    cache.invalidate_subject("t1", "u1")
    assert cache.get(a) is None
    assert cache.get(b) is not None


def test_invalidate_subject_user_none_tira_todo_el_tenant() -> None:
    cache = CapabilitySetCache(SimulatedClock(), max_staleness_ms=10000)
    a1 = CacheKey("t1", "u1", "v1", "d", "c")
    a2 = CacheKey("t1", "u2", "v1", "d", "c")
    b = CacheKey("t2", "u1", "v1", "d", "c")
    cache.put(a1, _set())
    cache.put(a2, _set(user="u2"))
    cache.put(b, _set(tenant="t2"))
    cache.invalidate_subject("t1", None)
    assert cache.get(a1) is None
    assert cache.get(a2) is None
    assert cache.get(b) is not None


def test_version_published_invalida_todo() -> None:
    cache = CapabilitySetCache(SimulatedClock(), max_staleness_ms=10000)
    cache.put(CacheKey("t1", "u1", "v1", "d", "c"), _set())
    cache.put(CacheKey("t2", "u1", "v1", "d", "c"), _set(tenant="t2"))
    PolicyCacheInvalidator(cache).on_version_published(
        PolicyVersionPublishedPayload(policy_version="v2", actor="admin")
    )
    assert cache.get(CacheKey("t1", "u1", "v1", "d", "c")) is None
    assert cache.get(CacheKey("t2", "u1", "v1", "d", "c")) is None


def test_kill_switch_changed_invalida_todo() -> None:
    cache = CapabilitySetCache(SimulatedClock(), max_staleness_ms=10000)
    cache.put(CacheKey("t1", "u1", "v1", "d", "c"), _set())
    PolicyCacheInvalidator(cache).on_kill_switch_changed(
        KillSwitchPayload(
            kill_switch_id="k1",
            scope=KillSwitchScope.GLOBAL,
            reason_code="manual",
            policy_version="v1",
            actor="operador",
        )
    )
    assert cache.get(CacheKey("t1", "u1", "v1", "d", "c")) is None


def test_subject_invalidated_tira_solo_al_sujeto() -> None:
    cache = CapabilitySetCache(SimulatedClock(), max_staleness_ms=10000)
    a = CacheKey("t1", "u1", "v1", "d", "c")
    b = CacheKey("t2", "u1", "v1", "d", "c")
    cache.put(a, _set())
    cache.put(b, _set(tenant="t2"))
    PolicyCacheInvalidator(cache).on_subject_invalidated(
        SubjectInvalidatedPayload(
            tenant_id="t1",
            reason=InvalidationReason.ROLE_CHANGED,
            policy_version="v1",
            user_id="u1",
        )
    )
    assert cache.get(a) is None
    assert cache.get(b) is not None
