"""Built-in policy types for SecureMCP.

Provides ready-to-use PolicyProvider implementations for common access
control patterns: allowlist/denylist, RBAC, ABAC, time-based,
resource-scoped, and rate limiting.
"""

from fastmcp.server.security.policy.policies.abac import AttributeBasedPolicy
from fastmcp.server.security.policy.policies.allowlist import (
    AllowlistPolicy,
    DenylistPolicy,
)
from fastmcp.server.security.policy.policies.rate_limit import RateLimitPolicy
from fastmcp.server.security.policy.policies.rbac import RoleBasedPolicy
from fastmcp.server.security.policy.policies.resource_scoped import (
    ResourceScopedPolicy,
)
from fastmcp.server.security.policy.policies.temporal import TimeBasedPolicy

__all__ = [
    "AllowlistPolicy",
    "AttributeBasedPolicy",
    "DenylistPolicy",
    "RateLimitPolicy",
    "ResourceScopedPolicy",
    "RoleBasedPolicy",
    "TimeBasedPolicy",
]
