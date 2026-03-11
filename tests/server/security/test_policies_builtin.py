"""Tests for built-in policy types (RBAC, ABAC, Temporal, ResourceScoped, RateLimit)."""

import asyncio
from datetime import datetime, time, timedelta, timezone

from fastmcp.server.security.policy.policies.abac import AttributeBasedPolicy
from fastmcp.server.security.policy.policies.rate_limit import RateLimitPolicy
from fastmcp.server.security.policy.policies.rbac import RoleBasedPolicy
from fastmcp.server.security.policy.policies.resource_scoped import (
    ResourceScopedPolicy,
)
from fastmcp.server.security.policy.policies.temporal import TimeBasedPolicy
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
    timestamp: datetime | None = None,
) -> PolicyEvaluationContext:
    return PolicyEvaluationContext(
        actor_id="agent-1",
        action=action,
        resource_id=resource_id,
        metadata=metadata or {},
        tags=tags or frozenset(),
        timestamp=timestamp or datetime.now(timezone.utc),
    )


# ── RBAC Tests ─────────────────────────────────────────────────


class TestRoleBasedPolicy:
    def test_admin_wildcard_allows_all(self):
        policy = RoleBasedPolicy(role_mappings={"admin": {"*"}})
        ctx = _ctx(metadata={"role": "admin"})
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.ALLOW

    def test_specific_action_allowed(self):
        policy = RoleBasedPolicy(role_mappings={"viewer": {"read_resource"}})
        ctx = _ctx(action="read_resource", metadata={"role": "viewer"})
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.ALLOW

    def test_action_not_in_role(self):
        policy = RoleBasedPolicy(role_mappings={"viewer": {"read_resource"}})
        ctx = _ctx(action="call_tool", metadata={"role": "viewer"})
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.DENY

    def test_unknown_role_denied(self):
        policy = RoleBasedPolicy(role_mappings={"admin": {"*"}})
        ctx = _ctx(metadata={"role": "unknown"})
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.DENY

    def test_no_role_in_metadata(self):
        policy = RoleBasedPolicy(role_mappings={"admin": {"*"}})
        ctx = _ctx(metadata={})
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.DENY

    def test_custom_role_resolver(self):
        policy = RoleBasedPolicy(
            role_mappings={"super": {"*"}},
            role_resolver=lambda ctx: ctx.metadata.get("custom_role"),
        )
        ctx = _ctx(metadata={"custom_role": "super"})
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.ALLOW

    def test_constraint_includes_role(self):
        policy = RoleBasedPolicy(role_mappings={"admin": {"*"}})
        ctx = _ctx(metadata={"role": "admin"})
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))
        assert "role:admin" in result.constraints


# ── ABAC Tests ─────────────────────────────────────────────────


class TestAttributeBasedPolicy:
    def test_all_rules_pass(self):
        policy = AttributeBasedPolicy(
            rules={
                "is_internal": lambda ctx: ctx.metadata.get("network") == "internal",
                "has_clearance": lambda ctx: ctx.metadata.get("clearance", 0) >= 3,
            },
            require_all=True,
        )
        ctx = _ctx(metadata={"network": "internal", "clearance": 5})
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.ALLOW

    def test_one_rule_fails_require_all(self):
        policy = AttributeBasedPolicy(
            rules={
                "is_internal": lambda ctx: ctx.metadata.get("network") == "internal",
                "has_clearance": lambda ctx: ctx.metadata.get("clearance", 0) >= 3,
            },
            require_all=True,
        )
        ctx = _ctx(metadata={"network": "external", "clearance": 5})
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.DENY

    def test_any_rule_passes(self):
        policy = AttributeBasedPolicy(
            rules={
                "is_admin": lambda ctx: ctx.metadata.get("role") == "admin",
                "is_internal": lambda ctx: ctx.metadata.get("network") == "internal",
            },
            require_all=False,
        )
        ctx = _ctx(metadata={"network": "internal"})
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.ALLOW

    def test_no_rules_defers(self):
        policy = AttributeBasedPolicy(rules={})
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.DEFER

    def test_rule_exception_counts_as_failure(self):
        policy = AttributeBasedPolicy(
            rules={"bad_rule": lambda ctx: 1 / 0},
            require_all=True,
        )
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.DENY

    def test_async_rule(self):
        async def async_check(ctx):
            return ctx.metadata.get("approved", False)

        policy = AttributeBasedPolicy(
            rules={"approved": async_check},
            require_all=True,
        )
        ctx = _ctx(metadata={"approved": True})
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.ALLOW


# ── Temporal Tests ─────────────────────────────────────────────


class TestTimeBasedPolicy:
    def test_within_window(self):
        # Create a timestamp at 12:00 on a Monday (weekday=0)
        ts = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)  # Monday
        policy = TimeBasedPolicy(
            allowed_days=frozenset({0, 1, 2, 3, 4}),
            allowed_start_time=time(9, 0),
            allowed_end_time=time(17, 0),
        )
        ctx = _ctx(timestamp=ts)
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.ALLOW

    def test_outside_hours(self):
        ts = datetime(2026, 3, 9, 20, 0, tzinfo=timezone.utc)  # Monday 8pm
        policy = TimeBasedPolicy(
            allowed_start_time=time(9, 0),
            allowed_end_time=time(17, 0),
        )
        ctx = _ctx(timestamp=ts)
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.DENY

    def test_wrong_day(self):
        ts = datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc)  # Sunday
        policy = TimeBasedPolicy(
            allowed_days=frozenset({0, 1, 2, 3, 4}),  # Mon-Fri only
        )
        ctx = _ctx(timestamp=ts)
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.DENY

    def test_overnight_window(self):
        # Night shift: 22:00 to 06:00
        ts = datetime(2026, 3, 9, 2, 0, tzinfo=timezone.utc)  # 2am
        policy = TimeBasedPolicy(
            allowed_start_time=time(22, 0),
            allowed_end_time=time(6, 0),
        )
        ctx = _ctx(timestamp=ts)
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.ALLOW

    def test_utc_offset(self):
        # 10:00 UTC = 15:30 IST (UTC+5:30... but we use integer offset so UTC+5)
        ts = datetime(2026, 3, 9, 10, 0, tzinfo=timezone.utc)
        policy = TimeBasedPolicy(
            allowed_start_time=time(14, 0),
            allowed_end_time=time(16, 0),
            utc_offset_hours=5,
        )
        ctx = _ctx(timestamp=ts)
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.ALLOW

    def test_all_days_all_hours(self):
        policy = TimeBasedPolicy()
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.ALLOW


# ── ResourceScoped Tests ───────────────────────────────────────


class TestResourceScopedPolicy:
    def test_exact_match(self):
        policy = ResourceScopedPolicy(
            resource_rules={
                "tool:safe": AllowAllPolicy(),
                "tool:dangerous": DenyAllPolicy(),
            },
        )
        ctx = _ctx(resource_id="tool:safe")
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.ALLOW

    def test_exact_match_deny(self):
        policy = ResourceScopedPolicy(
            resource_rules={"tool:dangerous": DenyAllPolicy()},
        )
        ctx = _ctx(resource_id="tool:dangerous")
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.DENY

    def test_no_match_defers(self):
        policy = ResourceScopedPolicy(
            resource_rules={"tool:known": AllowAllPolicy()},
        )
        ctx = _ctx(resource_id="tool:unknown")
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.DEFER

    def test_default_policy(self):
        policy = ResourceScopedPolicy(
            resource_rules={},
            default_policy=DenyAllPolicy(),
        )
        ctx = _ctx(resource_id="anything")
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.DENY

    def test_prefix_match(self):
        policy = ResourceScopedPolicy(
            resource_rules={"tool:": AllowAllPolicy()},
            prefix_match=True,
        )
        ctx = _ctx(resource_id="tool:calculator")
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.ALLOW


# ── RateLimit Tests ────────────────────────────────────────────


class TestRateLimitPolicy:
    def test_within_limit(self):
        policy = RateLimitPolicy(max_requests=5, window_seconds=60)
        ctx = _ctx()
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.ALLOW

    def test_exceeds_limit(self):
        policy = RateLimitPolicy(max_requests=3, window_seconds=60)
        for _ in range(3):
            asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.DENY

    def test_window_expiry(self):
        policy = RateLimitPolicy(max_requests=2, window_seconds=60)
        old_ts = datetime.now(timezone.utc) - timedelta(seconds=120)

        # Two old requests
        for _ in range(2):
            ctx = _ctx(timestamp=old_ts)
            asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))

        # New request should be within limit (old ones expired)
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.ALLOW

    def test_per_actor_isolation(self):
        policy = RateLimitPolicy(max_requests=1, window_seconds=60)

        ctx1 = PolicyEvaluationContext(
            actor_id="agent-1", action="call_tool", resource_id="tool:test"
        )
        ctx2 = PolicyEvaluationContext(
            actor_id="agent-2", action="call_tool", resource_id="tool:test"
        )

        asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx1))
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx2))
        assert result.decision == PolicyDecision.ALLOW

    def test_get_remaining(self):
        policy = RateLimitPolicy(max_requests=5, window_seconds=60)
        asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        remaining = policy.get_remaining("agent-1")
        assert remaining == 3

    def test_reset_specific_actor(self):
        policy = RateLimitPolicy(max_requests=2, window_seconds=60)
        asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        policy.reset("agent-1")
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.ALLOW

    def test_reset_all(self):
        policy = RateLimitPolicy(max_requests=1, window_seconds=60)
        asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        policy.reset()
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(_ctx()))
        assert result.decision == PolicyDecision.ALLOW

    def test_anonymous_actor(self):
        policy = RateLimitPolicy(max_requests=1, window_seconds=60)
        ctx = PolicyEvaluationContext(
            actor_id=None, action="call_tool", resource_id="tool:test"
        )
        result = asyncio.get_event_loop().run_until_complete(policy.evaluate(ctx))
        assert result.decision == PolicyDecision.ALLOW
