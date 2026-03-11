"""Tests for Dashboard Data Bridge.

Covers the DashboardDataBridge class that converts SecurityDashboard
backend state into React-dashboard-compatible JSON.
"""

from __future__ import annotations

import json

import pytest

from fastmcp.server.security.compliance.frameworks import (
    ComplianceFramework,
    ComplianceRequirement,
    ComplianceStatus,
    RequirementCategory,
)
from fastmcp.server.security.compliance.reports import ComplianceReporter
from fastmcp.server.security.dashboard.data_bridge import DashboardDataBridge
from fastmcp.server.security.dashboard.snapshot import (
    DashboardSnapshot,
    HealthLevel,
    SecurityDashboard,
    TimelineEntry,
    TimelineType,
)
from fastmcp.server.security.federation.crl import CertificateRevocationList
from fastmcp.server.security.federation.federation import TrustFederation
from fastmcp.server.security.gateway.tool_marketplace import ToolMarketplace
from fastmcp.server.security.registry.registry import TrustRegistry
from fastmcp.server.security.sandbox.enforcer import SandboxedRunner

# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture()
def empty_dashboard():
    return SecurityDashboard()


@pytest.fixture()
def populated_dashboard():
    registry = TrustRegistry()
    registry.register("tool-alpha", attestation=None)
    registry.register("tool-beta", attestation=None)

    marketplace = ToolMarketplace()
    marketplace.publish("tool-alpha", display_name="Tool Alpha")

    federation = TrustFederation()
    federation.add_peer("partner-eu")

    crl = CertificateRevocationList()
    sandbox = SandboxedRunner()

    fw = ComplianceFramework(name="TestFW")
    reporter = ComplianceReporter(framework=fw)

    dash = SecurityDashboard(
        registry=registry,
        marketplace=marketplace,
        federation=federation,
        crl=crl,
        sandbox_runner=sandbox,
        compliance_reporter=reporter,
    )

    # Add some timeline entries
    dash.add_timeline_entry(
        TimelineEntry(
            title="Tool certified: tool-alpha",
            entry_type=TimelineType.CERTIFICATION,
            severity="info",
        )
    )
    dash.add_timeline_entry(
        TimelineEntry(
            title="Sandbox violation detected",
            entry_type=TimelineType.VIOLATION,
            severity="warning",
        )
    )
    dash.add_timeline_entry(
        TimelineEntry(
            title="Compliance report generated",
            entry_type=TimelineType.COMPLIANCE,
            severity="info",
        )
    )

    return dash


# ── Empty dashboard tests ───────────────────────────────────────


class TestEmptyDashboard:
    def test_export_returns_dict(self, empty_dashboard):
        bridge = DashboardDataBridge(dashboard=empty_dashboard)
        data = bridge.export()
        assert isinstance(data, dict)

    def test_export_has_all_keys(self, empty_dashboard):
        bridge = DashboardDataBridge(dashboard=empty_dashboard)
        data = bridge.export()
        expected_keys = {
            "trust_timeline",
            "compliance_data",
            "tool_categories",
            "components",
            "timeline_events",
            "top_tools",
            "health_banner",
            "generated_at",
        }
        assert expected_keys == set(data.keys())

    def test_export_json_is_valid(self, empty_dashboard):
        bridge = DashboardDataBridge(dashboard=empty_dashboard)
        json_str = bridge.export_json()
        parsed = json.loads(json_str)
        assert "trust_timeline" in parsed

    def test_empty_timeline_events(self, empty_dashboard):
        bridge = DashboardDataBridge(dashboard=empty_dashboard)
        data = bridge.export()
        assert data["timeline_events"] == []

    def test_empty_top_tools(self, empty_dashboard):
        bridge = DashboardDataBridge(dashboard=empty_dashboard)
        data = bridge.export()
        assert data["top_tools"] == []

    def test_empty_health_banner(self, empty_dashboard):
        bridge = DashboardDataBridge(dashboard=empty_dashboard)
        data = bridge.export()
        banner = data["health_banner"]
        assert banner["overall_health"] == "unknown"
        assert banner["total_tools"] == 0


# ── Populated dashboard tests ──────────────────────────────────


class TestPopulatedDashboard:
    def test_trust_timeline_has_entries(self, populated_dashboard):
        bridge = DashboardDataBridge(dashboard=populated_dashboard)
        timeline = bridge.build_trust_timeline()
        assert len(timeline) >= 1
        assert "time" in timeline[0]
        assert "score" in timeline[0]

    def test_compliance_data(self, populated_dashboard):
        bridge = DashboardDataBridge(dashboard=populated_dashboard)
        data = bridge.build_compliance_data()
        assert len(data) >= 1
        assert "name" in data[0]
        assert "score" in data[0]
        assert "total" in data[0]
        assert "passed" in data[0]

    def test_tool_categories(self, populated_dashboard):
        bridge = DashboardDataBridge(dashboard=populated_dashboard)
        cats = bridge.build_tool_categories()
        assert len(cats) >= 1
        assert "name" in cats[0]
        assert "value" in cats[0]
        assert "color" in cats[0]

    def test_components(self, populated_dashboard):
        bridge = DashboardDataBridge(dashboard=populated_dashboard)
        comps = bridge.build_components()
        assert len(comps) == 5  # registry, marketplace, federation, sandbox, CRL
        names = {c["name"] for c in comps}
        assert "Trust Registry" in names
        assert "Marketplace" in names

    def test_component_status(self, populated_dashboard):
        bridge = DashboardDataBridge(dashboard=populated_dashboard)
        comps = bridge.build_components()
        for comp in comps:
            assert comp["status"] in ("healthy", "degraded", "critical", "unknown")
            assert "icon" in comp

    def test_timeline_events(self, populated_dashboard):
        bridge = DashboardDataBridge(dashboard=populated_dashboard)
        events = bridge.build_timeline_events()
        assert len(events) == 3
        assert events[0]["type"] in (
            "certification",
            "violation",
            "compliance",
            "system",
        )

    def test_timeline_event_structure(self, populated_dashboard):
        bridge = DashboardDataBridge(dashboard=populated_dashboard)
        events = bridge.build_timeline_events()
        for event in events:
            assert "id" in event
            assert "type" in event
            assert "title" in event
            assert "severity" in event
            assert "time" in event
            assert "icon" in event

    def test_top_tools(self, populated_dashboard):
        bridge = DashboardDataBridge(dashboard=populated_dashboard)
        tools = bridge.build_top_tools()
        assert len(tools) == 2  # tool-alpha, tool-beta
        assert tools[0]["name"] in ("tool-alpha", "tool-beta")

    def test_top_tools_sorted_by_trust(self, populated_dashboard):
        bridge = DashboardDataBridge(dashboard=populated_dashboard)
        tools = bridge.build_top_tools()
        if len(tools) >= 2:
            assert tools[0]["trust"] >= tools[1]["trust"]

    def test_top_tools_structure(self, populated_dashboard):
        bridge = DashboardDataBridge(dashboard=populated_dashboard)
        tools = bridge.build_top_tools()
        for tool in tools:
            assert "name" in tool
            assert "trust" in tool
            assert "certified" in tool
            assert "installs" in tool
            assert "rating" in tool
            assert "status" in tool

    def test_health_banner(self, populated_dashboard):
        bridge = DashboardDataBridge(dashboard=populated_dashboard)
        banner = bridge.build_health_banner()
        assert banner["overall_health"] in (
            "healthy",
            "degraded",
            "critical",
            "unknown",
        )
        assert "health_label" in banner
        assert isinstance(banner["total_tools"], int)
        assert isinstance(banner["compliance_score"], (int, float))

    def test_health_banner_operational(self, populated_dashboard):
        bridge = DashboardDataBridge(dashboard=populated_dashboard)
        banner = bridge.build_health_banner()
        # All components configured and healthy = operational
        assert isinstance(banner["all_operational"], bool)

    def test_full_export(self, populated_dashboard):
        bridge = DashboardDataBridge(dashboard=populated_dashboard)
        data = bridge.export()
        assert len(data["components"]) == 5
        assert len(data["timeline_events"]) == 3
        assert len(data["top_tools"]) == 2
        assert data["health_banner"]["total_tools"] == 2


# ── Snapshot caching tests ──────────────────────────────────────


class TestSnapshotCaching:
    def test_snapshot_cached_after_export(self, populated_dashboard):
        bridge = DashboardDataBridge(dashboard=populated_dashboard)
        assert bridge.snapshot is None
        bridge.export()
        assert bridge.snapshot is not None

    def test_pre_provided_snapshot(self, empty_dashboard):
        snap = DashboardSnapshot(
            overall_health=HealthLevel.HEALTHY,
            compliance_score=0.95,
        )
        bridge = DashboardDataBridge(
            dashboard=empty_dashboard,
            snapshot=snap,
        )
        data = bridge.export()
        assert data["health_banner"]["compliance_score"] == 95


# ── History-based timeline tests ────────────────────────────────


class TestSnapshotHistory:
    def test_trust_timeline_from_history(self, populated_dashboard):
        # Generate multiple snapshots to create history
        populated_dashboard.generate_snapshot()
        populated_dashboard.generate_snapshot()
        populated_dashboard.generate_snapshot()

        bridge = DashboardDataBridge(dashboard=populated_dashboard)
        timeline = bridge.build_trust_timeline()
        assert len(timeline) == 3
        assert timeline[-1]["time"] == "Now"

    def test_trust_timeline_max_7(self, populated_dashboard):
        for _ in range(10):
            populated_dashboard.generate_snapshot()

        bridge = DashboardDataBridge(dashboard=populated_dashboard)
        timeline = bridge.build_trust_timeline()
        assert len(timeline) <= 7


# ── Critical health banner tests ────────────────────────────────


class TestCriticalHealth:
    def test_critical_banner(self):
        crl = CertificateRevocationList()
        crl.revoke("bad-tool", emergency=True)
        dash = SecurityDashboard(crl=crl)
        bridge = DashboardDataBridge(dashboard=dash)
        banner = bridge.build_health_banner()
        assert banner["overall_health"] == "critical"
        assert banner["health_label"] == "Critical"
        assert banner["all_operational"] is False


# ── JSON serialisation tests ────────────────────────────────────


class TestJsonExport:
    def test_json_roundtrip(self, populated_dashboard):
        bridge = DashboardDataBridge(dashboard=populated_dashboard)
        json_str = bridge.export_json()
        parsed = json.loads(json_str)
        assert parsed["health_banner"]["total_tools"] == 2

    def test_json_indent(self, empty_dashboard):
        bridge = DashboardDataBridge(dashboard=empty_dashboard)
        json_str = bridge.export_json(indent=4)
        # Indented JSON has newlines
        assert "\n" in json_str


# ── Import tests ────────────────────────────────────────────────


class TestBridgeImports:
    def test_import_from_dashboard_package(self):
        from fastmcp.server.security.dashboard import DashboardDataBridge

        assert DashboardDataBridge is not None

    def test_import_from_security_package(self):
        from fastmcp.server.security import DashboardDataBridge

        assert DashboardDataBridge is not None


# ── Average trust computation tests ─────────────────────────────


class TestAverageTrust:
    def test_avg_trust_computed(self, populated_dashboard):
        bridge = DashboardDataBridge(dashboard=populated_dashboard)
        data = bridge.export()
        assert data["health_banner"]["avg_trust"] > 0

    def test_avg_trust_empty(self, empty_dashboard):
        bridge = DashboardDataBridge(dashboard=empty_dashboard)
        data = bridge.export()
        assert data["health_banner"]["avg_trust"] == 0.0


# ── Compliance section code-path tests ───────────────────────


class TestComplianceSections:
    """Exercises the data_bridge compliance code path that iterates
    report.sections and accesses section.name / section.findings.

    The populated_dashboard fixture uses an empty framework so the
    bridge falls through to the fallback path.  These tests wire up
    a real framework with requirements so generate_report() produces
    actual ReportSection objects.
    """

    @pytest.fixture()
    def compliance_dashboard(self):
        fw = ComplianceFramework(name="TestFramework")
        fw.add_requirement(
            ComplianceRequirement(
                requirement_id="req-ac-1",
                name="Access Control Active",
                category=RequirementCategory.ACCESS_CONTROL,
            )
        )
        fw.add_requirement(
            ComplianceRequirement(
                requirement_id="req-ac-2",
                name="MFA Enabled",
                category=RequirementCategory.ACCESS_CONTROL,
            )
        )
        fw.add_requirement(
            ComplianceRequirement(
                requirement_id="req-audit-1",
                name="Audit Logging Active",
                category=RequirementCategory.AUDIT_LOGGING,
            )
        )

        reporter = ComplianceReporter(framework=fw)

        # Register checks: first passes, second fails, third passes
        reporter.register_check(
            "req-ac-1",
            lambda: (ComplianceStatus.COMPLIANT, "OK", {}),
        )
        reporter.register_check(
            "req-ac-2",
            lambda: (ComplianceStatus.NON_COMPLIANT, "MFA not enabled", {}),
        )
        reporter.register_check(
            "req-audit-1",
            lambda: (ComplianceStatus.COMPLIANT, "Logging active", {}),
        )

        return SecurityDashboard(compliance_reporter=reporter)

    def test_sections_returned_not_fallback(self, compliance_dashboard):
        bridge = DashboardDataBridge(dashboard=compliance_dashboard)
        data = bridge.build_compliance_data()
        # Should have 2 sections (Access Control + Audit Logging), not
        # the single "Overall" fallback entry.
        assert len(data) == 2
        names = {d["name"] for d in data}
        assert "Access Control" in names
        assert "Audit Logging" in names

    def test_section_scores_computed(self, compliance_dashboard):
        bridge = DashboardDataBridge(dashboard=compliance_dashboard)
        data = bridge.build_compliance_data()
        by_name = {d["name"]: d for d in data}

        ac = by_name["Access Control"]
        assert ac["total"] == 2
        assert ac["passed"] == 1
        assert ac["score"] == 50  # 1/2 = 50%

        audit = by_name["Audit Logging"]
        assert audit["total"] == 1
        assert audit["passed"] == 1
        assert audit["score"] == 100  # 1/1 = 100%

    def test_section_dict_keys(self, compliance_dashboard):
        bridge = DashboardDataBridge(dashboard=compliance_dashboard)
        data = bridge.build_compliance_data()
        for entry in data:
            assert "name" in entry
            assert "score" in entry
            assert "total" in entry
            assert "passed" in entry

    def test_all_compliant_framework(self):
        fw = ComplianceFramework(name="AllPass")
        fw.add_requirement(
            ComplianceRequirement(
                requirement_id="r1",
                name="Check One",
                category=RequirementCategory.DATA_PROTECTION,
            )
        )
        reporter = ComplianceReporter(framework=fw)
        reporter.register_check(
            "r1",
            lambda: (ComplianceStatus.COMPLIANT, "OK", {}),
        )
        dash = SecurityDashboard(compliance_reporter=reporter)
        bridge = DashboardDataBridge(dashboard=dash)
        data = bridge.build_compliance_data()
        assert len(data) == 1
        assert data[0]["score"] == 100
        assert data[0]["name"] == "Data Protection"

    def test_empty_framework_uses_fallback(self):
        fw = ComplianceFramework(name="Empty")
        reporter = ComplianceReporter(framework=fw)
        dash = SecurityDashboard(compliance_reporter=reporter)
        bridge = DashboardDataBridge(dashboard=dash)
        data = bridge.build_compliance_data()
        # No requirements → no sections → fallback
        assert len(data) == 1
        assert data[0]["name"] == "Overall"
