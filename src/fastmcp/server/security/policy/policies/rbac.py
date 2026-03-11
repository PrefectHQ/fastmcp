"""Role-Based Access Control (RBAC) policy.

Maps roles to permitted actions. The actor's role is extracted from
``context.metadata["role"]`` by default, or via a custom callable.

Example::

    policy = RoleBasedPolicy(
        role_mappings={
            "admin": {"*"},          # all actions
            "operator": {"call_tool", "read_resource"},
            "viewer": {"read_resource"},
        },
    )
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from fastmcp.server.security.policy.provider import (
    PolicyDecision,
    PolicyEvaluationContext,
    PolicyResult,
)

logger = logging.getLogger(__name__)


@dataclass
class RoleBasedPolicy:
    """RBAC policy: map roles to sets of permitted actions.

    Attributes:
        role_mappings: Dict mapping role names to sets of allowed actions.
            Use ``{"*"}`` to allow all actions for a role.
        role_resolver: Optional callable to extract role from context.
            Receives ``PolicyEvaluationContext``, returns role string or None.
            Defaults to reading ``context.metadata["role"]``.
        policy_id: Unique identifier for this policy instance.
        version: Version string.
        default_decision: Decision when role is not found or not mapped.
    """

    role_mappings: dict[str, set[str]] = field(default_factory=dict)
    role_resolver: (
        Callable[[PolicyEvaluationContext], str | Awaitable[str] | None] | None
    ) = None
    policy_id: str = "rbac-policy"
    version: str = "1.0.0"
    default_decision: PolicyDecision = PolicyDecision.DENY

    async def evaluate(self, context: PolicyEvaluationContext) -> PolicyResult:
        """Check if actor's role permits the requested action."""
        role: str | None = None

        if self.role_resolver is not None:
            result = self.role_resolver(context)
            if isinstance(result, Awaitable):
                role = await result
            else:
                role = result
        else:
            role = context.metadata.get("role")

        if role is None:
            return PolicyResult(
                decision=self.default_decision,
                reason="No role found for actor",
                policy_id=self.policy_id,
            )

        allowed_actions = self.role_mappings.get(role)
        if allowed_actions is None:
            return PolicyResult(
                decision=self.default_decision,
                reason=f"Role '{role}' has no mappings",
                policy_id=self.policy_id,
            )

        if "*" in allowed_actions or context.action in allowed_actions:
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                reason=f"Role '{role}' is permitted action '{context.action}'",
                policy_id=self.policy_id,
                constraints=[f"role:{role}"],
            )

        return PolicyResult(
            decision=PolicyDecision.DENY,
            reason=f"Role '{role}' is not permitted action '{context.action}'",
            policy_id=self.policy_id,
        )

    async def get_policy_id(self) -> str:
        return self.policy_id

    async def get_policy_version(self) -> str:
        return self.version
