"""Policy kernel for SecureMCP.

Pluggable, hot-swappable policy engines with formal verification support.
Includes composable policy operators, built-in policy types, a fluent builder
API, and policy versioning with rollback.
"""

from fastmcp.server.security.policy.audit import AuditEntry, PolicyAuditLog
from fastmcp.server.security.policy.builders import PolicyBuilder
from fastmcp.server.security.policy.composition import AllOf, AnyOf, FirstMatch, Not
from fastmcp.server.security.policy.declarative import (
    dump_policy_schema,
    load_policy,
)
from fastmcp.server.security.policy.engine import (
    PolicyDecision,
    PolicyEngine,
    PolicyEvaluationContext,
    PolicyResult,
)
from fastmcp.server.security.policy.invariants import (
    Invariant,
    InvariantVerificationResult,
    InvariantVerifier,
)
from fastmcp.server.security.policy.policies import (
    AllowlistPolicy,
    AttributeBasedPolicy,
    DenylistPolicy,
    RateLimitPolicy,
    ResourceScopedPolicy,
    RoleBasedPolicy,
    TimeBasedPolicy,
)
from fastmcp.server.security.policy.provider import PolicyProvider
from fastmcp.server.security.policy.simulation import (
    Scenario,
    ScenarioResult,
    SimulationReport,
    simulate,
)
from fastmcp.server.security.policy.versioning import (
    PolicyVersion,
    PolicyVersionHistory,
    PolicyVersionManager,
)

__all__ = [
    "AllOf",
    "AllowlistPolicy",
    "AnyOf",
    "AttributeBasedPolicy",
    "AuditEntry",
    "DenylistPolicy",
    "FirstMatch",
    "Invariant",
    "InvariantVerificationResult",
    "InvariantVerifier",
    "Not",
    "PolicyAuditLog",
    "PolicyBuilder",
    "PolicyDecision",
    "PolicyEngine",
    "PolicyEvaluationContext",
    "PolicyProvider",
    "PolicyResult",
    "PolicyVersion",
    "PolicyVersionHistory",
    "PolicyVersionManager",
    "RateLimitPolicy",
    "ResourceScopedPolicy",
    "RoleBasedPolicy",
    "Scenario",
    "ScenarioResult",
    "SimulationReport",
    "TimeBasedPolicy",
    "dump_policy_schema",
    "load_policy",
    "simulate",
]
