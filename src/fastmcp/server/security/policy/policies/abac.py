"""Attribute-Based Access Control (ABAC) policy.

Evaluates predicate functions against the evaluation context to determine
access. Each rule is a named callable that returns True (allow) or False (deny).

Example::

    policy = AttributeBasedPolicy(
        rules={
            "is_internal": lambda ctx: ctx.metadata.get("network") == "internal",
            "has_clearance": lambda ctx: ctx.metadata.get("clearance_level", 0) >= 3,
        },
        require_all=True,  # all rules must pass
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
class AttributeBasedPolicy:
    """ABAC policy: evaluate predicate conditions on context.

    Attributes:
        rules: Dict mapping rule names to predicate callables.
            Each callable receives a ``PolicyEvaluationContext`` and returns
            bool (sync or async).
        require_all: If True, all rules must pass (AND). If False, any rule
            passing is sufficient (OR).
        policy_id: Unique identifier for this policy instance.
        version: Version string.
    """

    rules: dict[str, Callable[[PolicyEvaluationContext], bool | Awaitable[bool]]] = (
        field(default_factory=dict)
    )
    require_all: bool = True
    policy_id: str = "abac-policy"
    version: str = "1.0.0"

    async def evaluate(self, context: PolicyEvaluationContext) -> PolicyResult:
        """Evaluate attribute rules against context."""
        if not self.rules:
            return PolicyResult(
                decision=PolicyDecision.DEFER,
                reason="No ABAC rules configured",
                policy_id=self.policy_id,
            )

        passed: list[str] = []
        failed: list[str] = []

        for name, predicate in self.rules.items():
            try:
                result = predicate(context)
                if isinstance(result, Awaitable):
                    result = await result
                if result:
                    passed.append(name)
                else:
                    failed.append(name)
            except Exception as exc:
                logger.warning(
                    "ABAC rule '%s' raised %s: %s", name, type(exc).__name__, exc
                )
                failed.append(name)

        if self.require_all:
            if not failed:
                return PolicyResult(
                    decision=PolicyDecision.ALLOW,
                    reason=f"All {len(passed)} ABAC rules passed",
                    policy_id=self.policy_id,
                    constraints=[f"abac:{name}" for name in passed],
                )
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"ABAC rules failed: {', '.join(failed)}",
                policy_id=self.policy_id,
            )
        else:
            if passed:
                return PolicyResult(
                    decision=PolicyDecision.ALLOW,
                    reason=f"{len(passed)}/{len(self.rules)} ABAC rules passed: {', '.join(passed)}",
                    policy_id=self.policy_id,
                    constraints=[f"abac:{name}" for name in passed],
                )
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"No ABAC rules passed (checked: {', '.join(failed)})",
                policy_id=self.policy_id,
            )

    async def get_policy_id(self) -> str:
        return self.policy_id

    async def get_policy_version(self) -> str:
        return self.version
