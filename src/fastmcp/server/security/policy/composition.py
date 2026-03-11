"""Composable policy operators for SecureMCP.

Combine multiple PolicyProviders with boolean logic (AND/OR/NOT)
or priority-based evaluation (FirstMatch).

All composition operators implement the PolicyProvider protocol and can be
nested arbitrarily::

    policy = AllOf(
        RoleBasedPolicy(role_mappings={"admin": {"*"}}),
        AnyOf(
            TimeBasedPolicy(allowed_start_time=time(9, 0), allowed_end_time=time(17, 0)),
            AttributeBasedPolicy(rules={"emergency": lambda ctx: ctx.metadata.get("emergency")}),
        ),
    )
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from dataclasses import dataclass, field

from fastmcp.server.security.policy.provider import (
    PolicyDecision,
    PolicyEvaluationContext,
    PolicyProvider,
    PolicyResult,
)

logger = logging.getLogger(__name__)


async def _resolve(value: object) -> object:
    """Await if awaitable, otherwise return directly."""
    if isinstance(value, Awaitable):
        return await value
    return value


@dataclass
class AllOf:
    """All child policies must ALLOW for the composite to ALLOW.

    Semantics:
    - If ANY child returns DENY → DENY (short-circuit optional).
    - If ALL children return ALLOW → ALLOW (constraints merged).
    - DEFER is treated as ALLOW for AND purposes.

    Example::

        policy = AllOf(admin_role_policy, business_hours_policy)
    """

    policies: list[PolicyProvider] = field(default_factory=list)
    policy_id: str = "allof-composition"
    version: str = "1.0.0"
    short_circuit: bool = True

    def __init__(
        self,
        *policies: PolicyProvider,
        policy_id: str = "allof-composition",
        version: str = "1.0.0",
        short_circuit: bool = True,
    ) -> None:
        self.policies = list(policies)
        self.policy_id = policy_id
        self.version = version
        self.short_circuit = short_circuit

    async def evaluate(self, context: PolicyEvaluationContext) -> PolicyResult:
        """Evaluate all children with AND logic."""
        merged_constraints: list[str] = []
        reasons: list[str] = []

        for policy in self.policies:
            result = await _resolve(policy.evaluate(context))
            assert isinstance(result, PolicyResult)

            if result.decision == PolicyDecision.DENY:
                pid = await _resolve(policy.get_policy_id())
                return PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason=f"Denied by {pid}: {result.reason}",
                    policy_id=self.policy_id,
                    constraints=result.constraints,
                )

            if result.decision == PolicyDecision.ALLOW:
                merged_constraints.extend(result.constraints)
                reasons.append(result.reason)

        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason=f"All {len(self.policies)} policies allowed",
            policy_id=self.policy_id,
            constraints=merged_constraints,
        )

    async def get_policy_id(self) -> str:
        return self.policy_id

    async def get_policy_version(self) -> str:
        return self.version


@dataclass
class AnyOf:
    """At least N child policies must ALLOW for the composite to ALLOW.

    Semantics:
    - If at least ``require_minimum`` children ALLOW → ALLOW.
    - If not enough ALLOW → DENY.
    - DEFER does not count as ALLOW.

    Example::

        policy = AnyOf(admin_policy, manager_policy, require_minimum=1)
    """

    policies: list[PolicyProvider] = field(default_factory=list)
    policy_id: str = "anyof-composition"
    version: str = "1.0.0"
    require_minimum: int = 1

    def __init__(
        self,
        *policies: PolicyProvider,
        policy_id: str = "anyof-composition",
        version: str = "1.0.0",
        require_minimum: int = 1,
    ) -> None:
        self.policies = list(policies)
        self.policy_id = policy_id
        self.version = version
        self.require_minimum = require_minimum

    async def evaluate(self, context: PolicyEvaluationContext) -> PolicyResult:
        """Evaluate all children with OR logic."""
        allow_count = 0
        merged_constraints: list[str] = []
        deny_reasons: list[str] = []

        for policy in self.policies:
            result = await _resolve(policy.evaluate(context))
            assert isinstance(result, PolicyResult)

            if result.decision == PolicyDecision.ALLOW:
                allow_count += 1
                merged_constraints.extend(result.constraints)

            if result.decision == PolicyDecision.DENY:
                deny_reasons.append(result.reason)

        if allow_count >= self.require_minimum:
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                reason=f"{allow_count}/{len(self.policies)} policies allowed (minimum: {self.require_minimum})",
                policy_id=self.policy_id,
                constraints=merged_constraints,
            )

        return PolicyResult(
            decision=PolicyDecision.DENY,
            reason=f"Only {allow_count}/{self.require_minimum} required policies allowed",
            policy_id=self.policy_id,
        )

    async def get_policy_id(self) -> str:
        return self.policy_id

    async def get_policy_version(self) -> str:
        return self.version


@dataclass
class Not:
    """Inverts the decision of a child policy.

    Semantics:
    - ALLOW → DENY
    - DENY → ALLOW
    - DEFER → DEFER (unchanged)

    Example::

        policy = Not(deprecated_resource_policy)
    """

    policy: PolicyProvider | None = None
    policy_id: str = "not-composition"
    version: str = "1.0.0"

    def __init__(
        self,
        policy: PolicyProvider,
        *,
        policy_id: str = "not-composition",
        version: str = "1.0.0",
    ) -> None:
        self.policy = policy
        self.policy_id = policy_id
        self.version = version

    async def evaluate(self, context: PolicyEvaluationContext) -> PolicyResult:
        """Invert child decision."""
        assert self.policy is not None
        result = await _resolve(self.policy.evaluate(context))
        assert isinstance(result, PolicyResult)

        if result.decision == PolicyDecision.DEFER:
            return PolicyResult(
                decision=PolicyDecision.DEFER,
                reason=f"Inverted (deferred): {result.reason}",
                policy_id=self.policy_id,
                constraints=result.constraints,
            )

        inverted = (
            PolicyDecision.DENY
            if result.decision == PolicyDecision.ALLOW
            else PolicyDecision.ALLOW
        )

        return PolicyResult(
            decision=inverted,
            reason=f"Inverted: {result.reason}",
            policy_id=self.policy_id,
            constraints=result.constraints if inverted == PolicyDecision.ALLOW else [],
        )

    async def get_policy_id(self) -> str:
        return self.policy_id

    async def get_policy_version(self) -> str:
        return self.version


@dataclass
class FirstMatch:
    """Returns the first non-DEFER decision from child policies.

    Policies are evaluated in order. The first to return ALLOW or DENY wins.
    If all policies DEFER, ``default_decision`` is used.

    Example::

        policy = FirstMatch(
            specific_admin_rule,
            general_role_rule,
            fallback_deny_rule,
        )
    """

    policies: list[PolicyProvider] = field(default_factory=list)
    policy_id: str = "firstmatch-composition"
    version: str = "1.0.0"
    default_decision: PolicyDecision = PolicyDecision.DENY

    def __init__(
        self,
        *policies: PolicyProvider,
        policy_id: str = "firstmatch-composition",
        version: str = "1.0.0",
        default_decision: PolicyDecision = PolicyDecision.DENY,
    ) -> None:
        self.policies = list(policies)
        self.policy_id = policy_id
        self.version = version
        self.default_decision = default_decision

    async def evaluate(self, context: PolicyEvaluationContext) -> PolicyResult:
        """Evaluate children in order, return first non-DEFER."""
        for policy in self.policies:
            result = await _resolve(policy.evaluate(context))
            assert isinstance(result, PolicyResult)

            if result.decision != PolicyDecision.DEFER:
                return PolicyResult(
                    decision=result.decision,
                    reason=result.reason,
                    policy_id=self.policy_id,
                    constraints=result.constraints,
                )

        return PolicyResult(
            decision=self.default_decision,
            reason=f"All {len(self.policies)} policies deferred, using default: {self.default_decision.value}",
            policy_id=self.policy_id,
        )

    async def get_policy_id(self) -> str:
        return self.policy_id

    async def get_policy_version(self) -> str:
        return self.version
