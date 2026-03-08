"""Tests for SecureMCP Policy Engine."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fastmcp.server.security.policy.engine import (
    PolicyDecision,
    PolicyEngine,
    PolicySwapRecord,
    PolicyViolationError,
)
from fastmcp.server.security.policy.provider import (
    AllowAllPolicy,
    DenyAllPolicy,
    PolicyEvaluationContext,
    PolicyResult,
)


def _make_context(
    action: str = "call_tool",
    resource_id: str = "test_tool",
    tags: frozenset[str] | None = None,
    metadata: dict | None = None,
    actor_id: str | None = "test-actor",
) -> PolicyEvaluationContext:
    return PolicyEvaluationContext(
        actor_id=actor_id,
        action=action,
        resource_id=resource_id,
        tags=tags or frozenset(),
        metadata=metadata or {},
    )


# ── Basic engine behavior ────────────────────────────────────────────


class TestPolicyEngineBasics:
    async def test_default_engine_allows_all(self):
        engine = PolicyEngine()
        result = await engine.evaluate(_make_context())
        assert result.decision == PolicyDecision.ALLOW

    async def test_allow_all_policy(self):
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        result = await engine.evaluate(_make_context())
        assert result.decision == PolicyDecision.ALLOW

    async def test_deny_all_policy(self):
        engine = PolicyEngine(providers=[DenyAllPolicy()])
        result = await engine.evaluate(_make_context())
        assert result.decision == PolicyDecision.DENY

    async def test_multiple_providers_all_allow(self):
        engine = PolicyEngine(providers=[AllowAllPolicy(), AllowAllPolicy()])
        result = await engine.evaluate(_make_context())
        assert result.decision == PolicyDecision.ALLOW

    async def test_any_deny_short_circuits(self):
        engine = PolicyEngine(providers=[AllowAllPolicy(), DenyAllPolicy()])
        result = await engine.evaluate(_make_context())
        assert result.decision == PolicyDecision.DENY

    async def test_deny_before_allow_short_circuits(self):
        engine = PolicyEngine(providers=[DenyAllPolicy(), AllowAllPolicy()])
        result = await engine.evaluate(_make_context())
        assert result.decision == PolicyDecision.DENY

    async def test_evaluation_count_tracked(self):
        engine = PolicyEngine()
        assert engine.evaluation_count == 0
        await engine.evaluate(_make_context())
        assert engine.evaluation_count == 1
        await engine.evaluate(_make_context())
        assert engine.evaluation_count == 2

    async def test_deny_count_tracked(self):
        engine = PolicyEngine(providers=[DenyAllPolicy()])
        assert engine.deny_count == 0
        await engine.evaluate(_make_context())
        assert engine.deny_count == 1

    async def test_single_provider_not_list(self):
        engine = PolicyEngine(providers=AllowAllPolicy())
        result = await engine.evaluate(_make_context())
        assert result.decision == PolicyDecision.ALLOW


# ── Fail-closed behavior ────────────────────────────────────────────


class TestFailClosed:
    async def test_exception_in_provider_denies_when_fail_closed(self):
        class ErrorPolicy:
            async def evaluate(self, context):
                raise RuntimeError("Unexpected error")

            async def get_policy_id(self):
                return "error-policy"

            async def get_policy_version(self):
                return "1.0"

        engine = PolicyEngine(providers=[ErrorPolicy()], fail_closed=True)
        result = await engine.evaluate(_make_context())
        assert result.decision == PolicyDecision.DENY
        assert "fail-closed" in result.reason

    async def test_exception_in_provider_allows_when_fail_open(self):
        class ErrorPolicy:
            async def evaluate(self, context):
                raise RuntimeError("Unexpected error")

            async def get_policy_id(self):
                return "error-policy"

            async def get_policy_version(self):
                return "1.0"

        # When fail_closed=False and only provider errors, we get no results
        # which still hits the "no results" branch. With fail_closed=False
        # that branch allows.
        engine = PolicyEngine(providers=[ErrorPolicy()], fail_closed=False)
        result = await engine.evaluate(_make_context())
        assert result.decision == PolicyDecision.ALLOW

    async def test_no_providers_denies_when_fail_closed(self):
        engine = PolicyEngine(providers=[], fail_closed=True)
        result = await engine.evaluate(_make_context())
        assert result.decision == PolicyDecision.DENY

    async def test_no_providers_allows_when_fail_open(self):
        engine = PolicyEngine(providers=[], fail_closed=False)
        result = await engine.evaluate(_make_context())
        assert result.decision == PolicyDecision.ALLOW


# ── DEFER behavior ───────────────────────────────────────────────────


class TestDeferBehavior:
    async def test_all_defer_denies_when_fail_closed(self):
        class DeferPolicy:
            async def evaluate(self, context):
                return PolicyResult(
                    decision=PolicyDecision.DEFER,
                    reason="Deferring",
                    policy_id="defer-policy",
                )

            async def get_policy_id(self):
                return "defer-policy"

            async def get_policy_version(self):
                return "1.0"

        engine = PolicyEngine(providers=[DeferPolicy()], fail_closed=True)
        result = await engine.evaluate(_make_context())
        assert result.decision == PolicyDecision.DENY

    async def test_all_defer_allows_when_fail_open(self):
        class DeferPolicy:
            async def evaluate(self, context):
                return PolicyResult(
                    decision=PolicyDecision.DEFER,
                    reason="Deferring",
                    policy_id="defer-policy",
                )

            async def get_policy_id(self):
                return "defer-policy"

            async def get_policy_version(self):
                return "1.0"

        engine = PolicyEngine(providers=[DeferPolicy()], fail_closed=False)
        result = await engine.evaluate(_make_context())
        assert result.decision == PolicyDecision.ALLOW

    async def test_defer_plus_allow_allows(self):
        class DeferPolicy:
            async def evaluate(self, context):
                return PolicyResult(
                    decision=PolicyDecision.DEFER,
                    reason="Deferring",
                    policy_id="defer-policy",
                )

            async def get_policy_id(self):
                return "defer-policy"

            async def get_policy_version(self):
                return "1.0"

        engine = PolicyEngine(providers=[DeferPolicy(), AllowAllPolicy()])
        result = await engine.evaluate(_make_context())
        assert result.decision == PolicyDecision.ALLOW


# ── Hot swap ─────────────────────────────────────────────────────────


class TestHotSwap:
    async def test_hot_swap_replaces_provider(self):
        engine = PolicyEngine(providers=[DenyAllPolicy()])
        result = await engine.evaluate(_make_context())
        assert result.decision == PolicyDecision.DENY

        await engine.hot_swap(0, AllowAllPolicy(), reason="Switching to allow")
        result = await engine.evaluate(_make_context())
        assert result.decision == PolicyDecision.ALLOW

    async def test_hot_swap_records_history(self):
        engine = PolicyEngine(providers=[DenyAllPolicy()])
        assert len(engine.swap_history) == 0

        await engine.hot_swap(0, AllowAllPolicy(), reason="test swap")
        assert len(engine.swap_history) == 1

        record = engine.swap_history[0]
        assert record.old_policy_id == "deny-all"
        assert record.new_policy_id == "allow-all"
        assert record.reason == "test swap"

    async def test_hot_swap_disabled_raises(self):
        engine = PolicyEngine(providers=[DenyAllPolicy()], allow_hot_swap=False)
        with pytest.raises(RuntimeError, match="disabled"):
            await engine.hot_swap(0, AllowAllPolicy())

    async def test_hot_swap_invalid_index_raises(self):
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        with pytest.raises(IndexError):
            await engine.hot_swap(5, DenyAllPolicy())

    async def test_add_provider(self):
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        assert len(engine.providers) == 1

        await engine.add_provider(DenyAllPolicy())
        assert len(engine.providers) == 2

        result = await engine.evaluate(_make_context())
        assert result.decision == PolicyDecision.DENY

    async def test_remove_provider(self):
        engine = PolicyEngine(providers=[AllowAllPolicy(), DenyAllPolicy()])
        assert len(engine.providers) == 2

        removed = await engine.remove_provider(1)
        assert len(engine.providers) == 1

        result = await engine.evaluate(_make_context())
        assert result.decision == PolicyDecision.ALLOW


# ── Sync provider support ───────────────────────────────────────────


class TestSyncProviders:
    async def test_sync_provider_works(self):
        class SyncPolicy:
            def evaluate(self, context):
                return PolicyResult(
                    decision=PolicyDecision.ALLOW,
                    reason="Sync allow",
                    policy_id="sync-policy",
                )

            def get_policy_id(self):
                return "sync-policy"

            def get_policy_version(self):
                return "1.0"

        engine = PolicyEngine(providers=[SyncPolicy()])
        result = await engine.evaluate(_make_context())
        assert result.decision == PolicyDecision.ALLOW


# ── Constraint merging ───────────────────────────────────────────────


class TestConstraintMerging:
    async def test_constraints_merged_from_all_providers(self):
        class PolicyA:
            async def evaluate(self, context):
                return PolicyResult(
                    decision=PolicyDecision.ALLOW,
                    reason="A allows",
                    policy_id="a",
                    constraints=["constraint_a"],
                )

            async def get_policy_id(self):
                return "a"

            async def get_policy_version(self):
                return "1.0"

        class PolicyB:
            async def evaluate(self, context):
                return PolicyResult(
                    decision=PolicyDecision.ALLOW,
                    reason="B allows",
                    policy_id="b",
                    constraints=["constraint_b"],
                )

            async def get_policy_id(self):
                return "b"

            async def get_policy_version(self):
                return "1.0"

        engine = PolicyEngine(providers=[PolicyA(), PolicyB()])
        result = await engine.evaluate(_make_context())
        assert result.decision == PolicyDecision.ALLOW
        assert "constraint_a" in result.constraints
        assert "constraint_b" in result.constraints


# ── PolicyViolationError ─────────────────────────────────────────────


class TestPolicyViolationError:
    def test_error_contains_result(self):
        result = PolicyResult(
            decision=PolicyDecision.DENY,
            reason="Test denial",
            policy_id="test",
        )
        error = PolicyViolationError(result)
        assert error.result is result
        assert "Test denial" in str(error)
        assert "test" in str(error)
