"""Tests for policy composition operators (AllOf, AnyOf, Not, FirstMatch)."""

import asyncio

from fastmcp.server.security.policy.composition import AllOf, AnyOf, FirstMatch, Not
from fastmcp.server.security.policy.provider import (
    AllowAllPolicy,
    DenyAllPolicy,
    PolicyDecision,
    PolicyEvaluationContext,
    PolicyResult,
)


def _ctx(
    action: str = "call_tool", resource_id: str = "tool:test"
) -> PolicyEvaluationContext:
    return PolicyEvaluationContext(
        actor_id="agent-1",
        action=action,
        resource_id=resource_id,
    )


class _DeferPolicy:
    """Policy that always defers."""

    async def evaluate(self, context: PolicyEvaluationContext) -> PolicyResult:
        return PolicyResult(
            decision=PolicyDecision.DEFER,
            reason="Deferred",
            policy_id="defer-policy",
        )

    async def get_policy_id(self) -> str:
        return "defer-policy"

    async def get_policy_version(self) -> str:
        return "1.0.0"


class _ConstraintPolicy:
    """Policy that allows with constraints."""

    def __init__(self, constraints: list[str]) -> None:
        self._constraints = constraints

    async def evaluate(self, context: PolicyEvaluationContext) -> PolicyResult:
        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason="Allowed with constraints",
            policy_id="constraint-policy",
            constraints=self._constraints,
        )

    async def get_policy_id(self) -> str:
        return "constraint-policy"

    async def get_policy_version(self) -> str:
        return "1.0.0"


# ── AllOf Tests ────────────────────────────────────────────────


class TestAllOf:
    def test_all_allow(self):
        policy = AllOf(AllowAllPolicy(), AllowAllPolicy())
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.ALLOW

    def test_one_deny(self):
        policy = AllOf(AllowAllPolicy(), DenyAllPolicy(), AllowAllPolicy())
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.DENY

    def test_merges_constraints(self):
        policy = AllOf(
            _ConstraintPolicy(["c1"]),
            _ConstraintPolicy(["c2", "c3"]),
        )
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.ALLOW
        assert result.constraints == ["c1", "c2", "c3"]

    def test_defer_treated_as_pass(self):
        policy = AllOf(AllowAllPolicy(), _DeferPolicy())
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.ALLOW

    def test_empty_policies_allows(self):
        policy = AllOf()
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.ALLOW

    def test_policy_id(self):
        policy = AllOf(policy_id="my-allof")
        result = asyncio.get_event_loop().run_until_complete(policy.get_policy_id())
        assert result == "my-allof"


# ── AnyOf Tests ────────────────────────────────────────────────


class TestAnyOf:
    def test_one_allow(self):
        policy = AnyOf(DenyAllPolicy(), AllowAllPolicy(), DenyAllPolicy())
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.ALLOW

    def test_all_deny(self):
        policy = AnyOf(DenyAllPolicy(), DenyAllPolicy())
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.DENY

    def test_require_minimum(self):
        policy = AnyOf(
            AllowAllPolicy(),
            DenyAllPolicy(),
            AllowAllPolicy(),
            require_minimum=2,
        )
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.ALLOW

    def test_require_minimum_not_met(self):
        policy = AnyOf(
            AllowAllPolicy(),
            DenyAllPolicy(),
            DenyAllPolicy(),
            require_minimum=2,
        )
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.DENY

    def test_defer_does_not_count_as_allow(self):
        policy = AnyOf(_DeferPolicy(), _DeferPolicy())
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.DENY

    def test_merges_constraints_from_allows(self):
        policy = AnyOf(
            _ConstraintPolicy(["c1"]),
            DenyAllPolicy(),
            _ConstraintPolicy(["c2"]),
        )
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.ALLOW
        assert result.constraints == ["c1", "c2"]


# ── Not Tests ──────────────────────────────────────────────────


class TestNot:
    def test_inverts_allow_to_deny(self):
        policy = Not(AllowAllPolicy())
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.DENY

    def test_inverts_deny_to_allow(self):
        policy = Not(DenyAllPolicy())
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.ALLOW

    def test_defer_unchanged(self):
        policy = Not(_DeferPolicy())
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.DEFER

    def test_constraints_preserved_on_allow(self):
        inner = _ConstraintPolicy(["c1"])
        policy = Not(Not(inner))
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.ALLOW


# ── FirstMatch Tests ───────────────────────────────────────────


class TestFirstMatch:
    def test_first_non_defer_wins(self):
        policy = FirstMatch(_DeferPolicy(), DenyAllPolicy(), AllowAllPolicy())
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.DENY

    def test_first_allow_wins(self):
        policy = FirstMatch(_DeferPolicy(), AllowAllPolicy(), DenyAllPolicy())
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.ALLOW

    def test_all_defer_uses_default(self):
        policy = FirstMatch(
            _DeferPolicy(),
            _DeferPolicy(),
            default_decision=PolicyDecision.DENY,
        )
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.DENY

    def test_all_defer_allow_default(self):
        policy = FirstMatch(
            _DeferPolicy(),
            default_decision=PolicyDecision.ALLOW,
        )
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.ALLOW

    def test_empty_uses_default(self):
        policy = FirstMatch(default_decision=PolicyDecision.DENY)
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.DENY


# ── Nesting Tests ──────────────────────────────────────────────


class TestNesting:
    def test_allof_inside_anyof(self):
        """AnyOf(AllOf(allow, allow), deny) → ALLOW"""
        inner = AllOf(AllowAllPolicy(), AllowAllPolicy())
        outer = AnyOf(inner, DenyAllPolicy())
        result = asyncio.get_event_loop().run_until_complete(outer.evaluate(_ctx()))
        assert result.decision == PolicyDecision.ALLOW

    def test_anyof_inside_allof(self):
        """AllOf(AnyOf(deny, allow), allow) → ALLOW"""
        inner = AnyOf(DenyAllPolicy(), AllowAllPolicy())
        outer = AllOf(inner, AllowAllPolicy())
        result = asyncio.get_event_loop().run_until_complete(outer.evaluate(_ctx()))
        assert result.decision == PolicyDecision.ALLOW

    def test_not_inside_allof(self):
        """AllOf(Not(deny), allow) → ALLOW"""
        policy = AllOf(Not(DenyAllPolicy()), AllowAllPolicy())
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.ALLOW

    def test_deep_nesting(self):
        """FirstMatch(defer, AllOf(Not(deny), AnyOf(allow, deny)))"""
        deep = FirstMatch(
            _DeferPolicy(),
            AllOf(Not(DenyAllPolicy()), AnyOf(AllowAllPolicy(), DenyAllPolicy())),
        )
        result = asyncio.get_event_loop().run_until_complete(deep.evaluate(_ctx()))
        assert result.decision == PolicyDecision.ALLOW
