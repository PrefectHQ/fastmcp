"""Fluent builder API for composing policies.

Provides a chainable interface for building complex composite policies
without manually instantiating composition operators.

Example::

    from fastmcp.server.security.policy.builders import PolicyBuilder

    policy = (PolicyBuilder()
        .allow_roles("admin", "operator")
        .restrict_hours(start_hour=9, end_hour=17)
        .rate_limit(max_requests=100, window_seconds=3600)
        .require_all()
        .with_id("production-policy")
        .build())
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import time
from typing import Literal

from fastmcp.server.security.policy.composition import AllOf, AnyOf, FirstMatch, Not
from fastmcp.server.security.policy.policies.abac import AttributeBasedPolicy
from fastmcp.server.security.policy.policies.rate_limit import RateLimitPolicy
from fastmcp.server.security.policy.policies.rbac import RoleBasedPolicy
from fastmcp.server.security.policy.policies.temporal import TimeBasedPolicy
from fastmcp.server.security.policy.provider import (
    PolicyDecision,
    PolicyEvaluationContext,
    PolicyProvider,
    PolicyResult,
)

logger = logging.getLogger(__name__)


class _ActionFilterPolicy:
    """Internal policy that filters by action name."""

    def __init__(
        self,
        *,
        allowed_actions: set[str] | None = None,
        denied_actions: set[str] | None = None,
        policy_id: str = "action-filter",
    ) -> None:
        self._allowed = allowed_actions
        self._denied = denied_actions
        self._policy_id = policy_id

    async def evaluate(self, context: PolicyEvaluationContext) -> PolicyResult:
        if self._denied and context.action in self._denied:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"Action '{context.action}' is explicitly denied",
                policy_id=self._policy_id,
            )
        if self._allowed is not None:
            if context.action in self._allowed:
                return PolicyResult(
                    decision=PolicyDecision.ALLOW,
                    reason=f"Action '{context.action}' is explicitly allowed",
                    policy_id=self._policy_id,
                )
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"Action '{context.action}' not in allowed set",
                policy_id=self._policy_id,
            )
        return PolicyResult(
            decision=PolicyDecision.DEFER,
            reason="No action filter matched",
            policy_id=self._policy_id,
        )

    async def get_policy_id(self) -> str:
        return self._policy_id

    async def get_policy_version(self) -> str:
        return "1.0.0"


class _TagFilterPolicy:
    """Internal policy that filters by tags."""

    def __init__(
        self,
        *,
        allowed_tags: set[str] | None = None,
        denied_tags: set[str] | None = None,
        policy_id: str = "tag-filter",
    ) -> None:
        self._allowed = allowed_tags
        self._denied = denied_tags
        self._policy_id = policy_id

    async def evaluate(self, context: PolicyEvaluationContext) -> PolicyResult:
        if self._denied and self._denied & context.tags:
            matched = self._denied & context.tags
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"Denied tags present: {matched}",
                policy_id=self._policy_id,
            )
        if self._allowed is not None:
            if self._allowed & context.tags:
                return PolicyResult(
                    decision=PolicyDecision.ALLOW,
                    reason="Required tags present",
                    policy_id=self._policy_id,
                )
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"No allowed tags found (need one of: {self._allowed})",
                policy_id=self._policy_id,
            )
        return PolicyResult(
            decision=PolicyDecision.DEFER,
            reason="No tag filter matched",
            policy_id=self._policy_id,
        )

    async def get_policy_id(self) -> str:
        return self._policy_id

    async def get_policy_version(self) -> str:
        return "1.0.0"


class PolicyBuilder:
    """Fluent API for building composite policies.

    Methods are chainable. Call ``.build()`` at the end to produce a
    ``PolicyProvider``.

    Example::

        policy = (PolicyBuilder()
            .allow_roles("admin")
            .deny_actions("delete_system")
            .restrict_hours(start_hour=9, end_hour=17)
            .require_all()
            .build())
    """

    def __init__(self) -> None:
        self._policies: list[PolicyProvider] = []
        self._composition: Literal["all", "any", "first_match"] = "all"
        self._negate: bool = False
        self._policy_id: str = "builder-policy"
        self._version: str = "1.0.0"
        self._require_minimum: int = 1
        self._default_decision: PolicyDecision = PolicyDecision.DENY

    def allow_roles(self, *roles: str) -> PolicyBuilder:
        """Add RBAC rule: allow these roles for all actions."""
        self._policies.append(
            RoleBasedPolicy(
                role_mappings={role: {"*"} for role in roles},
                policy_id=f"rbac-allow-{'-'.join(roles)}",
            )
        )
        return self

    def deny_roles(self, *roles: str) -> PolicyBuilder:
        """Add RBAC rule: deny these roles (via Not wrapper)."""
        rbac = RoleBasedPolicy(
            role_mappings={role: {"*"} for role in roles},
            policy_id=f"rbac-deny-{'-'.join(roles)}",
        )
        self._policies.append(Not(rbac, policy_id=f"not-rbac-{'-'.join(roles)}"))
        return self

    def allow_actions(self, *actions: str) -> PolicyBuilder:
        """Add action allow-list filter."""
        self._policies.append(
            _ActionFilterPolicy(
                allowed_actions=set(actions),
                policy_id=f"action-allow-{'-'.join(actions)}",
            )
        )
        return self

    def deny_actions(self, *actions: str) -> PolicyBuilder:
        """Add action deny-list filter."""
        self._policies.append(
            _ActionFilterPolicy(
                denied_actions=set(actions),
                policy_id=f"action-deny-{'-'.join(actions)}",
            )
        )
        return self

    def allow_tags(self, *tags: str) -> PolicyBuilder:
        """Add tag-based allow filter."""
        self._policies.append(
            _TagFilterPolicy(
                allowed_tags=set(tags),
                policy_id=f"tag-allow-{'-'.join(tags)}",
            )
        )
        return self

    def deny_tags(self, *tags: str) -> PolicyBuilder:
        """Add tag-based deny filter."""
        self._policies.append(
            _TagFilterPolicy(
                denied_tags=set(tags),
                policy_id=f"tag-deny-{'-'.join(tags)}",
            )
        )
        return self

    def require_attributes(
        self,
        require_all: bool = True,
        **conditions: Callable[[PolicyEvaluationContext], bool | Awaitable[bool]],
    ) -> PolicyBuilder:
        """Add ABAC conditions."""
        self._policies.append(
            AttributeBasedPolicy(
                rules=dict(conditions),
                require_all=require_all,
                policy_id="abac-builder",
            )
        )
        return self

    def restrict_hours(
        self,
        start_hour: int = 9,
        end_hour: int = 17,
        allowed_days: frozenset[int] | None = None,
        utc_offset_hours: int = 0,
    ) -> PolicyBuilder:
        """Add time-based restriction."""
        self._policies.append(
            TimeBasedPolicy(
                allowed_days=allowed_days or frozenset(range(7)),
                allowed_start_time=time(start_hour, 0),
                allowed_end_time=time(end_hour, 0),
                utc_offset_hours=utc_offset_hours,
                policy_id="time-builder",
            )
        )
        return self

    def rate_limit(
        self,
        max_requests: int = 100,
        window_seconds: int = 3600,
    ) -> PolicyBuilder:
        """Add rate limiting."""
        self._policies.append(
            RateLimitPolicy(
                max_requests=max_requests,
                window_seconds=window_seconds,
                policy_id="rate-limit-builder",
            )
        )
        return self

    def add_policy(self, policy: PolicyProvider) -> PolicyBuilder:
        """Add a custom policy provider."""
        self._policies.append(policy)
        return self

    def require_all(self) -> PolicyBuilder:
        """Combine policies with AND logic (all must allow)."""
        self._composition = "all"
        return self

    def require_any(self, minimum: int = 1) -> PolicyBuilder:
        """Combine policies with OR logic (at least N must allow)."""
        self._composition = "any"
        self._require_minimum = minimum
        return self

    def first_match(
        self, default: PolicyDecision = PolicyDecision.DENY
    ) -> PolicyBuilder:
        """Use first-match evaluation (first non-DEFER wins)."""
        self._composition = "first_match"
        self._default_decision = default
        return self

    def negate(self) -> PolicyBuilder:
        """Invert the final composite decision."""
        self._negate = True
        return self

    def with_id(self, policy_id: str) -> PolicyBuilder:
        """Set the policy ID."""
        self._policy_id = policy_id
        return self

    def with_version(self, version: str) -> PolicyBuilder:
        """Set the version string."""
        self._version = version
        return self

    def build(self) -> PolicyProvider:
        """Build the final composite policy.

        Returns:
            A PolicyProvider combining all added rules.

        Raises:
            ValueError: If no policies have been added.
        """
        if not self._policies:
            raise ValueError("No policies added to builder")

        # Single policy: no composition wrapper needed
        if len(self._policies) == 1 and not self._negate:
            return self._policies[0]

        # Build composition
        composite: PolicyProvider
        if self._composition == "all":
            composite = AllOf(
                *self._policies,
                policy_id=self._policy_id,
                version=self._version,
            )
        elif self._composition == "any":
            composite = AnyOf(
                *self._policies,
                policy_id=self._policy_id,
                version=self._version,
                require_minimum=self._require_minimum,
            )
        else:
            composite = FirstMatch(
                *self._policies,
                policy_id=self._policy_id,
                version=self._version,
                default_decision=self._default_decision,
            )

        if self._negate:
            composite = Not(
                composite,
                policy_id=f"not-{self._policy_id}",
                version=self._version,
            )

        return composite
