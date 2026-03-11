"""Tests for policy dry-run / simulation mode."""

from __future__ import annotations

import json

import pytest

from fastmcp.server.security.policy.engine import PolicyEngine
from fastmcp.server.security.policy.policies.allowlist import (
    AllowlistPolicy,
    DenylistPolicy,
)
from fastmcp.server.security.policy.provider import (
    AllowAllPolicy,
    DenyAllPolicy,
    PolicyDecision,
    PolicyEvaluationContext,
)
from fastmcp.server.security.policy.simulation import (
    Scenario,
    ScenarioResult,
    SimulationReport,
    simulate,
)

# ── Scenario tests ─────────────────────────────────────────────────


class TestScenario:
    def test_defaults(self):
        s = Scenario(resource_id="tool-a")
        assert s.resource_id == "tool-a"
        assert s.action == "call_tool"
        assert s.actor_id == "sim-actor"
        assert s.metadata == {}
        assert s.tags == frozenset()
        assert s.label == ""

    def test_to_context(self):
        s = Scenario(
            resource_id="tool-a",
            action="read_resource",
            actor_id="agent-1",
            metadata={"key": "val"},
            tags=frozenset({"internal"}),
        )
        ctx = s.to_context()
        assert isinstance(ctx, PolicyEvaluationContext)
        assert ctx.resource_id == "tool-a"
        assert ctx.action == "read_resource"
        assert ctx.actor_id == "agent-1"
        assert ctx.metadata == {"key": "val"}
        assert ctx.tags == frozenset({"internal"})

    def test_custom_label(self):
        s = Scenario(resource_id="x", label="admin access test")
        assert s.label == "admin access test"


# ── Basic simulation ───────────────────────────────────────────────


class TestSimulateBasic:
    @pytest.mark.anyio
    async def test_all_allowed(self):
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        scenarios = [
            Scenario(resource_id="tool-a"),
            Scenario(resource_id="tool-b"),
        ]
        report = await simulate(engine, scenarios)
        assert report.total == 2
        assert report.allowed == 2
        assert report.denied == 0

    @pytest.mark.anyio
    async def test_all_denied(self):
        engine = PolicyEngine(providers=[DenyAllPolicy()])
        scenarios = [
            Scenario(resource_id="tool-a"),
            Scenario(resource_id="tool-b"),
        ]
        report = await simulate(engine, scenarios)
        assert report.total == 2
        assert report.allowed == 0
        assert report.denied == 2

    @pytest.mark.anyio
    async def test_mixed_results(self):
        policy = AllowlistPolicy(allowed={"safe-*"})
        scenarios = [
            Scenario(resource_id="safe-tool"),
            Scenario(resource_id="unsafe-tool"),
        ]
        report = await simulate(policy, scenarios)
        assert report.total == 2
        assert report.allowed == 1
        assert report.denied == 1

    @pytest.mark.anyio
    async def test_empty_scenarios(self):
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        report = await simulate(engine, [])
        assert report.total == 0
        assert report.allowed == 0
        assert report.denied == 0


# ── No side effects ────────────────────────────────────────────────


class TestNoSideEffects:
    @pytest.mark.anyio
    async def test_engine_counters_not_affected_by_allow(self):
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        assert engine.evaluation_count == 0

        await simulate(
            engine,
            [
                Scenario(resource_id="tool-a"),
                Scenario(resource_id="tool-b"),
            ],
        )

        # Engine counters should not change
        assert engine.evaluation_count == 0

    @pytest.mark.anyio
    async def test_engine_event_bus_not_triggered(self):
        """Simulation bypasses the engine's evaluate(), so no events fire."""
        from fastmcp.server.security.alerts.bus import SecurityEventBus

        bus = SecurityEventBus()
        events = []
        bus.subscribe(lambda e: events.append(e), name="test")

        engine = PolicyEngine(
            providers=[DenyAllPolicy()],
            event_bus=bus,
        )

        # Run through the engine directly to verify events DO fire normally
        await engine.evaluate(
            PolicyEvaluationContext(
                actor_id="x", action="call_tool", resource_id="tool-a"
            )
        )
        assert len(events) == 1  # Engine fires an event

        events.clear()

        # Simulation should NOT fire events
        await simulate(engine, [Scenario(resource_id="tool-a")])
        assert len(events) == 0

    @pytest.mark.anyio
    async def test_engine_counters_unchanged_by_simulation(self):
        """Simulation does not increment engine's evaluation or deny counts."""
        engine = PolicyEngine(providers=[DenyAllPolicy()])

        # One real evaluation
        await engine.evaluate(
            PolicyEvaluationContext(
                actor_id="x", action="call_tool", resource_id="tool-a"
            )
        )
        assert engine.evaluation_count == 1
        assert engine.deny_count == 1

        # Simulation should not affect counters
        await simulate(
            engine,
            [
                Scenario(resource_id="a"),
                Scenario(resource_id="b"),
            ],
        )
        assert engine.evaluation_count == 1
        assert engine.deny_count == 1


# ── Provider list input ────────────────────────────────────────────


class TestProviderListInput:
    @pytest.mark.anyio
    async def test_single_provider(self):
        policy = AllowlistPolicy(allowed={"tool-a"})
        report = await simulate(
            policy,
            [
                Scenario(resource_id="tool-a"),
                Scenario(resource_id="tool-b"),
            ],
        )
        assert report.allowed == 1
        assert report.denied == 1

    @pytest.mark.anyio
    async def test_provider_list(self):
        providers = [
            DenylistPolicy(denied={"blocked"}),
            AllowlistPolicy(allowed={"tool-a", "blocked"}),
        ]
        report = await simulate(
            providers,
            [
                Scenario(resource_id="tool-a"),
                Scenario(resource_id="blocked"),
                Scenario(resource_id="tool-c"),
            ],
        )
        # tool-a: not denied + in allowlist → ALLOW
        assert report.results[0].decision == PolicyDecision.ALLOW
        # blocked: denied by denylist → DENY
        assert report.results[1].decision == PolicyDecision.DENY
        # tool-c: not denied + not in allowlist → DENY
        assert report.results[2].decision == PolicyDecision.DENY


# ── Per-provider breakdown ─────────────────────────────────────────


class TestPerProviderBreakdown:
    @pytest.mark.anyio
    async def test_breakdown_on_allow(self):
        providers = [
            AllowlistPolicy(allowed={"tool-a"}, policy_id="allowlist-1"),
        ]
        report = await simulate(
            providers,
            [
                Scenario(resource_id="tool-a"),
            ],
        )
        r = report.results[0]
        assert len(r.per_provider) == 1
        assert r.per_provider[0].policy_id == "allowlist-1"
        assert r.per_provider[0].decision == PolicyDecision.ALLOW

    @pytest.mark.anyio
    async def test_breakdown_stops_on_deny(self):
        providers = [
            DenylistPolicy(denied={"tool-a"}, policy_id="deny-1"),
            AllowAllPolicy(),  # Should not be reached
        ]
        report = await simulate(
            providers,
            [
                Scenario(resource_id="tool-a"),
            ],
        )
        r = report.results[0]
        assert r.decision == PolicyDecision.DENY
        # Only 1 provider evaluated (short-circuit)
        assert len(r.per_provider) == 1
        assert r.per_provider[0].policy_id == "deny-1"


# ── Error handling ─────────────────────────────────────────────────


class TestErrorHandling:
    @pytest.mark.anyio
    async def test_provider_exception_fail_closed(self):
        class BrokenPolicy:
            async def evaluate(self, context):
                raise RuntimeError("broken")

            async def get_policy_id(self):
                return "broken"

            async def get_policy_version(self):
                return "1.0"

        report = await simulate(
            [BrokenPolicy()],
            [Scenario(resource_id="tool-a")],
            fail_closed=True,
        )
        r = report.results[0]
        assert r.decision == PolicyDecision.DENY
        assert r.error is not None
        assert report.errors == 1

    @pytest.mark.anyio
    async def test_provider_exception_fail_open(self):
        class BrokenPolicy:
            async def evaluate(self, context):
                raise RuntimeError("broken")

            async def get_policy_id(self):
                return "broken"

            async def get_policy_version(self):
                return "1.0"

        report = await simulate(
            [BrokenPolicy()],
            [Scenario(resource_id="tool-a")],
            fail_closed=False,
        )
        r = report.results[0]
        # With fail_closed=False and exception, behavior is: error recorded
        # but since no provider gave a DENY, it falls through
        assert r.error is not None
        assert report.errors == 1


# ── Report functionality ───────────────────────────────────────────


class TestReport:
    @pytest.mark.anyio
    async def test_summary_string(self):
        report = await simulate(
            AllowAllPolicy(),
            [Scenario(resource_id="tool-a"), Scenario(resource_id="tool-b")],
        )
        summary = report.summary()
        assert "2 scenarios" in summary
        assert "ALLOW:  2" in summary
        assert "DENY:   0" in summary

    @pytest.mark.anyio
    async def test_to_dict(self):
        report = await simulate(
            AllowlistPolicy(allowed={"tool-a"}),
            [
                Scenario(resource_id="tool-a", label="allowed"),
                Scenario(resource_id="tool-b", label="denied"),
            ],
        )
        d = report.to_dict()
        assert d["total"] == 2
        assert d["allowed"] == 1
        assert d["denied"] == 1
        assert len(d["results"]) == 2
        assert d["results"][0]["label"] == "allowed"
        assert d["results"][0]["decision"] == "allow"
        assert d["results"][1]["decision"] == "deny"

    @pytest.mark.anyio
    async def test_to_dict_is_json_serializable(self):
        report = await simulate(
            AllowAllPolicy(),
            [Scenario(resource_id="x")],
        )
        serialized = json.dumps(report.to_dict())
        assert len(serialized) > 0

    @pytest.mark.anyio
    async def test_filter_by_decision(self):
        report = await simulate(
            AllowlistPolicy(allowed={"tool-a"}),
            [
                Scenario(resource_id="tool-a"),
                Scenario(resource_id="tool-b"),
                Scenario(resource_id="tool-a"),
            ],
        )
        allowed = report.filter_by_decision(PolicyDecision.ALLOW)
        denied = report.filter_by_decision(PolicyDecision.DENY)
        assert len(allowed) == 2
        assert len(denied) == 1

    @pytest.mark.anyio
    async def test_report_has_timestamp(self):
        report = await simulate(AllowAllPolicy(), [Scenario(resource_id="x")])
        assert report.created_at is not None

    @pytest.mark.anyio
    async def test_per_provider_in_dict(self):
        report = await simulate(
            [AllowlistPolicy(allowed={"x"}, policy_id="my-policy")],
            [Scenario(resource_id="x")],
        )
        d = report.to_dict()
        providers = d["results"][0]["per_provider"]
        assert len(providers) == 1
        assert providers[0]["policy_id"] == "my-policy"
        assert providers[0]["decision"] == "allow"


# ── Fail-closed behavior ──────────────────────────────────────────


class TestFailClosed:
    @pytest.mark.anyio
    async def test_no_providers_fail_closed(self):
        report = await simulate([], [Scenario(resource_id="x")], fail_closed=True)
        assert report.results[0].decision == PolicyDecision.DENY

    @pytest.mark.anyio
    async def test_no_providers_fail_open(self):
        report = await simulate([], [Scenario(resource_id="x")], fail_closed=False)
        assert report.results[0].decision == PolicyDecision.ALLOW


# ── Integration with declarative policies ──────────────────────────


class TestDeclarativeIntegration:
    @pytest.mark.anyio
    async def test_simulate_declarative_policy(self):
        from fastmcp.server.security.policy.declarative import load_policy

        policy = load_policy(
            {
                "composition": "all_of",
                "policies": [
                    {"type": "allowlist", "allowed": ["safe-*"]},
                    {"type": "denylist", "denied": ["safe-but-blocked"]},
                ],
            }
        )

        report = await simulate(
            policy,
            [
                Scenario(resource_id="safe-tool", label="should allow"),
                Scenario(resource_id="safe-but-blocked", label="should deny"),
                Scenario(resource_id="unknown", label="should deny"),
            ],
        )
        assert report.allowed == 1
        assert report.denied == 2
        assert report.results[0].scenario.label == "should allow"


# ── Import tests ───────────────────────────────────────────────────


class TestImports:
    def test_import_from_simulation_module(self):
        from fastmcp.server.security.policy.simulation import (
            Scenario,
            simulate,
        )

        assert Scenario is not None
        assert ScenarioResult is not None
        assert SimulationReport is not None
        assert simulate is not None

    def test_import_from_policy_package(self):
        from fastmcp.server.security.policy import (
            Scenario,
            SimulationReport,
            simulate,
        )

        assert Scenario is not None
        assert SimulationReport is not None
        assert simulate is not None
