"""Tests for SecurityOrchestrator (Phase 10)."""

from __future__ import annotations

import pytest

from fastmcp import FastMCP
from fastmcp.server.security.alerts.bus import SecurityEventBus
from fastmcp.server.security.config import (
    AlertConfig,
    ConsentConfig,
    ContractConfig,
    GatewayConfig,
    PolicyConfig,
    ProvenanceConfig,
    ReflexiveConfig,
    SecurityConfig,
)
from fastmcp.server.security.consent.graph import ConsentGraph
from fastmcp.server.security.contracts.broker import ContextBroker
from fastmcp.server.security.gateway.audit import AuditAPI
from fastmcp.server.security.gateway.marketplace import Marketplace
from fastmcp.server.security.integration import (
    attach_security,
    get_security_context,
    register_security_gateway_tools,
)
from fastmcp.server.security.middleware.consent_enforcement import (
    ConsentEnforcementMiddleware,
)
from fastmcp.server.security.middleware.contract_validation import (
    ContractValidationMiddleware,
)
from fastmcp.server.security.middleware.policy_enforcement import (
    PolicyEnforcementMiddleware,
)
from fastmcp.server.security.middleware.provenance_recording import (
    ProvenanceRecordingMiddleware,
)
from fastmcp.server.security.middleware.reflexive import ReflexiveMiddleware
from fastmcp.server.security.orchestrator import SecurityContext, SecurityOrchestrator
from fastmcp.server.security.policy.engine import PolicyEngine
from fastmcp.server.security.policy.provider import AllowAllPolicy
from fastmcp.server.security.provenance.ledger import ProvenanceLedger
from fastmcp.server.security.reflexive.analyzer import (
    BehavioralAnalyzer,
    EscalationEngine,
)

# ---------------------------------------------------------------------------
# SecurityContext
# ---------------------------------------------------------------------------


class TestSecurityContext:
    def test_default_fields_are_none(self):
        ctx = SecurityContext(config=SecurityConfig())
        assert ctx.event_bus is None
        assert ctx.policy_engine is None
        assert ctx.broker is None
        assert ctx.provenance_ledger is None
        assert ctx.behavioral_analyzer is None
        assert ctx.escalation_engine is None
        assert ctx.consent_graph is None
        assert ctx.audit_api is None
        assert ctx.marketplace is None
        assert ctx.middleware == []
        assert ctx.gateway_tools == {}

    def test_stores_config(self):
        cfg = SecurityConfig()
        ctx = SecurityContext(config=cfg)
        assert ctx.config is cfg


# ---------------------------------------------------------------------------
# Empty / disabled config
# ---------------------------------------------------------------------------


class TestBootstrapEmpty:
    def test_empty_config_returns_empty_context(self):
        ctx = SecurityOrchestrator.bootstrap(SecurityConfig())
        assert ctx.middleware == []
        assert ctx.event_bus is None

    def test_disabled_config_returns_empty_context(self):
        cfg = SecurityConfig(
            policy=PolicyConfig(providers=[AllowAllPolicy()]),
            enabled=False,
        )
        ctx = SecurityOrchestrator.bootstrap(cfg)
        assert ctx.middleware == []
        assert ctx.policy_engine is None


# ---------------------------------------------------------------------------
# Individual layer bootstrapping
# ---------------------------------------------------------------------------


class TestBootstrapPolicy:
    def test_creates_policy_engine_and_middleware(self):
        cfg = SecurityConfig(
            policy=PolicyConfig(providers=[AllowAllPolicy()]),
        )
        ctx = SecurityOrchestrator.bootstrap(cfg)
        assert isinstance(ctx.policy_engine, PolicyEngine)
        assert len(ctx.middleware) == 1
        assert isinstance(ctx.middleware[0], PolicyEnforcementMiddleware)

    def test_uses_provided_engine(self):
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        cfg = SecurityConfig(
            policy=PolicyConfig(engine=engine),
        )
        ctx = SecurityOrchestrator.bootstrap(cfg)
        assert ctx.policy_engine is engine


class TestBootstrapContracts:
    def test_creates_broker_and_middleware(self):
        cfg = SecurityConfig(contracts=ContractConfig())
        ctx = SecurityOrchestrator.bootstrap(cfg, server_name="test-srv")
        assert isinstance(ctx.broker, ContextBroker)
        assert len(ctx.middleware) == 1
        assert isinstance(ctx.middleware[0], ContractValidationMiddleware)


class TestBootstrapProvenance:
    def test_creates_ledger_and_middleware(self):
        cfg = SecurityConfig(provenance=ProvenanceConfig())
        ctx = SecurityOrchestrator.bootstrap(cfg)
        assert isinstance(ctx.provenance_ledger, ProvenanceLedger)
        assert len(ctx.middleware) == 1
        assert isinstance(ctx.middleware[0], ProvenanceRecordingMiddleware)


class TestBootstrapReflexive:
    def test_creates_analyzer_escalation_and_middleware(self):
        cfg = SecurityConfig(reflexive=ReflexiveConfig())
        ctx = SecurityOrchestrator.bootstrap(cfg)
        assert isinstance(ctx.behavioral_analyzer, BehavioralAnalyzer)
        assert isinstance(ctx.escalation_engine, EscalationEngine)
        assert len(ctx.middleware) == 1
        assert isinstance(ctx.middleware[0], ReflexiveMiddleware)


class TestBootstrapConsent:
    def test_creates_graph_and_middleware(self):
        cfg = SecurityConfig(consent=ConsentConfig())
        ctx = SecurityOrchestrator.bootstrap(cfg)
        assert isinstance(ctx.consent_graph, ConsentGraph)
        assert len(ctx.middleware) == 1
        assert isinstance(ctx.middleware[0], ConsentEnforcementMiddleware)


class TestBootstrapGateway:
    def test_creates_marketplace_and_audit_api(self):
        cfg = SecurityConfig(gateway=GatewayConfig())
        ctx = SecurityOrchestrator.bootstrap(cfg)
        assert isinstance(ctx.marketplace, Marketplace)
        assert isinstance(ctx.audit_api, AuditAPI)
        # No middleware for gateway, but tools are created
        assert ctx.middleware == []
        assert len(ctx.gateway_tools) > 0

    def test_gateway_wires_existing_layers(self):
        cfg = SecurityConfig(
            provenance=ProvenanceConfig(),
            reflexive=ReflexiveConfig(),
            consent=ConsentConfig(),
            policy=PolicyConfig(providers=[AllowAllPolicy()]),
            gateway=GatewayConfig(),
        )
        ctx = SecurityOrchestrator.bootstrap(cfg)
        # AuditAPI should reference the same components
        assert ctx.audit_api is not None

    def test_register_tools_false_skips_tools(self):
        cfg = SecurityConfig(
            gateway=GatewayConfig(register_tools=False),
        )
        ctx = SecurityOrchestrator.bootstrap(cfg)
        assert ctx.gateway_tools == {}
        assert ctx.marketplace is not None


# ---------------------------------------------------------------------------
# Event bus wiring
# ---------------------------------------------------------------------------


class TestEventBusWiring:
    def test_alert_config_creates_event_bus(self):
        cfg = SecurityConfig(alerts=AlertConfig())
        ctx = SecurityOrchestrator.bootstrap(cfg)
        assert isinstance(ctx.event_bus, SecurityEventBus)

    def test_provided_event_bus_is_used(self):
        bus = SecurityEventBus()
        cfg = SecurityConfig(alerts=AlertConfig(event_bus=bus))
        ctx = SecurityOrchestrator.bootstrap(cfg)
        assert ctx.event_bus is bus

    def test_event_bus_injected_into_policy_engine(self):
        cfg = SecurityConfig(
            policy=PolicyConfig(providers=[AllowAllPolicy()]),
            alerts=AlertConfig(),
        )
        ctx = SecurityOrchestrator.bootstrap(cfg)
        assert ctx.policy_engine is not None
        assert ctx.policy_engine._event_bus is ctx.event_bus

    def test_event_bus_injected_into_provenance_ledger(self):
        cfg = SecurityConfig(
            provenance=ProvenanceConfig(),
            alerts=AlertConfig(),
        )
        ctx = SecurityOrchestrator.bootstrap(cfg)
        assert ctx.provenance_ledger is not None
        assert ctx.provenance_ledger._event_bus is ctx.event_bus

    def test_event_bus_injected_into_analyzer_and_escalation(self):
        cfg = SecurityConfig(
            reflexive=ReflexiveConfig(),
            alerts=AlertConfig(),
        )
        ctx = SecurityOrchestrator.bootstrap(cfg)
        assert ctx.behavioral_analyzer is not None
        assert ctx.behavioral_analyzer._event_bus is ctx.event_bus
        assert ctx.escalation_engine is not None
        assert ctx.escalation_engine._event_bus is ctx.event_bus

    def test_event_bus_injected_into_consent_graph(self):
        cfg = SecurityConfig(
            consent=ConsentConfig(),
            alerts=AlertConfig(),
        )
        ctx = SecurityOrchestrator.bootstrap(cfg)
        assert ctx.consent_graph is not None
        assert ctx.consent_graph._event_bus is ctx.event_bus

    def test_event_bus_injected_into_marketplace(self):
        cfg = SecurityConfig(
            gateway=GatewayConfig(),
            alerts=AlertConfig(),
        )
        ctx = SecurityOrchestrator.bootstrap(cfg)
        assert ctx.marketplace is not None
        assert ctx.marketplace._event_bus is ctx.event_bus

    def test_propagate_false_skips_injection(self):
        cfg = SecurityConfig(
            policy=PolicyConfig(providers=[AllowAllPolicy()]),
            provenance=ProvenanceConfig(),
            alerts=AlertConfig(propagate_to_components=False),
        )
        ctx = SecurityOrchestrator.bootstrap(cfg)
        assert ctx.event_bus is not None
        assert ctx.policy_engine is not None
        assert ctx.policy_engine._event_bus is None
        assert ctx.provenance_ledger is not None
        assert ctx.provenance_ledger._event_bus is None

    def test_no_alerts_config_means_no_injection(self):
        cfg = SecurityConfig(
            policy=PolicyConfig(providers=[AllowAllPolicy()]),
        )
        ctx = SecurityOrchestrator.bootstrap(cfg)
        assert ctx.event_bus is None
        # Engine should still work, just without bus
        assert ctx.policy_engine is not None


# ---------------------------------------------------------------------------
# Full stack bootstrap
# ---------------------------------------------------------------------------


class TestFullStackBootstrap:
    def test_all_layers_enabled(self):
        cfg = SecurityConfig(
            policy=PolicyConfig(providers=[AllowAllPolicy()]),
            contracts=ContractConfig(),
            provenance=ProvenanceConfig(),
            reflexive=ReflexiveConfig(),
            consent=ConsentConfig(),
            gateway=GatewayConfig(),
            alerts=AlertConfig(),
        )
        ctx = SecurityOrchestrator.bootstrap(cfg, server_name="full-test")

        assert ctx.policy_engine is not None
        assert ctx.broker is not None
        assert ctx.provenance_ledger is not None
        assert ctx.behavioral_analyzer is not None
        assert ctx.escalation_engine is not None
        assert ctx.consent_graph is not None
        assert ctx.marketplace is not None
        assert ctx.audit_api is not None
        assert ctx.event_bus is not None
        assert len(ctx.gateway_tools) > 0

        # Middleware order: policy, contracts, provenance, reflexive, consent
        assert len(ctx.middleware) == 5
        assert isinstance(ctx.middleware[0], PolicyEnforcementMiddleware)
        assert isinstance(ctx.middleware[1], ContractValidationMiddleware)
        assert isinstance(ctx.middleware[2], ProvenanceRecordingMiddleware)
        assert isinstance(ctx.middleware[3], ReflexiveMiddleware)
        assert isinstance(ctx.middleware[4], ConsentEnforcementMiddleware)

    def test_all_components_share_same_event_bus(self):
        cfg = SecurityConfig(
            policy=PolicyConfig(providers=[AllowAllPolicy()]),
            provenance=ProvenanceConfig(),
            reflexive=ReflexiveConfig(),
            consent=ConsentConfig(),
            gateway=GatewayConfig(),
            alerts=AlertConfig(),
        )
        ctx = SecurityOrchestrator.bootstrap(cfg)
        bus = ctx.event_bus
        assert bus is not None
        assert ctx.policy_engine._event_bus is bus
        assert ctx.provenance_ledger._event_bus is bus
        assert ctx.behavioral_analyzer._event_bus is bus
        assert ctx.escalation_engine._event_bus is bus
        assert ctx.consent_graph._event_bus is bus
        assert ctx.marketplace._event_bus is bus


# ---------------------------------------------------------------------------
# Server integration helpers
# ---------------------------------------------------------------------------


class TestServerIntegration:
    def test_attach_security_returns_context(self):
        cfg = SecurityConfig(
            policy=PolicyConfig(providers=[AllowAllPolicy()]),
        )
        mcp = FastMCP("test")

        ctx = attach_security(mcp, cfg)

        assert isinstance(ctx, SecurityContext)
        assert get_security_context(mcp) is ctx

    def test_attached_context_exposes_component_refs(self):
        cfg = SecurityConfig(
            provenance=ProvenanceConfig(),
            reflexive=ReflexiveConfig(),
            consent=ConsentConfig(),
            gateway=GatewayConfig(),
        )
        mcp = FastMCP("test")

        ctx = attach_security(mcp, cfg)

        assert isinstance(ctx.provenance_ledger, ProvenanceLedger)
        assert isinstance(ctx.behavioral_analyzer, BehavioralAnalyzer)
        assert isinstance(ctx.escalation_engine, EscalationEngine)
        assert isinstance(ctx.consent_graph, ConsentGraph)
        assert isinstance(ctx.audit_api, AuditAPI)
        assert isinstance(ctx.marketplace, Marketplace)

    def test_get_security_context_returns_none_when_unattached(self):
        mcp = FastMCP("test")
        assert get_security_context(mcp) is None

    def test_attach_security_registers_middleware(self):
        cfg = SecurityConfig(
            policy=PolicyConfig(providers=[AllowAllPolicy()]),
            consent=ConsentConfig(),
        )
        mcp = FastMCP("test")

        attach_security(mcp, cfg)

        policy_mw = [
            m for m in mcp.middleware if isinstance(m, PolicyEnforcementMiddleware)
        ]
        consent_mw = [
            m for m in mcp.middleware if isinstance(m, ConsentEnforcementMiddleware)
        ]
        assert len(policy_mw) == 1
        assert len(consent_mw) == 1

    def test_attach_security_with_alerts_wires_bus(self):
        cfg = SecurityConfig(
            policy=PolicyConfig(providers=[AllowAllPolicy()]),
            alerts=AlertConfig(),
        )
        mcp = FastMCP("test")

        ctx = attach_security(mcp, cfg)

        assert ctx.event_bus is not None
        assert get_security_context(mcp) is ctx

    def test_attach_security_rejects_duplicate_attachment(self):
        cfg = SecurityConfig(
            policy=PolicyConfig(providers=[AllowAllPolicy()]),
        )
        mcp = FastMCP("test")

        attach_security(mcp, cfg)

        with pytest.raises(RuntimeError, match="already attached"):
            attach_security(mcp, cfg)

    async def test_register_security_gateway_tools_is_explicit_and_idempotent(self):
        cfg = SecurityConfig(gateway=GatewayConfig())
        mcp = FastMCP("test")

        ctx = attach_security(mcp, cfg)
        registered = register_security_gateway_tools(mcp)

        assert set(registered) == set(ctx.gateway_tools)

        tools = await mcp.list_tools()
        tool_names = {tool.name for tool in tools}
        assert set(registered).issubset(tool_names)

        assert register_security_gateway_tools(mcp) == []


# ---------------------------------------------------------------------------
# bypass_stdio parameter
# ---------------------------------------------------------------------------


class TestBypassStdio:
    def test_bypass_stdio_true(self):
        cfg = SecurityConfig(
            policy=PolicyConfig(providers=[AllowAllPolicy()]),
        )
        ctx = SecurityOrchestrator.bootstrap(cfg, bypass_stdio=True)
        mw = ctx.middleware[0]
        assert isinstance(mw, PolicyEnforcementMiddleware)
        assert mw.bypass_stdio is True

    def test_bypass_stdio_false(self):
        cfg = SecurityConfig(
            policy=PolicyConfig(providers=[AllowAllPolicy()]),
        )
        ctx = SecurityOrchestrator.bootstrap(cfg, bypass_stdio=False)
        mw = ctx.middleware[0]
        assert isinstance(mw, PolicyEnforcementMiddleware)
        assert mw.bypass_stdio is False
