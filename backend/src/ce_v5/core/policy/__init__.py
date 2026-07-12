"""PolicyEvaluator central: el gate fail-closed (ADR-012)."""

from ce_v5.core.policy.audit import (
    SensitiveActionAudit,
    SensitiveActionRecord,
    build_context,
)
from ce_v5.core.policy.cache import (
    CacheEntry,
    CacheKey,
    CapabilitySetCache,
    capabilities_digest,
    resources_digest,
)
from ce_v5.core.policy.cached_evaluator import (
    CachedPolicyEvaluator,
    PolicyDegradedError,
)
from ce_v5.core.policy.capabilities import (
    SENSITIVE_CAPABILITIES,
    CapabilityId,
    SensitiveCapability,
    is_sensitive,
)
from ce_v5.core.policy.decisions import Decision, ReasonCode
from ce_v5.core.policy.evaluator import (
    CapabilityDecision,
    CapabilitySet,
    PolicyEvaluator,
    ResourceContext,
)
from ce_v5.core.policy.gate import PolicyDenied, PolicyGate
from ce_v5.core.policy.inputs import (
    EvidenceSource,
    JurisdictionEvidence,
    KycStatus,
    PolicyInputs,
    ResolvedJurisdiction,
    TrustHierarchy,
    resolve_jurisdiction,
)
from ce_v5.core.policy.invalidation import PolicyCacheInvalidator
from ce_v5.core.policy.kill_switch_consumer import KillSwitchQuarantineConsumer
from ce_v5.core.policy.kill_switch_targeting import kill_switch_targets
from ce_v5.core.policy.lifecycle_gate import (
    KillSwitchSource,
    PolicyLifecycleGate,
    SubjectInputsResolver,
)
from ce_v5.core.policy.ports import (
    EntitlementRecord,
    KillSwitchRecord,
    OverrideRecord,
    PolicyRuleRecord,
    PolicyStore,
)
from ce_v5.core.policy.providers import (
    IpGeoProvider,
    KycProvider,
    StaticIpGeoProvider,
    StaticKycProvider,
    StaticVpnDetector,
    VpnDetector,
)

__all__ = [
    "SENSITIVE_CAPABILITIES",
    "CacheEntry",
    "CacheKey",
    "CachedPolicyEvaluator",
    "CapabilityDecision",
    "CapabilityId",
    "CapabilitySet",
    "CapabilitySetCache",
    "Decision",
    "EntitlementRecord",
    "EvidenceSource",
    "IpGeoProvider",
    "JurisdictionEvidence",
    "KillSwitchQuarantineConsumer",
    "KillSwitchRecord",
    "KillSwitchSource",
    "KycProvider",
    "KycStatus",
    "OverrideRecord",
    "PolicyCacheInvalidator",
    "PolicyDegradedError",
    "PolicyDenied",
    "PolicyEvaluator",
    "PolicyGate",
    "PolicyInputs",
    "PolicyLifecycleGate",
    "PolicyRuleRecord",
    "PolicyStore",
    "ReasonCode",
    "ResolvedJurisdiction",
    "ResourceContext",
    "SensitiveActionAudit",
    "SensitiveActionRecord",
    "SensitiveCapability",
    "StaticIpGeoProvider",
    "StaticKycProvider",
    "StaticVpnDetector",
    "SubjectInputsResolver",
    "TrustHierarchy",
    "VpnDetector",
    "build_context",
    "capabilities_digest",
    "is_sensitive",
    "kill_switch_targets",
    "resolve_jurisdiction",
    "resources_digest",
]
