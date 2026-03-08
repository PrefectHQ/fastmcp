"""Policy provider protocol for SecureMCP.

Policy providers are pluggable sources of governance rules. They can be
hot-swapped at runtime without service downtime.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class PolicyDecision(Enum):
    """Result of a policy evaluation."""

    ALLOW = "allow"
    DENY = "deny"
    DEFER = "defer"


@dataclass(frozen=True)
class PolicyEvaluationContext:
    """Context passed to policy providers for evaluation.

    Attributes:
        actor_id: Identifier of the agent/model making the request.
        action: The operation being performed (e.g., "call_tool", "read_resource").
        resource_id: The target component name or URI.
        metadata: Additional context (tool arguments, resource params, etc.).
        timestamp: When the request was received.
        tags: Tags on the component being accessed.
    """

    actor_id: str | None
    action: str
    resource_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tags: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class PolicyResult:
    """Result of a single policy evaluation.

    Attributes:
        decision: Whether the action is allowed, denied, or deferred.
        reason: Human-readable explanation for the decision.
        policy_id: Identifier of the policy that produced this result.
        evaluated_at: When the evaluation was performed.
        constraints: Any constraints that apply to the allowed action.
    """

    decision: PolicyDecision
    reason: str
    policy_id: str
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    constraints: list[str] = field(default_factory=list)


@runtime_checkable
class PolicyProvider(Protocol):
    """Protocol for pluggable policy providers.

    Implementations can be synchronous or asynchronous. The PolicyEngine
    handles both transparently.

    Example::

        class MyPolicy:
            async def evaluate(
                self, context: PolicyEvaluationContext
            ) -> PolicyResult:
                if "admin" in context.tags:
                    return PolicyResult(
                        decision=PolicyDecision.ALLOW,
                        reason="Admin access granted",
                        policy_id="my-policy-v1",
                    )
                return PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason="Insufficient privileges",
                    policy_id="my-policy-v1",
                )

            async def get_policy_id(self) -> str:
                return "my-policy-v1"

            async def get_policy_version(self) -> str:
                return "1.0.0"
    """

    def evaluate(
        self, context: PolicyEvaluationContext
    ) -> PolicyResult | Awaitable[PolicyResult]: ...

    def get_policy_id(self) -> str | Awaitable[str]: ...

    def get_policy_version(self) -> str | Awaitable[str]: ...


class AllowAllPolicy:
    """A policy provider that allows all requests. Useful as a default."""

    async def evaluate(self, context: PolicyEvaluationContext) -> PolicyResult:
        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason="Default allow-all policy",
            policy_id="allow-all",
        )

    async def get_policy_id(self) -> str:
        return "allow-all"

    async def get_policy_version(self) -> str:
        return "1.0.0"


class DenyAllPolicy:
    """A policy provider that denies all requests. Useful for lockdown scenarios."""

    async def evaluate(self, context: PolicyEvaluationContext) -> PolicyResult:
        return PolicyResult(
            decision=PolicyDecision.DENY,
            reason="Default deny-all policy",
            policy_id="deny-all",
        )

    async def get_policy_id(self) -> str:
        return "deny-all"

    async def get_policy_version(self) -> str:
        return "1.0.0"
