"""Tests for Security HTTP API.

Covers the SecurityAPI class and route-mounting logic for serving
dashboard, marketplace, compliance, trust, federation, CRL, provenance,
and health data over HTTP.
"""

from __future__ import annotations

import pytest

from fastmcp import FastMCP
from fastmcp.server.security import attach_security
from fastmcp.server.security.config import RegistryConfig, SecurityConfig
from fastmcp.server.security.http.api import SecurityAPI

# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture()
def empty_api():
    """API with no components configured."""
    return SecurityAPI()


@pytest.fixture()
def registry():
    from fastmcp.server.security.registry.registry import TrustRegistry

    return TrustRegistry()


@pytest.fixture()
def marketplace(registry):
    from fastmcp.server.security.gateway.tool_marketplace import (
        ToolCategory,
        ToolMarketplace,
    )

    mp = ToolMarketplace(trust_registry=registry)
    mp.publish(
        "test-tool",
        display_name="Test Tool",
        description="A test tool.",
        version="1.0.0",
        author="tester",
        categories={ToolCategory.SEARCH},
        tags={"test"},
        tool_license="MIT",
    )
    return mp


@pytest.fixture()
def compliance_reporter():
    from fastmcp.server.security.compliance.reports import (
        ComplianceFramework,
        ComplianceReporter,
    )

    framework = ComplianceFramework(name="TestFramework", version="1.0")
    return ComplianceReporter(framework=framework)


@pytest.fixture()
def provenance_ledger():
    from fastmcp.server.security.provenance.ledger import ProvenanceLedger
    from fastmcp.server.security.provenance.records import ProvenanceAction

    ledger = ProvenanceLedger()
    ledger.record(
        action=ProvenanceAction.TOOL_CALLED,
        actor_id="user-1",
        resource_id="test-tool",
        metadata={"args": {}},
    )
    return ledger


@pytest.fixture()
def federation():
    from fastmcp.server.security.federation.federation import TrustFederation

    fed = TrustFederation(federation_id="test-instance")
    fed.add_peer(
        "peer-1",
        endpoint="https://peer-1.example.com",
    )
    return fed


@pytest.fixture()
def crl():
    from fastmcp.server.security.federation.crl import CertificateRevocationList

    return CertificateRevocationList()


@pytest.fixture()
def event_bus():
    from fastmcp.server.security.alerts.bus import SecurityEventBus

    return SecurityEventBus()


@pytest.fixture()
def full_api(
    registry,
    marketplace,
    compliance_reporter,
    provenance_ledger,
    federation,
    crl,
    event_bus,
):
    from fastmcp.server.security.dashboard.snapshot import SecurityDashboard

    dashboard = SecurityDashboard(
        registry=registry,
        marketplace=marketplace,
        federation=federation,
        crl=crl,
        compliance_reporter=compliance_reporter,
        event_bus=event_bus,
    )
    return SecurityAPI(
        dashboard=dashboard,
        marketplace=marketplace,
        registry=registry,
        compliance_reporter=compliance_reporter,
        provenance_ledger=provenance_ledger,
        federation=federation,
        crl=crl,
        event_bus=event_bus,
    )


# ── Empty API tests ────────────────────────────────────────────


class TestEmptyAPI:
    def test_dashboard_not_configured(self, empty_api):
        result = empty_api.get_dashboard()
        assert result["error"] == "Dashboard not configured"
        assert result["status"] == 503

    def test_marketplace_not_configured(self, empty_api):
        result = empty_api.get_marketplace()
        assert result["error"] == "Marketplace not configured"
        assert result["status"] == 503

    def test_marketplace_listing_not_configured(self, empty_api):
        result = empty_api.get_marketplace_listing("some-id")
        assert result["error"] == "Marketplace not configured"
        assert result["status"] == 503

    def test_compliance_not_configured(self, empty_api):
        result = empty_api.get_compliance_report()
        assert result["error"] == "Compliance reporter not configured"
        assert result["status"] == 503

    def test_trust_registry_not_configured(self, empty_api):
        result = empty_api.get_trust_registry()
        assert result["error"] == "Registry not configured"
        assert result["status"] == 503

    def test_trust_score_not_configured(self, empty_api):
        result = empty_api.get_trust_score("some-tool")
        assert result["error"] == "Registry not configured"
        assert result["status"] == 503

    def test_federation_not_configured(self, empty_api):
        result = empty_api.get_federation_status()
        assert result["error"] == "Federation not configured"
        assert result["status"] == 503

    def test_revocations_not_configured(self, empty_api):
        result = empty_api.get_revocations()
        assert result["error"] == "CRL not configured"
        assert result["status"] == 503

    def test_is_revoked_not_configured(self, empty_api):
        result = empty_api.is_revoked("some-tool")
        assert result["error"] == "CRL not configured"
        assert result["status"] == 503

    def test_provenance_not_configured(self, empty_api):
        result = empty_api.get_provenance()
        assert result["error"] == "Provenance ledger not configured"
        assert result["status"] == 503

    def test_health_unconfigured(self, empty_api):
        result = empty_api.get_health()
        assert result["status"] == "unconfigured"
        assert result["component_count"] == 0
        assert result["components"] == {}
        assert "timestamp" in result


# ── Dashboard tests ────────────────────────────────────────────


class TestDashboard:
    def test_dashboard_returns_dict(self, full_api):
        result = full_api.get_dashboard()
        assert isinstance(result, dict)

    def test_dashboard_has_sections(self, full_api):
        result = full_api.get_dashboard()
        # DashboardDataBridge export keys
        assert (
            "trust_timeline" in result or "components" in result or "banner" in result
        )


# ── Marketplace tests ──────────────────────────────────────────


class TestMarketplace:
    def test_marketplace_returns_dict(self, full_api):
        result = full_api.get_marketplace()
        assert isinstance(result, dict)
        assert "listings" in result
        assert "stats" in result

    def test_marketplace_listing_found(self, full_api, marketplace):
        listing = marketplace.get_by_name("test-tool")
        assert listing is not None
        result = full_api.get_marketplace_listing(listing.listing_id)
        assert result["name"] == "test-tool"

    def test_marketplace_listing_not_found(self, full_api):
        result = full_api.get_marketplace_listing("nonexistent-id")
        assert result["error"] == "Listing not found"
        assert result["status"] == 404


# ── Compliance tests ───────────────────────────────────────────


class TestCompliance:
    def test_compliance_full_report(self, full_api):
        result = full_api.get_compliance_report("full")
        assert isinstance(result, dict)

    def test_compliance_summary_report(self, full_api):
        result = full_api.get_compliance_report("summary")
        assert isinstance(result, dict)

    def test_compliance_unknown_type_defaults_to_full(self, full_api):
        result = full_api.get_compliance_report("unknown")
        assert isinstance(result, dict)


# ── Trust Registry tests ──────────────────────────────────────


class TestTrustRegistry:
    def test_trust_registry_overview(self, full_api):
        result = full_api.get_trust_registry()
        assert "total_tools" in result
        assert "tools" in result
        assert "generated_at" in result

    def test_trust_registry_has_tools(self, full_api):
        result = full_api.get_trust_registry()
        # Marketplace publish auto-registers tools
        assert result["total_tools"] >= 1

    def test_trust_score_found(self, full_api):
        result = full_api.get_trust_score("test-tool")
        assert "overall" in result
        assert "tool_name" in result
        assert result["tool_name"] == "test-tool"

    def test_trust_score_not_found(self, full_api):
        result = full_api.get_trust_score("nonexistent-tool")
        assert result["status"] == 404
        assert "error" in result


# ── Federation tests ───────────────────────────────────────────


class TestFederation:
    def test_federation_status(self, full_api):
        result = full_api.get_federation_status()
        assert isinstance(result, dict)


# ── CRL tests ─────────────────────────────────────────────────


class TestCRL:
    def test_revocations_empty(self, full_api):
        result = full_api.get_revocations()
        assert result["total_entries"] == 0
        assert result["entries"] == []
        assert "generated_at" in result

    def test_is_revoked_false(self, full_api):
        result = full_api.is_revoked("test-tool")
        assert result["tool_name"] == "test-tool"
        assert result["is_revoked"] is False

    def test_revocations_after_revoke(self, full_api, crl):
        from fastmcp.server.security.federation.crl import RevocationReason

        crl.revoke("bad-tool", reason=RevocationReason.POLICY_VIOLATION)
        result = full_api.get_revocations()
        assert result["total_entries"] == 1

        check = full_api.is_revoked("bad-tool")
        assert check["is_revoked"] is True


# ── Provenance tests ──────────────────────────────────────────


class TestProvenance:
    def test_provenance_all(self, full_api):
        result = full_api.get_provenance()
        assert result["total_records"] >= 1
        assert "generated_at" in result

    def test_provenance_by_resource(self, full_api):
        result = full_api.get_provenance(resource_id="test-tool")
        assert result["total_records"] >= 1

    def test_provenance_by_actor(self, full_api):
        result = full_api.get_provenance(actor_id="user-1")
        assert result["total_records"] >= 1

    def test_provenance_no_results(self, full_api):
        result = full_api.get_provenance(resource_id="nonexistent")
        assert result["total_records"] == 0

    def test_provenance_limit(self, full_api):
        result = full_api.get_provenance(limit=1)
        assert result["total_records"] <= 1


# ── Health tests ──────────────────────────────────────────────


class TestHealth:
    def test_health_all_components(self, full_api):
        result = full_api.get_health()
        assert result["status"] == "healthy"
        assert result["component_count"] >= 7
        assert "dashboard" in result["components"]
        assert "marketplace" in result["components"]
        assert "registry" in result["components"]
        assert "compliance" in result["components"]
        assert "federation" in result["components"]
        assert "crl" in result["components"]
        assert "provenance" in result["components"]
        assert "event_bus" in result["components"]

    def test_health_partial(self, registry):
        api = SecurityAPI(registry=registry)
        result = api.get_health()
        assert result["status"] == "healthy"
        assert result["component_count"] == 1
        assert "registry" in result["components"]


# ── Import tests ──────────────────────────────────────────────


class TestImports:
    def test_import_from_http_package(self):
        from fastmcp.server.security.http import SecurityAPI, mount_security_routes

        assert SecurityAPI is not None
        assert mount_security_routes is not None

    def test_import_from_api_module(self):
        from fastmcp.server.security.http.api import SecurityAPI, mount_security_routes

        assert SecurityAPI is not None
        assert mount_security_routes is not None


# ── mount_security_routes tests ───────────────────────────────


class TestMountRoutes:
    def test_mount_returns_api(self):
        """mount_security_routes should return the SecurityAPI instance."""
        from fastmcp import FastMCP
        from fastmcp.server.security.http.api import SecurityAPI, mount_security_routes

        server = FastMCP("test-server")
        api = mount_security_routes(server, api=SecurityAPI())
        assert isinstance(api, SecurityAPI)

    def test_mount_with_custom_prefix(self):
        from fastmcp import FastMCP
        from fastmcp.server.security.http.api import SecurityAPI, mount_security_routes

        server = FastMCP("test-server")
        api = mount_security_routes(server, api=SecurityAPI(), prefix="/api/v1")
        assert isinstance(api, SecurityAPI)

    def test_mount_auto_build(self):
        """Without an explicit api arg, mount should still work (returns empty API)."""
        from fastmcp.server.security.http.api import mount_security_routes

        server = FastMCP("test-server")
        api = mount_security_routes(server)
        assert api is not None

    def test_mount_auto_build_uses_attached_context(self):
        from fastmcp.server.security.http.api import mount_security_routes

        server = FastMCP("test-server")
        attach_security(server, SecurityConfig(registry=RegistryConfig()))

        api = mount_security_routes(server)

        assert api.registry is not None


# ── from_context tests ──────────────────────────────────────


class TestFromContext:
    """Test SecurityAPI.from_context() factory method."""

    def test_from_full_context(self):
        from fastmcp.server.security.config import (
            AlertConfig,
            ComplianceConfig,
            CRLConfig,
            FederationConfig,
            ProvenanceConfig,
            RegistryConfig,
            SecurityConfig,
            ToolMarketplaceConfig,
        )
        from fastmcp.server.security.orchestrator import SecurityOrchestrator

        config = SecurityConfig(
            alerts=AlertConfig(),
            provenance=ProvenanceConfig(),
            registry=RegistryConfig(),
            tool_marketplace=ToolMarketplaceConfig(),
            federation=FederationConfig(),
            crl_config=CRLConfig(),
            compliance=ComplianceConfig(),
        )
        ctx = SecurityOrchestrator.bootstrap(config)
        api = SecurityAPI.from_context(ctx)

        assert api.dashboard is ctx.dashboard
        assert api.marketplace is ctx.tool_marketplace
        assert api.registry is ctx.registry
        assert api.compliance_reporter is ctx.compliance_reporter
        assert api.provenance_ledger is ctx.provenance_ledger
        assert api.federation is ctx.federation
        assert api.crl is ctx.crl
        assert api.event_bus is ctx.event_bus

    def test_from_partial_context(self):
        from fastmcp.server.security.config import RegistryConfig, SecurityConfig
        from fastmcp.server.security.orchestrator import SecurityOrchestrator

        config = SecurityConfig(registry=RegistryConfig())
        ctx = SecurityOrchestrator.bootstrap(config)
        api = SecurityAPI.from_context(ctx)

        assert api.registry is ctx.registry
        assert api.dashboard is ctx.dashboard
        assert api.marketplace is None
        assert api.federation is None

    def test_from_empty_context(self):
        from fastmcp.server.security.config import SecurityConfig
        from fastmcp.server.security.orchestrator import SecurityOrchestrator

        ctx = SecurityOrchestrator.bootstrap(SecurityConfig())
        api = SecurityAPI.from_context(ctx)

        assert api.dashboard is ctx.dashboard
        assert api.registry is None
        assert api.marketplace is None
