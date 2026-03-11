"""Integration tests for policy wiring — audit log, declarative loading, API endpoints."""

from __future__ import annotations

import pytest

from fastmcp.server.security.config import (
    AlertConfig,
    PolicyConfig,
    SecurityConfig,
)
from fastmcp.server.security.http.api import SecurityAPI
from fastmcp.server.security.orchestrator import SecurityOrchestrator
from fastmcp.server.security.policy.audit import PolicyAuditLog
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

# ── Engine + AuditLog wiring ────────────────────────────────────────


class TestEngineAuditLogWiring:
    @pytest.mark.anyio
    async def test_engine_auto_records_allow(self):
        audit = PolicyAuditLog()
        engine = PolicyEngine(providers=[AllowAllPolicy()], audit_log=audit)

        ctx = PolicyEvaluationContext(
            actor_id="agent-1", action="call_tool", resource_id="tool-a"
        )
        result = await engine.evaluate(ctx)

        assert result.decision == PolicyDecision.ALLOW
        assert audit.size == 1
        entry = audit.query(limit=1)[0]
        assert entry.actor_id == "agent-1"
        assert entry.resource_id == "tool-a"
        assert entry.decision == PolicyDecision.ALLOW

    @pytest.mark.anyio
    async def test_engine_auto_records_deny(self):
        audit = PolicyAuditLog()
        engine = PolicyEngine(providers=[DenyAllPolicy()], audit_log=audit)

        ctx = PolicyEvaluationContext(
            actor_id="agent-1", action="call_tool", resource_id="tool-x"
        )
        result = await engine.evaluate(ctx)

        assert result.decision == PolicyDecision.DENY
        assert audit.size == 1
        entry = audit.query(limit=1)[0]
        assert entry.decision == PolicyDecision.DENY

    @pytest.mark.anyio
    async def test_engine_records_timing(self):
        audit = PolicyAuditLog()
        engine = PolicyEngine(providers=[AllowAllPolicy()], audit_log=audit)

        ctx = PolicyEvaluationContext(
            actor_id="x", action="call_tool", resource_id="tool-a"
        )
        await engine.evaluate(ctx)

        entry = audit.query(limit=1)[0]
        assert entry.elapsed_ms >= 0

    @pytest.mark.anyio
    async def test_engine_no_audit_log_is_fine(self):
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        ctx = PolicyEvaluationContext(
            actor_id="x", action="call_tool", resource_id="tool-a"
        )
        result = await engine.evaluate(ctx)
        assert result.decision == PolicyDecision.ALLOW

    @pytest.mark.anyio
    async def test_engine_audit_log_property(self):
        audit = PolicyAuditLog()
        engine = PolicyEngine(providers=[AllowAllPolicy()], audit_log=audit)
        assert engine.audit_log is audit

    @pytest.mark.anyio
    async def test_engine_records_fail_closed(self):
        """When a provider raises an exception with fail_closed, audit records DENY."""

        class BrokenPolicy:
            def evaluate(self, ctx):
                raise RuntimeError("boom")

            def get_policy_id(self):
                return "broken"

            def get_policy_version(self):
                return "1.0"

        audit = PolicyAuditLog()
        engine = PolicyEngine(
            providers=[BrokenPolicy()], audit_log=audit, fail_closed=True
        )
        ctx = PolicyEvaluationContext(
            actor_id="x", action="call_tool", resource_id="tool-a"
        )
        result = await engine.evaluate(ctx)

        assert result.decision == PolicyDecision.DENY
        assert audit.size == 1
        assert audit.query(limit=1)[0].decision == PolicyDecision.DENY

    @pytest.mark.anyio
    async def test_multiple_evaluations_recorded(self):
        audit = PolicyAuditLog()
        engine = PolicyEngine(
            providers=[AllowlistPolicy(allowed={"safe-*"})], audit_log=audit
        )

        for rid in ["safe-tool", "unsafe-tool", "safe-other"]:
            ctx = PolicyEvaluationContext(
                actor_id="a", action="call_tool", resource_id=rid
            )
            await engine.evaluate(ctx)

        assert audit.size == 3
        assert audit.total_allowed == 2
        assert audit.total_denied == 1


# ── Orchestrator wiring ─────────────────────────────────────────────


class TestOrchestratorPolicyWiring:
    def test_bootstrap_creates_audit_log(self):
        config = SecurityConfig(
            policy=PolicyConfig(providers=[AllowAllPolicy()]),
        )
        ctx = SecurityOrchestrator.bootstrap(config)

        assert ctx.policy_engine is not None
        assert ctx.policy_audit_log is not None
        assert ctx.policy_engine.audit_log is ctx.policy_audit_log

    def test_bootstrap_uses_provided_audit_log(self):
        custom_audit = PolicyAuditLog(max_entries=500)
        config = SecurityConfig(
            policy=PolicyConfig(
                providers=[AllowAllPolicy()],
                audit_log=custom_audit,
            ),
        )
        ctx = SecurityOrchestrator.bootstrap(config)

        assert ctx.policy_audit_log is custom_audit
        assert ctx.policy_engine.audit_log is custom_audit

    @pytest.mark.anyio
    async def test_orchestrated_engine_records_to_audit(self):
        config = SecurityConfig(
            policy=PolicyConfig(
                providers=[DenylistPolicy(denied={"blocked"})],
            ),
        )
        ctx = SecurityOrchestrator.bootstrap(config)

        eval_ctx = PolicyEvaluationContext(
            actor_id="agent-1", action="call_tool", resource_id="blocked"
        )
        await ctx.policy_engine.evaluate(eval_ctx)

        assert ctx.policy_audit_log.size == 1
        assert ctx.policy_audit_log.total_denied == 1

    def test_bootstrap_with_event_bus_and_audit(self):
        config = SecurityConfig(
            policy=PolicyConfig(providers=[AllowAllPolicy()]),
            alerts=AlertConfig(),
        )
        ctx = SecurityOrchestrator.bootstrap(config)

        assert ctx.event_bus is not None
        assert ctx.policy_engine._event_bus is ctx.event_bus
        assert ctx.policy_audit_log is not None

    def test_bootstrap_declarative_policy_file(self):
        config = SecurityConfig(
            policy=PolicyConfig(
                policy_file={
                    "type": "allowlist",
                    "allowed": ["safe-*"],
                },
            ),
        )
        ctx = SecurityOrchestrator.bootstrap(config)

        assert ctx.policy_engine is not None
        assert len(ctx.policy_engine.providers) == 1

    def test_bootstrap_declarative_with_extra_providers(self):
        config = SecurityConfig(
            policy=PolicyConfig(
                providers=[DenylistPolicy(denied={"blocked"})],
                policy_file={
                    "type": "allowlist",
                    "allowed": ["safe-*"],
                },
            ),
        )
        ctx = SecurityOrchestrator.bootstrap(config)

        # Declarative provider is prepended, so there should be 2 providers
        assert len(ctx.policy_engine.providers) == 2


# ── SecurityAPI wiring ───────────────────────────────────────────────


class TestSecurityAPIWiring:
    def test_from_context_includes_policy(self):
        config = SecurityConfig(
            policy=PolicyConfig(providers=[AllowAllPolicy()]),
        )
        ctx = SecurityOrchestrator.bootstrap(config)
        api = SecurityAPI.from_context(ctx)

        assert api.policy_engine is ctx.policy_engine
        assert api.policy_audit_log is ctx.policy_audit_log

    def test_get_policy_status(self):
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        api = SecurityAPI(policy_engine=engine)
        status = api.get_policy_status()

        assert status["provider_count"] == 1
        assert status["evaluation_count"] == 0
        assert status["fail_closed"] is True
        assert "generated_at" in status
        assert status["providers"][0]["type"] == "AllowAllPolicy"
        assert status["has_audit_log"] is False

    def test_get_policy_status_unconfigured(self):
        api = SecurityAPI()
        status = api.get_policy_status()
        assert status["status"] == 503

    @pytest.mark.anyio
    async def test_get_policy_audit(self):
        audit = PolicyAuditLog()
        engine = PolicyEngine(providers=[AllowAllPolicy()], audit_log=audit)

        # Record some evaluations
        for i in range(3):
            ctx = PolicyEvaluationContext(
                actor_id="a", action="call_tool", resource_id=f"tool-{i}"
            )
            await engine.evaluate(ctx)

        api = SecurityAPI(policy_engine=engine, policy_audit_log=audit)
        result = api.get_policy_audit(limit=10)

        assert result["total_entries"] == 3
        assert len(result["entries"]) == 3

    @pytest.mark.anyio
    async def test_get_policy_audit_filtered(self):
        audit = PolicyAuditLog()
        engine = PolicyEngine(
            providers=[AllowlistPolicy(allowed={"safe-*"})], audit_log=audit
        )

        for rid in ["safe-tool", "blocked-tool"]:
            ctx = PolicyEvaluationContext(
                actor_id="a", action="call_tool", resource_id=rid
            )
            await engine.evaluate(ctx)

        api = SecurityAPI(policy_engine=engine, policy_audit_log=audit)

        denied = api.get_policy_audit(decision="deny")
        assert denied["total_entries"] == 1
        assert denied["entries"][0]["resource_id"] == "blocked-tool"

    def test_get_policy_audit_statistics(self):
        audit = PolicyAuditLog()
        api = SecurityAPI(policy_audit_log=audit)
        stats = api.get_policy_audit_statistics()

        assert stats["entries_in_log"] == 0
        assert stats["total_recorded"] == 0

    @pytest.mark.anyio
    async def test_simulate_policy(self):
        engine = PolicyEngine(providers=[AllowlistPolicy(allowed={"tool-a"})])
        api = SecurityAPI(policy_engine=engine)

        scenarios = [
            {"resource_id": "tool-a", "label": "allowed"},
            {"resource_id": "tool-b", "label": "denied"},
        ]
        result = await api.simulate_policy(scenarios)

        assert result["total"] == 2
        assert result["allowed"] == 1
        assert result["denied"] == 1

    def test_get_policy_schema(self):
        api = SecurityAPI()
        schema = api.get_policy_schema()

        assert "policy_types" in schema
        assert "compositions" in schema

    def test_health_includes_policy(self):
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        audit = PolicyAuditLog()
        api = SecurityAPI(policy_engine=engine, policy_audit_log=audit)
        health = api.get_health()

        assert "policy_engine" in health["components"]
        assert "policy_audit_log" in health["components"]
        assert health["component_count"] >= 2


# ── PolicyConfig tests ──────────────────────────────────────────────


class TestPolicyConfigEnhancements:
    def test_get_audit_log_default(self):
        config = PolicyConfig()
        audit = config.get_audit_log()
        assert isinstance(audit, PolicyAuditLog)
        assert audit.max_entries == 10_000

    def test_get_audit_log_custom_max(self):
        config = PolicyConfig(audit_max_entries=500)
        audit = config.get_audit_log()
        assert audit.max_entries == 500

    def test_get_audit_log_provided(self):
        custom = PolicyAuditLog(max_entries=100)
        config = PolicyConfig(audit_log=custom)
        assert config.get_audit_log() is custom

    def test_get_engine_with_audit_log(self):
        audit = PolicyAuditLog()
        config = PolicyConfig(providers=[AllowAllPolicy()])
        engine = config.get_engine(audit_log=audit)
        assert engine.audit_log is audit

    def test_get_engine_with_declarative_file(self):
        config = PolicyConfig(
            policy_file={"type": "allowlist", "allowed": ["*"]},
        )
        engine = config.get_engine()
        assert len(engine.providers) == 1

    def test_get_engine_declarative_prepended_to_providers(self):
        config = PolicyConfig(
            providers=[DenyAllPolicy()],
            policy_file={"type": "allowlist", "allowed": ["safe-*"]},
        )
        engine = config.get_engine()
        # Declarative is prepended, DenyAll is second
        assert len(engine.providers) == 2

    def test_get_engine_injects_audit_into_existing(self):
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        assert engine.audit_log is None

        audit = PolicyAuditLog()
        config = PolicyConfig(engine=engine)
        returned = config.get_engine(audit_log=audit)

        assert returned is engine
        assert returned.audit_log is audit


# ── End-to-end: config → orchestrator → API ──────────────────────────


class TestEndToEnd:
    @pytest.mark.anyio
    async def test_full_pipeline(self):
        """Config → Orchestrator → Engine → Audit → API → Query."""
        config = SecurityConfig(
            policy=PolicyConfig(
                providers=[
                    DenylistPolicy(denied={"blocked-*"}),
                    AllowlistPolicy(allowed={"safe-*"}),
                ],
            ),
            alerts=AlertConfig(),
        )
        ctx = SecurityOrchestrator.bootstrap(config)
        api = SecurityAPI.from_context(ctx)

        # Evaluate some requests
        for rid in ["safe-tool", "blocked-tool", "safe-other", "unknown"]:
            eval_ctx = PolicyEvaluationContext(
                actor_id="test-agent", action="call_tool", resource_id=rid
            )
            await ctx.policy_engine.evaluate(eval_ctx)

        # Check audit log via API
        all_entries = api.get_policy_audit(limit=100)
        assert all_entries["total_entries"] == 4

        denied = api.get_policy_audit(decision="deny")
        assert denied["total_entries"] == 2  # blocked-tool + unknown

        allowed = api.get_policy_audit(decision="allow")
        assert allowed["total_entries"] == 2  # safe-tool + safe-other

        # Check stats
        stats = api.get_policy_audit_statistics()
        assert stats["total_recorded"] == 4
        assert stats["total_allowed"] == 2
        assert stats["total_denied"] == 2

        # Check policy status
        status = api.get_policy_status()
        assert status["evaluation_count"] == 4
        assert status["deny_count"] == 2
        assert status["provider_count"] == 2

        # Simulate
        sim_result = await api.simulate_policy(
            [
                {"resource_id": "safe-new", "label": "should allow"},
                {"resource_id": "blocked-new", "label": "should deny"},
            ]
        )
        assert sim_result["allowed"] == 1
        assert sim_result["denied"] == 1

        # Health
        health = api.get_health()
        assert "policy_engine" in health["components"]
        assert "policy_audit_log" in health["components"]
        assert "event_bus" in health["components"]

    @pytest.mark.anyio
    async def test_declarative_end_to_end(self):
        """Declarative YAML policy loaded through config pipeline."""
        config = SecurityConfig(
            policy=PolicyConfig(
                policy_file={
                    "composition": "all_of",
                    "policies": [
                        {"type": "allowlist", "allowed": ["safe-*"]},
                        {"type": "denylist", "denied": ["safe-but-blocked"]},
                    ],
                },
            ),
        )
        ctx = SecurityOrchestrator.bootstrap(config)

        # safe-tool → allowed
        r1 = await ctx.policy_engine.evaluate(
            PolicyEvaluationContext(
                actor_id="a", action="call_tool", resource_id="safe-tool"
            )
        )
        assert r1.decision == PolicyDecision.ALLOW

        # safe-but-blocked → denied (denylist overrides)
        r2 = await ctx.policy_engine.evaluate(
            PolicyEvaluationContext(
                actor_id="a", action="call_tool", resource_id="safe-but-blocked"
            )
        )
        assert r2.decision == PolicyDecision.DENY

        # unknown → denied (not in allowlist)
        r3 = await ctx.policy_engine.evaluate(
            PolicyEvaluationContext(
                actor_id="a", action="call_tool", resource_id="unknown"
            )
        )
        assert r3.decision == PolicyDecision.DENY

        # All recorded in audit
        assert ctx.policy_audit_log.size == 3
