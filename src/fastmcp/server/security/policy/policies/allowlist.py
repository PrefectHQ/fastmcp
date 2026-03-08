"""Tool allowlist and denylist policies.

Simple, explicit access control: specify which tools are allowed or denied
by name, with optional glob-pattern matching.

Examples::

    # Only allow specific tools
    policy = AllowlistPolicy(allowed={"weather-lookup", "translate"})

    # Block specific tools, allow everything else
    policy = DenylistPolicy(denied={"dangerous-tool", "admin-*"})

    # Combine both: allowlist takes precedence
    from fastmcp.server.security.policy.composition import AllOf
    policy = AllOf(
        DenylistPolicy(denied={"debug-*"}),
        AllowlistPolicy(allowed={"debug-logs"}),  # override for one
    )
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field

from fastmcp.server.security.policy.provider import (
    PolicyDecision,
    PolicyEvaluationContext,
    PolicyResult,
)

logger = logging.getLogger(__name__)


def _matches_any(resource_id: str, patterns: set[str]) -> str | None:
    """Check if resource_id matches any pattern in the set.

    Supports both exact matches and glob patterns (``*``, ``?``, ``[seq]``).
    Returns the first matching pattern, or None.
    """
    for pattern in patterns:
        if pattern == resource_id or fnmatch.fnmatchcase(resource_id, pattern):
            return pattern
    return None


@dataclass
class AllowlistPolicy:
    """Only allow access to explicitly listed tools/resources.

    Any resource not in the allowlist is denied.  Supports glob patterns
    for prefix or suffix matching (e.g., ``"weather-*"``).

    Attributes:
        allowed: Set of resource IDs or glob patterns to allow.
        policy_id: Unique identifier for this policy instance.
        version: Version string.
    """

    allowed: set[str] = field(default_factory=set)
    policy_id: str = "allowlist-policy"
    version: str = "1.0.0"

    async def evaluate(self, context: PolicyEvaluationContext) -> PolicyResult:
        """Allow only if resource_id matches an allowlist entry."""
        resource_id = context.resource_id

        match = _matches_any(resource_id, self.allowed)
        if match is not None:
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                reason=f"Resource '{resource_id}' matches allowlist pattern '{match}'",
                policy_id=self.policy_id,
            )

        return PolicyResult(
            decision=PolicyDecision.DENY,
            reason=f"Resource '{resource_id}' is not in the allowlist",
            policy_id=self.policy_id,
        )

    def add(self, pattern: str) -> None:
        """Add a resource ID or pattern to the allowlist."""
        self.allowed.add(pattern)

    def remove(self, pattern: str) -> bool:
        """Remove a pattern from the allowlist.  Returns True if it was present."""
        try:
            self.allowed.discard(pattern)
            return True
        except KeyError:
            return False

    def is_allowed(self, resource_id: str) -> bool:
        """Check if a resource would be allowed (sync convenience)."""
        return _matches_any(resource_id, self.allowed) is not None

    async def get_policy_id(self) -> str:
        return self.policy_id

    async def get_policy_version(self) -> str:
        return self.version


@dataclass
class DenylistPolicy:
    """Block access to explicitly listed tools/resources.

    Any resource in the denylist is denied; everything else is allowed.
    Supports glob patterns (e.g., ``"admin-*"``).

    Attributes:
        denied: Set of resource IDs or glob patterns to deny.
        policy_id: Unique identifier for this policy instance.
        version: Version string.
    """

    denied: set[str] = field(default_factory=set)
    policy_id: str = "denylist-policy"
    version: str = "1.0.0"

    async def evaluate(self, context: PolicyEvaluationContext) -> PolicyResult:
        """Deny if resource_id matches a denylist entry."""
        resource_id = context.resource_id

        match = _matches_any(resource_id, self.denied)
        if match is not None:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"Resource '{resource_id}' matches denylist pattern '{match}'",
                policy_id=self.policy_id,
            )

        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason=f"Resource '{resource_id}' is not in the denylist",
            policy_id=self.policy_id,
        )

    def add(self, pattern: str) -> None:
        """Add a resource ID or pattern to the denylist."""
        self.denied.add(pattern)

    def remove(self, pattern: str) -> bool:
        """Remove a pattern from the denylist.  Returns True if it was present."""
        try:
            self.denied.discard(pattern)
            return True
        except KeyError:
            return False

    def is_denied(self, resource_id: str) -> bool:
        """Check if a resource would be denied (sync convenience)."""
        return _matches_any(resource_id, self.denied) is not None

    async def get_policy_id(self) -> str:
        return self.policy_id

    async def get_policy_version(self) -> str:
        return self.version
