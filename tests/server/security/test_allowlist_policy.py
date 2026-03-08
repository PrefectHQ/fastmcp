"""Tests for AllowlistPolicy and DenylistPolicy."""

from __future__ import annotations

import pytest

from fastmcp.server.security.policy.policies.allowlist import (
    AllowlistPolicy,
    DenylistPolicy,
)
from fastmcp.server.security.policy.provider import (
    PolicyDecision,
    PolicyEvaluationContext,
)


def _ctx(resource_id: str) -> PolicyEvaluationContext:
    """Shorthand to build a minimal evaluation context."""
    return PolicyEvaluationContext(
        actor_id="test-actor",
        action="call_tool",
        resource_id=resource_id,
    )


# ── AllowlistPolicy ─────────────────────────────────────────


class TestAllowlistPolicy:
    @pytest.mark.anyio
    async def test_allowed_exact_match(self):
        policy = AllowlistPolicy(allowed={"weather-lookup", "translate"})
        result = await policy.evaluate(_ctx("weather-lookup"))
        assert result.decision == PolicyDecision.ALLOW

    @pytest.mark.anyio
    async def test_denied_when_not_in_list(self):
        policy = AllowlistPolicy(allowed={"weather-lookup"})
        result = await policy.evaluate(_ctx("admin-tool"))
        assert result.decision == PolicyDecision.DENY
        assert "not in the allowlist" in result.reason

    @pytest.mark.anyio
    async def test_glob_pattern_star(self):
        policy = AllowlistPolicy(allowed={"weather-*"})
        result = await policy.evaluate(_ctx("weather-lookup"))
        assert result.decision == PolicyDecision.ALLOW

    @pytest.mark.anyio
    async def test_glob_pattern_no_match(self):
        policy = AllowlistPolicy(allowed={"weather-*"})
        result = await policy.evaluate(_ctx("translate"))
        assert result.decision == PolicyDecision.DENY

    @pytest.mark.anyio
    async def test_glob_question_mark(self):
        policy = AllowlistPolicy(allowed={"tool-?"})
        assert (await policy.evaluate(_ctx("tool-a"))).decision == PolicyDecision.ALLOW
        assert (await policy.evaluate(_ctx("tool-ab"))).decision == PolicyDecision.DENY

    @pytest.mark.anyio
    async def test_empty_allowlist_denies_all(self):
        policy = AllowlistPolicy(allowed=set())
        result = await policy.evaluate(_ctx("anything"))
        assert result.decision == PolicyDecision.DENY

    @pytest.mark.anyio
    async def test_add_and_remove(self):
        policy = AllowlistPolicy()
        policy.add("new-tool")
        assert policy.is_allowed("new-tool")
        policy.remove("new-tool")
        assert not policy.is_allowed("new-tool")

    @pytest.mark.anyio
    async def test_is_allowed_sync(self):
        policy = AllowlistPolicy(allowed={"tool-a", "tool-b"})
        assert policy.is_allowed("tool-a") is True
        assert policy.is_allowed("tool-c") is False

    @pytest.mark.anyio
    async def test_policy_id_and_version(self):
        policy = AllowlistPolicy(policy_id="my-allowlist", version="2.0")
        assert await policy.get_policy_id() == "my-allowlist"
        assert await policy.get_policy_version() == "2.0"

    @pytest.mark.anyio
    async def test_reason_includes_matching_pattern(self):
        policy = AllowlistPolicy(allowed={"data-*"})
        result = await policy.evaluate(_ctx("data-export"))
        assert "data-*" in result.reason


# ── DenylistPolicy ──────────────────────────────────────────


class TestDenylistPolicy:
    @pytest.mark.anyio
    async def test_denied_exact_match(self):
        policy = DenylistPolicy(denied={"dangerous-tool"})
        result = await policy.evaluate(_ctx("dangerous-tool"))
        assert result.decision == PolicyDecision.DENY

    @pytest.mark.anyio
    async def test_allowed_when_not_in_list(self):
        policy = DenylistPolicy(denied={"dangerous-tool"})
        result = await policy.evaluate(_ctx("safe-tool"))
        assert result.decision == PolicyDecision.ALLOW
        assert "not in the denylist" in result.reason

    @pytest.mark.anyio
    async def test_glob_pattern(self):
        policy = DenylistPolicy(denied={"admin-*"})
        assert (await policy.evaluate(_ctx("admin-delete"))).decision == PolicyDecision.DENY
        assert (await policy.evaluate(_ctx("admin-reset"))).decision == PolicyDecision.DENY
        assert (await policy.evaluate(_ctx("user-profile"))).decision == PolicyDecision.ALLOW

    @pytest.mark.anyio
    async def test_empty_denylist_allows_all(self):
        policy = DenylistPolicy(denied=set())
        result = await policy.evaluate(_ctx("anything"))
        assert result.decision == PolicyDecision.ALLOW

    @pytest.mark.anyio
    async def test_add_and_remove(self):
        policy = DenylistPolicy()
        policy.add("bad-tool")
        assert policy.is_denied("bad-tool")
        policy.remove("bad-tool")
        assert not policy.is_denied("bad-tool")

    @pytest.mark.anyio
    async def test_is_denied_sync(self):
        policy = DenylistPolicy(denied={"blocked"})
        assert policy.is_denied("blocked") is True
        assert policy.is_denied("safe") is False

    @pytest.mark.anyio
    async def test_policy_id_and_version(self):
        policy = DenylistPolicy(policy_id="my-denylist", version="3.0")
        assert await policy.get_policy_id() == "my-denylist"
        assert await policy.get_policy_version() == "3.0"

    @pytest.mark.anyio
    async def test_reason_includes_matching_pattern(self):
        policy = DenylistPolicy(denied={"debug-*"})
        result = await policy.evaluate(_ctx("debug-trace"))
        assert "debug-*" in result.reason


# ── Integration with PolicyEngine ────────────────────────────


class TestEngineIntegration:
    @pytest.mark.anyio
    async def test_allowlist_with_engine(self):
        from fastmcp.server.security.policy.engine import PolicyEngine

        engine = PolicyEngine(
            providers=[AllowlistPolicy(allowed={"safe-tool"})],
        )
        result = await engine.evaluate(_ctx("safe-tool"))
        assert result.decision == PolicyDecision.ALLOW

        result = await engine.evaluate(_ctx("other-tool"))
        assert result.decision == PolicyDecision.DENY

    @pytest.mark.anyio
    async def test_denylist_with_engine(self):
        from fastmcp.server.security.policy.engine import PolicyEngine

        engine = PolicyEngine(
            providers=[DenylistPolicy(denied={"bad-tool"})],
        )
        result = await engine.evaluate(_ctx("good-tool"))
        assert result.decision == PolicyDecision.ALLOW

        result = await engine.evaluate(_ctx("bad-tool"))
        assert result.decision == PolicyDecision.DENY

    @pytest.mark.anyio
    async def test_combined_allowlist_and_denylist(self):
        """AllOf(denylist, allowlist) → denylist blocks first."""
        from fastmcp.server.security.policy.engine import PolicyEngine

        engine = PolicyEngine(
            providers=[
                DenylistPolicy(denied={"tool-b"}),
                AllowlistPolicy(allowed={"tool-a", "tool-b"}),
            ],
        )
        # tool-a: not denied + in allowlist = ALLOW
        result = await engine.evaluate(_ctx("tool-a"))
        assert result.decision == PolicyDecision.ALLOW

        # tool-b: denied by denylist → short-circuit DENY
        result = await engine.evaluate(_ctx("tool-b"))
        assert result.decision == PolicyDecision.DENY

        # tool-c: not denied + not in allowlist = DENY (from allowlist)
        result = await engine.evaluate(_ctx("tool-c"))
        assert result.decision == PolicyDecision.DENY


# ── Import tests ─────────────────────────────────────────────


class TestImports:
    def test_import_from_policies_package(self):
        from fastmcp.server.security.policy.policies import (
            AllowlistPolicy,
            DenylistPolicy,
        )
        assert AllowlistPolicy is not None
        assert DenylistPolicy is not None

    def test_import_from_policy_package(self):
        from fastmcp.server.security.policy import (
            AllowlistPolicy,
            DenylistPolicy,
        )
        assert AllowlistPolicy is not None
        assert DenylistPolicy is not None
