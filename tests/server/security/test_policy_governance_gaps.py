"""Tests for policy governance gap closures.

Covers: PolicyValidator, PolicyGovernor, PolicyMonitor, constraint enforcement,
invariant wiring, and full orchestrator integration.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from fastmcp.server.security.policy.engine import PolicyEngine
from fastmcp.server.security.policy.governance import (
    PolicyGovernor,
    ProposalAction,
    ProposalStatus,
)
from fastmcp.server.security.policy.invariants import (
    Invariant,
    InvariantRegistry,
    InvariantSeverity,
)
from fastmcp.server.security.policy.monitoring import AlertLevel, PolicyMonitor
from fastmcp.server.security.policy.policies.allowlist import (
    AllowlistPolicy,
    DenylistPolicy,
)
from fastmcp.server.security.policy.provider import (
    AllowAllPolicy,
    DenyAllPolicy,
    PolicyEvaluationContext,
)
from fastmcp.server.security.policy.simulation import Scenario
from fastmcp.server.security.policy.validator import (
    PolicyValidator,
)

# ── PolicyValidator Tests ─────────────────────────────────────


class TestValidatorSchemaValidation:
    """Tests for declarative policy schema validation."""

    def test_valid_allowlist(self) -> None:
        v = PolicyValidator()
        result = v.validate_declarative(
            {
                "type": "allowlist",
                "allowed": ["tool-a", "tool-b"],
            }
        )
        assert result.valid

    def test_valid_composite(self) -> None:
        v = PolicyValidator()
        result = v.validate_declarative(
            {
                "composition": "all_of",
                "policies": [
                    {"type": "allowlist", "allowed": ["tool-*"]},
                    {"type": "denylist", "denied": ["admin-*"]},
                ],
            }
        )
        assert result.valid

    def test_missing_type(self) -> None:
        v = PolicyValidator()
        result = v.validate_declarative({"allowed": ["tool-a"]})
        assert not result.valid
        assert any(f.code == "E_MISSING_TYPE" for f in result.errors)

    def test_unknown_type(self) -> None:
        v = PolicyValidator()
        result = v.validate_declarative({"type": "banana"})
        assert not result.valid
        assert any(f.code == "E_UNKNOWN_TYPE" for f in result.errors)

    def test_missing_required_field(self) -> None:
        v = PolicyValidator()
        result = v.validate_declarative({"type": "allowlist"})
        assert not result.valid
        assert any(f.code == "E_MISSING_FIELD" for f in result.errors)

    def test_empty_allowlist_warning(self) -> None:
        v = PolicyValidator()
        result = v.validate_declarative({"type": "allowlist", "allowed": []})
        assert result.valid  # Warning, not error
        assert len(result.warnings) == 1
        assert result.warnings[0].code == "W_EMPTY_ALLOWLIST"

    def test_invalid_allowed_type(self) -> None:
        v = PolicyValidator()
        result = v.validate_declarative({"type": "allowlist", "allowed": "not-a-list"})
        assert not result.valid

    def test_unknown_composition(self) -> None:
        v = PolicyValidator()
        result = v.validate_declarative(
            {
                "composition": "banana_of",
                "policies": [],
            }
        )
        assert not result.valid
        assert any(f.code == "E_UNKNOWN_COMPOSITION" for f in result.errors)

    def test_not_requires_single_child(self) -> None:
        v = PolicyValidator()
        result = v.validate_declarative(
            {
                "composition": "not",
                "policies": [
                    {"type": "allowlist", "allowed": ["a"]},
                    {"type": "allowlist", "allowed": ["b"]},
                ],
            }
        )
        assert not result.valid
        assert any(f.code == "E_NOT_SINGLE_CHILD" for f in result.errors)

    def test_depth_limit(self) -> None:
        v = PolicyValidator(max_composition_depth=2)
        deep = {
            "composition": "all_of",
            "policies": [
                {
                    "composition": "all_of",
                    "policies": [
                        {
                            "composition": "all_of",
                            "policies": [{"type": "allow_all"}],
                        }
                    ],
                }
            ],
        }
        result = v.validate_declarative(deep)
        assert not result.valid
        assert any(f.code == "E_DEPTH_EXCEEDED" for f in result.errors)

    def test_rate_limit_validation(self) -> None:
        v = PolicyValidator()
        result = v.validate_declarative(
            {
                "type": "rate_limit",
                "max_requests": -1,
            }
        )
        assert not result.valid

    def test_time_based_invalid_day(self) -> None:
        v = PolicyValidator()
        result = v.validate_declarative(
            {
                "type": "time_based",
                "allowed_days": [0, 7],  # 7 is invalid
            }
        )
        assert not result.valid

    def test_time_based_invalid_hour(self) -> None:
        v = PolicyValidator()
        result = v.validate_declarative(
            {
                "type": "time_based",
                "start_hour": 25,
            }
        )
        assert not result.valid

    def test_rbac_invalid_mappings_type(self) -> None:
        v = PolicyValidator()
        result = v.validate_declarative(
            {
                "type": "rbac",
                "role_mappings": "not-a-dict",
            }
        )
        assert not result.valid

    def test_rbac_invalid_decision(self) -> None:
        v = PolicyValidator()
        result = v.validate_declarative(
            {
                "type": "rbac",
                "role_mappings": {"admin": ["*"]},
                "default_decision": "banana",
            }
        )
        assert not result.valid

    def test_valid_allow_all(self) -> None:
        v = PolicyValidator()
        result = v.validate_declarative({"type": "allow_all"})
        assert result.valid

    def test_to_dict(self) -> None:
        v = PolicyValidator()
        result = v.validate_declarative({"type": "banana"})
        d = result.to_dict()
        assert d["valid"] is False
        assert d["error_count"] >= 1
        assert len(d["findings"]) >= 1


class TestValidatorSemanticValidation:
    """Tests for semantic validation of provider lists."""

    def test_contradicting_patterns(self) -> None:
        v = PolicyValidator()
        result = v.validate_providers(
            [
                AllowlistPolicy(allowed={"tool-a", "tool-b"}),
                DenylistPolicy(denied={"tool-a", "tool-c"}),
            ]
        )
        assert not result.valid
        assert any(f.code == "E_CONTRADICTING_RULES" for f in result.errors)

    def test_allow_all_plus_deny_all(self) -> None:
        v = PolicyValidator()
        result = v.validate_providers([AllowAllPolicy(), DenyAllPolicy()])
        assert not result.valid
        assert any(f.code == "E_ALLOW_DENY_ALL" for f in result.errors)

    def test_deny_all_shadows_others(self) -> None:
        v = PolicyValidator()
        result = v.validate_providers(
            [
                AllowlistPolicy(allowed={"tool-a"}),
                DenyAllPolicy(),
            ]
        )
        assert any(f.code == "W_DENY_ALL_SHADOWS" for f in result.warnings)

    def test_too_many_providers(self) -> None:
        v = PolicyValidator(max_providers=2)
        providers = [AllowAllPolicy() for _ in range(5)]
        result = v.validate_providers(providers)
        assert any(f.code == "W_TOO_MANY_PROVIDERS" for f in result.warnings)

    def test_no_issues(self) -> None:
        v = PolicyValidator()
        result = v.validate_providers(
            [
                AllowlistPolicy(allowed={"tool-a"}),
                DenylistPolicy(denied={"admin-*"}),
            ]
        )
        assert result.valid

    def test_validate_full(self) -> None:
        v = PolicyValidator()
        result = v.validate_full(
            config={"type": "banana"},
            providers=[AllowAllPolicy(), DenyAllPolicy()],
        )
        assert not result.valid
        # Should have errors from both schema and semantic
        assert len(result.errors) >= 2


# ── PolicyGovernor Tests ──────────────────────────────────────


class TestGovernorWorkflow:
    """Tests for the policy governance workflow."""

    def test_propose_add(self) -> None:
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        gov = PolicyGovernor(engine=engine, require_simulation=False)
        proposal = gov.propose_add(
            AllowlistPolicy(allowed={"tool-a"}),
            author="test-user",
            description="Add allowlist",
        )
        assert proposal.status == ProposalStatus.DRAFT
        assert proposal.action == ProposalAction.ADD
        assert proposal.author == "test-user"

    def test_propose_swap(self) -> None:
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        gov = PolicyGovernor(engine=engine, require_simulation=False)
        proposal = gov.propose_swap(
            0,
            AllowlistPolicy(allowed={"tool-a"}),
            author="test-user",
        )
        assert proposal.action == ProposalAction.SWAP
        assert proposal.target_index == 0

    def test_propose_swap_invalid_index(self) -> None:
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        gov = PolicyGovernor(engine=engine)
        with pytest.raises(IndexError):
            gov.propose_swap(5, AllowAllPolicy())

    def test_propose_remove(self) -> None:
        engine = PolicyEngine(providers=[AllowAllPolicy(), DenyAllPolicy()])
        gov = PolicyGovernor(engine=engine, require_simulation=False)
        proposal = gov.propose_remove(1, author="test-user")
        assert proposal.action == ProposalAction.REMOVE

    def test_validate_catches_errors(self) -> None:
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        gov = PolicyGovernor(engine=engine, require_simulation=False)
        proposal = gov.propose_add(DenyAllPolicy())
        result = gov.validate_proposal(proposal.proposal_id)
        # AllowAll + DenyAll should flag contradiction
        assert not result.valid
        assert proposal.status == ProposalStatus.VALIDATION_FAILED

    def test_validate_passes(self) -> None:
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        gov = PolicyGovernor(engine=engine, require_simulation=False)
        proposal = gov.propose_add(
            AllowlistPolicy(allowed={"tool-a"}),
        )
        result = gov.validate_proposal(proposal.proposal_id)
        assert result.valid
        assert proposal.status == ProposalStatus.VALIDATED

    @pytest.mark.anyio
    async def test_simulate_proposal(self) -> None:
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        gov = PolicyGovernor(engine=engine)
        proposal = gov.propose_add(
            AllowlistPolicy(allowed={"tool-a"}),
        )
        gov.validate_proposal(proposal.proposal_id)

        scenarios = [
            Scenario(resource_id="tool-a"),
            Scenario(resource_id="tool-b"),
        ]
        report = await gov.simulate_proposal(proposal.proposal_id, scenarios)
        assert report.total == 2
        assert proposal.status == ProposalStatus.SIMULATED

    def test_approve_after_validation(self) -> None:
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        gov = PolicyGovernor(engine=engine, require_simulation=False)
        proposal = gov.propose_add(
            AllowlistPolicy(allowed={"tool-a"}),
        )
        gov.validate_proposal(proposal.proposal_id)
        gov.approve(proposal.proposal_id, approver="ciso")
        assert proposal.status == ProposalStatus.APPROVED
        assert proposal.approved_by == "ciso"

    def test_approve_requires_simulation_when_enabled(self) -> None:
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        gov = PolicyGovernor(engine=engine, require_simulation=True)
        proposal = gov.propose_add(
            AllowlistPolicy(allowed={"tool-a"}),
        )
        gov.validate_proposal(proposal.proposal_id)
        with pytest.raises(ValueError, match="Simulation is required"):
            gov.approve(proposal.proposal_id)

    def test_reject(self) -> None:
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        gov = PolicyGovernor(engine=engine, require_simulation=False)
        proposal = gov.propose_add(AllowlistPolicy(allowed={"x"}))
        gov.reject(proposal.proposal_id, reason="Not needed")
        assert proposal.status == ProposalStatus.REJECTED

    def test_withdraw(self) -> None:
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        gov = PolicyGovernor(engine=engine)
        proposal = gov.propose_add(AllowlistPolicy(allowed={"x"}))
        gov.withdraw(proposal.proposal_id)
        assert proposal.status == ProposalStatus.WITHDRAWN

    @pytest.mark.anyio
    async def test_deploy_add(self) -> None:
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        gov = PolicyGovernor(engine=engine, require_simulation=False)
        proposal = gov.propose_add(
            AllowlistPolicy(allowed={"tool-a"}),
        )
        gov.validate_proposal(proposal.proposal_id)
        gov.approve(proposal.proposal_id)
        await gov.deploy(proposal.proposal_id)
        assert proposal.status == ProposalStatus.DEPLOYED
        assert len(engine.providers) == 2

    @pytest.mark.anyio
    async def test_deploy_swap(self) -> None:
        old_policy = AllowAllPolicy()
        engine = PolicyEngine(providers=[old_policy])
        gov = PolicyGovernor(engine=engine, require_simulation=False)
        new_policy = AllowlistPolicy(allowed={"tool-a"})
        proposal = gov.propose_swap(0, new_policy)
        gov.validate_proposal(proposal.proposal_id)
        gov.approve(proposal.proposal_id)
        await gov.deploy(proposal.proposal_id)
        assert proposal.status == ProposalStatus.DEPLOYED
        assert engine.providers[0] is new_policy

    @pytest.mark.anyio
    async def test_deploy_remove(self) -> None:
        engine = PolicyEngine(providers=[AllowAllPolicy(), DenyAllPolicy()])
        gov = PolicyGovernor(engine=engine, require_simulation=False)
        proposal = gov.propose_remove(1)
        gov.validate_proposal(proposal.proposal_id)
        gov.approve(proposal.proposal_id)
        await gov.deploy(proposal.proposal_id)
        assert len(engine.providers) == 1

    @pytest.mark.anyio
    async def test_deploy_requires_approval(self) -> None:
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        gov = PolicyGovernor(
            engine=engine, require_approval=True, require_simulation=False
        )
        proposal = gov.propose_add(AllowlistPolicy(allowed={"x"}))
        gov.validate_proposal(proposal.proposal_id)
        with pytest.raises(ValueError, match="Must be approved"):
            await gov.deploy(proposal.proposal_id)

    def test_proposals_listing(self) -> None:
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        gov = PolicyGovernor(engine=engine, require_simulation=False)
        gov.propose_add(AllowlistPolicy(allowed={"a"}))
        gov.propose_add(AllowlistPolicy(allowed={"b"}))
        assert len(gov.proposals) == 2

    def test_pending_proposals(self) -> None:
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        gov = PolicyGovernor(engine=engine, require_simulation=False)
        p1 = gov.propose_add(AllowlistPolicy(allowed={"a"}))
        p2 = gov.propose_add(AllowlistPolicy(allowed={"b"}))
        gov.reject(p1.proposal_id, reason="no")
        assert len(gov.pending_proposals) == 1

    def test_proposal_to_dict(self) -> None:
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        gov = PolicyGovernor(engine=engine)
        proposal = gov.propose_add(AllowlistPolicy(allowed={"a"}), author="me")
        d = proposal.to_dict()
        assert d["status"] == "draft"
        assert d["author"] == "me"

    def test_get_unknown_proposal(self) -> None:
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        gov = PolicyGovernor(engine=engine)
        with pytest.raises(KeyError):
            gov.validate_proposal("nonexistent")


# ── PolicyMonitor Tests ───────────────────────────────────────


class TestMonitoring:
    """Tests for the policy monitoring system."""

    def test_record_and_metrics(self) -> None:
        monitor = PolicyMonitor()
        for _ in range(10):
            monitor.record_decision(decision="allow", resource_id="tool-a")
        for _ in range(5):
            monitor.record_decision(decision="deny", resource_id="tool-b")

        metrics = monitor.get_metrics()
        assert metrics["window"]["total"] == 15
        assert metrics["window"]["denied"] == 5
        assert metrics["window"]["allowed"] == 10
        assert metrics["lifetime"]["total_decisions"] == 15

    def test_deny_rate_warning(self) -> None:
        monitor = PolicyMonitor(deny_rate_warning=0.3, deny_rate_critical=0.6)
        # 4 denials out of 10 = 40% > 30% warning threshold
        for _ in range(6):
            monitor.record_decision(decision="allow", resource_id="x")
        for _ in range(4):
            monitor.record_decision(decision="deny", resource_id="x")

        alerts = monitor.check_anomalies()
        assert len(alerts) >= 1
        assert any(a.level == AlertLevel.WARNING for a in alerts)

    def test_deny_rate_critical(self) -> None:
        monitor = PolicyMonitor(deny_rate_warning=0.3, deny_rate_critical=0.6)
        # 7 denials out of 10 = 70% > 60% critical threshold
        for _ in range(3):
            monitor.record_decision(decision="allow", resource_id="x")
        for _ in range(7):
            monitor.record_decision(decision="deny", resource_id="x")

        alerts = monitor.check_anomalies()
        assert any(a.level == AlertLevel.CRITICAL for a in alerts)

    def test_burst_detection(self) -> None:
        monitor = PolicyMonitor(burst_threshold=5)
        for _ in range(10):
            monitor.record_decision(decision="deny", resource_id="x")

        alerts = monitor.check_anomalies()
        assert any(a.metric == "deny_burst" for a in alerts)

    def test_resource_concentration(self) -> None:
        monitor = PolicyMonitor(burst_threshold=10)
        for _ in range(6):
            monitor.record_decision(decision="deny", resource_id="admin-panel")
        for _ in range(10):
            monitor.record_decision(decision="allow", resource_id="tool-a")

        alerts = monitor.check_anomalies()
        assert any(a.metric == "resource_deny_burst" for a in alerts)

    def test_no_alerts_normal_operation(self) -> None:
        monitor = PolicyMonitor()
        for _ in range(100):
            monitor.record_decision(decision="allow", resource_id="x")

        alerts = monitor.check_anomalies()
        assert len(alerts) == 0

    def test_alert_cooldown(self) -> None:
        monitor = PolicyMonitor(deny_rate_warning=0.1)
        for _ in range(5):
            monitor.record_decision(decision="deny", resource_id="x")

        alerts1 = monitor.check_anomalies()
        alerts2 = monitor.check_anomalies()  # Should be empty due to cooldown
        assert len(alerts1) >= 1
        assert len(alerts2) == 0

    def test_recent_alerts(self) -> None:
        monitor = PolicyMonitor(deny_rate_warning=0.1)
        for _ in range(5):
            monitor.record_decision(decision="deny", resource_id="x")
        monitor.check_anomalies()

        recent = monitor.get_recent_alerts()
        assert len(recent) >= 1
        assert "level" in recent[0]

    def test_empty_window(self) -> None:
        monitor = PolicyMonitor()
        alerts = monitor.check_anomalies()
        assert len(alerts) == 0

    def test_top_denied_resources(self) -> None:
        monitor = PolicyMonitor()
        for i in range(5):
            monitor.record_decision(decision="deny", resource_id=f"tool-{i}")
        for _ in range(3):
            monitor.record_decision(decision="deny", resource_id="tool-0")

        metrics = monitor.get_metrics()
        top = metrics["window"]["top_denied_resources"]
        assert len(top) > 0
        assert top[0]["resource_id"] == "tool-0"

    def test_event_bus_integration(self) -> None:
        bus = MagicMock()
        monitor = PolicyMonitor(event_bus=bus, deny_rate_warning=0.1)
        for _ in range(5):
            monitor.record_decision(decision="deny", resource_id="x")
        monitor.check_anomalies()
        assert bus.emit.called


# ── Invariant Wiring Tests ────────────────────────────────────


class TestInvariantWiring:
    """Tests for invariant verification wired into engine."""

    @pytest.mark.anyio
    async def test_engine_accepts_invariant_registry(self) -> None:
        registry = InvariantRegistry()
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        engine._invariant_registry = registry
        assert engine.invariant_registry is registry

    @pytest.mark.anyio
    async def test_invariant_checked_on_hot_swap(self) -> None:
        registry = InvariantRegistry()
        registry.register(
            Invariant(
                id="min-providers",
                description="Must have at least 1 provider",
                expression="provider_count >= 1",
                severity=InvariantSeverity.CRITICAL,
            )
        )

        engine = PolicyEngine(providers=[AllowAllPolicy()])
        engine._invariant_registry = registry

        new_policy = AllowlistPolicy(allowed={"tool-a"})
        await engine.hot_swap(0, new_policy, reason="test")

        # Give the fire-and-forget task a moment to complete
        await asyncio.sleep(0.1)

        results = registry.recent_results
        assert len(results) >= 1
        assert results[0].satisfied is True

    @pytest.mark.anyio
    async def test_invariant_failure_doesnt_block_swap(self) -> None:
        registry = InvariantRegistry()
        registry.register(
            Invariant(
                id="always-fail",
                description="Always fails",
                expression="False",
            )
        )

        engine = PolicyEngine(providers=[AllowAllPolicy()])
        engine._invariant_registry = registry

        new_policy = AllowlistPolicy(allowed={"tool-a"})
        # Should not raise even though invariant fails
        await engine.hot_swap(0, new_policy, reason="test")
        assert engine.providers[0] is new_policy


# ── Monitor Wiring in Engine ──────────────────────────────────


class TestMonitorWiringInEngine:
    """Tests for monitor wired into PolicyEngine evaluation."""

    @pytest.mark.anyio
    async def test_engine_feeds_monitor(self) -> None:
        monitor = PolicyMonitor()
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        engine._monitor = monitor

        ctx = PolicyEvaluationContext(
            actor_id="test",
            action="call_tool",
            resource_id="tool-a",
        )
        await engine.evaluate(ctx)

        metrics = monitor.get_metrics()
        assert metrics["lifetime"]["total_decisions"] == 1

    @pytest.mark.anyio
    async def test_engine_feeds_monitor_on_deny(self) -> None:
        monitor = PolicyMonitor()
        engine = PolicyEngine(providers=[DenyAllPolicy()])
        engine._monitor = monitor

        ctx = PolicyEvaluationContext(
            actor_id="test",
            action="call_tool",
            resource_id="admin",
        )
        await engine.evaluate(ctx)

        metrics = monitor.get_metrics()
        assert metrics["lifetime"]["total_denials"] == 1


# ── Constraint Enforcement Tests ──────────────────────────────


class TestConstraintEnforcement:
    """Tests for constraint enforcement in the middleware."""

    def test_max_args_constraint_import(self) -> None:
        """Verify the enforcement middleware imports cleanly."""
        from fastmcp.server.security.middleware.policy_enforcement import (
            PolicyEnforcementMiddleware,
        )

        assert PolicyEnforcementMiddleware is not None


# ── Orchestrator Integration Tests ────────────────────────────


class TestOrchestratorGovernanceWiring:
    """Tests for governance components wired through orchestrator."""

    def test_validator_wired(self) -> None:
        from fastmcp.server.security.config import PolicyConfig, SecurityConfig
        from fastmcp.server.security.orchestrator import SecurityOrchestrator

        config = SecurityConfig(
            policy=PolicyConfig(
                enable_validation=True,
            ),
        )
        ctx = SecurityOrchestrator.bootstrap(config)
        assert ctx.policy_validator is not None
        assert ctx.policy_engine is not None
        assert ctx.policy_engine.validator is not None

    def test_monitor_wired(self) -> None:
        from fastmcp.server.security.config import PolicyConfig, SecurityConfig
        from fastmcp.server.security.orchestrator import SecurityOrchestrator

        config = SecurityConfig(
            policy=PolicyConfig(
                enable_monitoring=True,
            ),
        )
        ctx = SecurityOrchestrator.bootstrap(config)
        assert ctx.policy_monitor is not None
        assert ctx.policy_engine is not None
        assert ctx.policy_engine.monitor is not None

    def test_governor_wired(self) -> None:
        from fastmcp.server.security.config import PolicyConfig, SecurityConfig
        from fastmcp.server.security.orchestrator import SecurityOrchestrator

        config = SecurityConfig(
            policy=PolicyConfig(
                enable_governance=True,
                enable_validation=True,
            ),
        )
        ctx = SecurityOrchestrator.bootstrap(config)
        assert ctx.policy_governor is not None

    def test_invariant_registry_wired(self) -> None:
        from fastmcp.server.security.config import PolicyConfig, SecurityConfig
        from fastmcp.server.security.orchestrator import SecurityOrchestrator

        registry = InvariantRegistry()
        config = SecurityConfig(
            policy=PolicyConfig(
                invariant_registry=registry,
            ),
        )
        ctx = SecurityOrchestrator.bootstrap(config)
        assert ctx.policy_engine is not None
        assert ctx.policy_engine.invariant_registry is registry

    def test_disabled_features_not_wired(self) -> None:
        from fastmcp.server.security.config import PolicyConfig, SecurityConfig
        from fastmcp.server.security.orchestrator import SecurityOrchestrator

        config = SecurityConfig(
            policy=PolicyConfig(
                enable_validation=False,
                enable_monitoring=False,
                enable_governance=False,
            ),
        )
        ctx = SecurityOrchestrator.bootstrap(config)
        assert ctx.policy_validator is None
        assert ctx.policy_monitor is None
        assert ctx.policy_governor is None


# ── SecurityAPI Integration Tests ─────────────────────────────


class TestSecurityAPIGovernance:
    """Tests for new governance/monitoring/validation API endpoints."""

    def _make_api(self, *, validator=True, monitor=True, governor=True):
        from fastmcp.server.security.http.api import SecurityAPI

        engine = PolicyEngine(providers=[AllowAllPolicy()])
        api = SecurityAPI(policy_engine=engine)

        if validator:
            api.policy_validator = PolicyValidator()
        if monitor:
            api.policy_monitor = PolicyMonitor()
        if governor:
            gov = PolicyGovernor(engine=engine, require_simulation=False)
            api.policy_governor = gov

        return api

    def test_validate_policy_success(self) -> None:
        api = self._make_api()
        result = api.validate_policy({"type": "allow_all"})
        assert result["valid"] is True

    def test_validate_policy_failure(self) -> None:
        api = self._make_api()
        result = api.validate_policy({"type": "banana"})
        assert result["valid"] is False

    def test_validate_providers(self) -> None:
        api = self._make_api()
        result = api.validate_providers()
        assert result["valid"] is True

    def test_get_metrics(self) -> None:
        api = self._make_api()
        assert api.policy_monitor is not None
        api.policy_monitor.record_decision(decision="allow", resource_id="x")
        metrics = api.get_policy_metrics()
        assert "window" in metrics
        assert "lifetime" in metrics

    def test_get_alerts(self) -> None:
        api = self._make_api()
        result = api.get_policy_alerts()
        assert "alerts" in result

    def test_get_governance_proposals(self) -> None:
        api = self._make_api()
        result = api.get_governance_proposals()
        assert result["total_proposals"] == 0

    def test_get_governance_proposal_not_found(self) -> None:
        api = self._make_api()
        result = api.get_governance_proposal("nonexistent")
        assert result["status"] == 404

    def test_not_configured_returns_503(self) -> None:
        from fastmcp.server.security.http.api import SecurityAPI

        api = SecurityAPI()
        assert api.validate_policy({})["status"] == 503
        assert api.get_policy_metrics()["status"] == 503
        assert api.get_policy_alerts()["status"] == 503
        assert api.get_governance_proposals()["status"] == 503

    def test_health_includes_new_components(self) -> None:
        api = self._make_api()
        health = api.get_health()
        assert health["components"].get("policy_validator") == "ok"
        assert health["components"].get("policy_monitor") == "ok"
        assert health["components"].get("policy_governor") == "ok"
