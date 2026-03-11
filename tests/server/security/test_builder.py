"""Tests for the fluent PolicyBuilder API."""

import asyncio
from datetime import datetime, timezone

import pytest

from fastmcp.server.security.policy.builders import PolicyBuilder
from fastmcp.server.security.policy.composition import AllOf, AnyOf, FirstMatch, Not
from fastmcp.server.security.policy.provider import (
    AllowAllPolicy,
    DenyAllPolicy,
    PolicyDecision,
    PolicyEvaluationContext,
)


def _ctx(
    action: str = "call_tool",
    resource_id: str = "tool:test",
    metadata: dict | None = None,
    tags: frozenset[str] | None = None,
) -> PolicyEvaluationContext:
    return PolicyEvaluationContext(
        actor_id="agent-1",
        action=action,
        resource_id=resource_id,
        metadata=metadata or {},
        tags=tags or frozenset(),
    )


def _run(awaitable):
    return asyncio.run(awaitable)


class TestPolicyBuilderBasic:
    def test_empty_builder_raises(self):
        with pytest.raises(ValueError, match="No policies"):
            PolicyBuilder().build()

    def test_single_policy_no_wrapper(self):
        policy = PolicyBuilder().add_policy(AllowAllPolicy()).build()
        # Single policy should not be wrapped in composition
        assert isinstance(policy, AllowAllPolicy)

    def test_require_all_creates_allof(self):
        policy = (
            PolicyBuilder()
            .add_policy(AllowAllPolicy())
            .add_policy(AllowAllPolicy())
            .require_all()
            .build()
        )
        assert isinstance(policy, AllOf)

    def test_require_any_creates_anyof(self):
        policy = (
            PolicyBuilder()
            .add_policy(AllowAllPolicy())
            .add_policy(DenyAllPolicy())
            .require_any()
            .build()
        )
        assert isinstance(policy, AnyOf)

    def test_first_match_creates_firstmatch(self):
        policy = (
            PolicyBuilder()
            .add_policy(AllowAllPolicy())
            .add_policy(DenyAllPolicy())
            .first_match()
            .build()
        )
        assert isinstance(policy, FirstMatch)

    def test_negate_wraps_in_not(self):
        policy = (
            PolicyBuilder()
            .add_policy(AllowAllPolicy())
            .add_policy(AllowAllPolicy())
            .require_all()
            .negate()
            .build()
        )
        assert isinstance(policy, Not)


class TestPolicyBuilderRBAC:
    def test_allow_roles(self):
        policy = PolicyBuilder().allow_roles("admin").build()
        ctx = _ctx(metadata={"role": "admin"})
        result = _run(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.ALLOW

    def test_allow_roles_denied(self):
        policy = PolicyBuilder().allow_roles("admin").build()
        ctx = _ctx(metadata={"role": "viewer"})
        result = _run(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.DENY

    def test_deny_roles(self):
        # deny_roles wraps RBAC in Not, so matching role → DENY
        policy = PolicyBuilder().deny_roles("blocked").build()
        ctx = _ctx(metadata={"role": "blocked"})
        result = _run(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.DENY


class TestPolicyBuilderActions:
    def test_allow_actions(self):
        policy = PolicyBuilder().allow_actions("read_resource", "call_tool").build()
        ctx = _ctx(action="call_tool")
        result = _run(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.ALLOW

    def test_deny_actions(self):
        policy = PolicyBuilder().deny_actions("delete_system").build()
        ctx = _ctx(action="delete_system")
        result = _run(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.DENY

    def test_deny_actions_other_allowed(self):
        policy = PolicyBuilder().deny_actions("delete_system").build()
        ctx = _ctx(action="call_tool")
        result = _run(policy.evaluate(ctx))
        # Action not in denied set → DEFER (not explicit allow)
        assert result.decision == PolicyDecision.DEFER


class TestPolicyBuilderTags:
    def test_allow_tags(self):
        policy = PolicyBuilder().allow_tags("safe").build()
        ctx = _ctx(tags=frozenset({"safe", "tested"}))
        result = _run(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.ALLOW

    def test_deny_tags(self):
        policy = PolicyBuilder().deny_tags("dangerous").build()
        ctx = _ctx(tags=frozenset({"dangerous"}))
        result = _run(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.DENY


class TestPolicyBuilderComposite:
    def test_admin_with_rate_limit(self):
        policy = (
            PolicyBuilder()
            .allow_roles("admin")
            .rate_limit(max_requests=100)
            .require_all()
            .with_id("admin-rate-limited")
            .build()
        )
        ctx = _ctx(metadata={"role": "admin"})
        result = _run(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.ALLOW

    def test_with_id_and_version(self):
        policy = (
            PolicyBuilder()
            .add_policy(AllowAllPolicy())
            .add_policy(AllowAllPolicy())
            .require_all()
            .with_id("my-policy")
            .with_version("2.0.0")
            .build()
        )
        pid = _run(policy.get_policy_id())
        ver = _run(policy.get_policy_version())
        assert pid == "my-policy"
        assert ver == "2.0.0"

    def test_require_attributes(self):
        policy = (
            PolicyBuilder()
            .require_attributes(
                is_admin=lambda ctx: ctx.metadata.get("role") == "admin",
            )
            .build()
        )
        ctx = _ctx(metadata={"role": "admin"})
        result = _run(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.ALLOW

    def test_restrict_hours(self):
        # Create policy restricting to business hours
        policy = PolicyBuilder().restrict_hours(start_hour=9, end_hour=17).build()
        # 12:00 UTC on a Monday
        ts = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
        ctx = PolicyEvaluationContext(
            actor_id="agent-1",
            action="call_tool",
            resource_id="tool:test",
            timestamp=ts,
        )
        result = _run(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.ALLOW
