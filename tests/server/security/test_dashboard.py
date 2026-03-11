"""Tests for Dashboard (Phase 20).

Covers component health checks, timeline management, snapshot generation,
comparison, history, and integration with other components.
"""

from __future__ import annotations

import pytest

from fastmcp.server.security.compliance.frameworks import ComplianceFramework
from fastmcp.server.security.compliance.reports import (
    ComplianceReporter,
)
from fastmcp.server.security.dashboard.snapshot import (
    ComponentHealth,
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

# ── ComponentHealth tests ────────────────────────────────────────


class TestComponentHealth:
    def test_default(self):
        h = ComponentHealth()
        assert h.component_id.startswith("comp-")
        assert h.level == HealthLevel.UNKNOWN

    def test_to_dict(self):
        h = ComponentHealth(name="Registry", level=HealthLevel.HEALTHY, message="OK")
        d = h.to_dict()
        assert d["name"] == "Registry"
        assert d["level"] == "healthy"
        assert d["message"] == "OK"


# ── TimelineEntry tests ─────────────────────────────────────────


class TestTimelineEntry:
    def test_default(self):
        e = TimelineEntry()
        assert e.entry_id.startswith("tl-")
        assert e.entry_type == TimelineType.SYSTEM

    def test_to_dict(self):
        e = TimelineEntry(
            entry_type=TimelineType.VIOLATION,
            title="Sandbox violation",
            severity="warning",
        )
        d = e.to_dict()
        assert d["entry_type"] == "violation"
        assert d["title"] == "Sandbox violation"
        assert d["severity"] == "warning"


# ── DashboardSnapshot tests ─────────────────────────────────────


class TestDashboardSnapshot:
    def test_default(self):
        s = DashboardSnapshot()
        assert s.snapshot_id.startswith("snap-")
        assert s.overall_health == HealthLevel.UNKNOWN
        assert s.component_count == 0

    def test_add_component_health(self):
        s = DashboardSnapshot()
        s.add_component_health(ComponentHealth(level=HealthLevel.HEALTHY))
        s.add_component_health(ComponentHealth(level=HealthLevel.CRITICAL))
        assert s.component_count == 2
        assert s.healthy_count == 1
        assert s.critical_count == 1

    def test_add_timeline_entry(self):
        s = DashboardSnapshot()
        s.add_timeline_entry(TimelineEntry(title="Event A"))
        assert len(s.recent_timeline) == 1

    def test_degraded_count(self):
        s = DashboardSnapshot()
        s.add_component_health(ComponentHealth(level=HealthLevel.DEGRADED))
        s.add_component_health(ComponentHealth(level=HealthLevel.DEGRADED))
        assert s.degraded_count == 2

    def test_to_dict(self):
        s = DashboardSnapshot(overall_health=HealthLevel.HEALTHY, compliance_score=0.95)
        d = s.to_dict()
        assert d["overall_health"] == "healthy"
        assert d["compliance_score"] == 0.95
        assert "components" in d
        assert "recent_timeline" in d

    def test_to_summary_dict(self):
        s = DashboardSnapshot(overall_health=HealthLevel.HEALTHY)
        d = s.to_summary_dict()
        assert d["overall_health"] == "healthy"
        assert "components" not in d


# ── SecurityDashboard basic tests ────────────────────────────────


class TestSecurityDashboardBasic:
    def test_empty_dashboard(self):
        dash = SecurityDashboard()
        assert dash.snapshot_count == 0
        assert dash.timeline_count == 0

    def test_statistics(self):
        dash = SecurityDashboard(
            registry=TrustRegistry(),
            marketplace=ToolMarketplace(),
        )
        stats = dash.get_statistics()
        assert stats["components_configured"] == 2
        assert "registry" in stats["components"]
        assert "marketplace" in stats["components"]


# ── Timeline management tests ────────────────────────────────────


class TestTimelineManagement:
    def test_add_timeline_entry(self):
        dash = SecurityDashboard()
        dash.add_timeline_entry(TimelineEntry(title="Event A"))
        assert dash.timeline_count == 1

    def test_get_timeline_default(self):
        dash = SecurityDashboard()
        dash.add_timeline_entry(TimelineEntry(title="A"))
        dash.add_timeline_entry(TimelineEntry(title="B"))
        entries = dash.get_timeline()
        assert len(entries) == 2

    def test_get_timeline_limit(self):
        dash = SecurityDashboard()
        for i in range(10):
            dash.add_timeline_entry(TimelineEntry(title=f"Event {i}"))
        entries = dash.get_timeline(limit=3)
        assert len(entries) == 3

    def test_get_timeline_filter_type(self):
        dash = SecurityDashboard()
        dash.add_timeline_entry(
            TimelineEntry(title="Violation", entry_type=TimelineType.VIOLATION)
        )
        dash.add_timeline_entry(
            TimelineEntry(title="System", entry_type=TimelineType.SYSTEM)
        )
        violations = dash.get_timeline(entry_type=TimelineType.VIOLATION)
        assert len(violations) == 1
        assert violations[0].title == "Violation"


# ── Component health tests ───────────────────────────────────────


class TestComponentHealthChecks:
    def test_registry_health_not_configured(self):
        dash = SecurityDashboard()
        health = dash._check_registry_health()
        assert health.level == HealthLevel.UNKNOWN

    def test_registry_health_empty(self):
        dash = SecurityDashboard(registry=TrustRegistry())
        health = dash._check_registry_health()
        assert health.level == HealthLevel.DEGRADED

    def test_registry_health_with_tools(self):
        registry = TrustRegistry()
        registry.register("tool-a", attestation=None)
        dash = SecurityDashboard(registry=registry)
        health = dash._check_registry_health()
        assert health.level == HealthLevel.HEALTHY

    def test_marketplace_health_not_configured(self):
        dash = SecurityDashboard()
        health = dash._check_marketplace_health()
        assert health.level == HealthLevel.UNKNOWN

    def test_marketplace_health_configured(self):
        dash = SecurityDashboard(marketplace=ToolMarketplace())
        health = dash._check_marketplace_health()
        assert health.level == HealthLevel.HEALTHY

    def test_federation_health_not_configured(self):
        dash = SecurityDashboard()
        health = dash._check_federation_health()
        assert health.level == HealthLevel.UNKNOWN

    def test_federation_health_active(self):
        fed = TrustFederation()
        fed.add_peer("partner")
        dash = SecurityDashboard(federation=fed)
        health = dash._check_federation_health()
        assert health.level == HealthLevel.HEALTHY

    def test_sandbox_health_not_configured(self):
        dash = SecurityDashboard()
        health = dash._check_sandbox_health()
        assert health.level == HealthLevel.UNKNOWN

    def test_sandbox_health_configured(self):
        dash = SecurityDashboard(sandbox_runner=SandboxedRunner())
        health = dash._check_sandbox_health()
        assert health.level == HealthLevel.HEALTHY

    def test_crl_health_not_configured(self):
        dash = SecurityDashboard()
        health = dash._check_crl_health()
        assert health.level == HealthLevel.UNKNOWN

    def test_crl_health_empty(self):
        dash = SecurityDashboard(crl=CertificateRevocationList())
        health = dash._check_crl_health()
        assert health.level == HealthLevel.HEALTHY

    def test_crl_health_emergency(self):
        crl = CertificateRevocationList()
        crl.revoke("bad-tool", emergency=True)
        dash = SecurityDashboard(crl=crl)
        health = dash._check_crl_health()
        assert health.level == HealthLevel.CRITICAL


# ── Snapshot generation tests ────────────────────────────────────


class TestSnapshotGeneration:
    def test_empty_snapshot(self):
        dash = SecurityDashboard()
        snap = dash.generate_snapshot()
        assert snap.overall_health == HealthLevel.UNKNOWN
        assert snap.component_count == 5  # 5 health checks run

    def test_snapshot_with_registry(self):
        registry = TrustRegistry()
        registry.register("tool-a", attestation=None)
        dash = SecurityDashboard(registry=registry)
        snap = dash.generate_snapshot()
        # At least one healthy component
        assert snap.healthy_count >= 1

    def test_snapshot_overall_health_critical(self):
        crl = CertificateRevocationList()
        crl.revoke("bad", emergency=True)
        dash = SecurityDashboard(crl=crl)
        snap = dash.generate_snapshot()
        assert snap.overall_health == HealthLevel.CRITICAL

    def test_snapshot_with_compliance(self):
        fw = ComplianceFramework(name="Test")
        reporter = ComplianceReporter(framework=fw)
        dash = SecurityDashboard(compliance_reporter=reporter)
        snap = dash.generate_snapshot()
        # Empty framework = 100% compliance
        assert snap.compliance_score == 1.0

    def test_snapshot_includes_timeline(self):
        dash = SecurityDashboard()
        dash.add_timeline_entry(TimelineEntry(title="Event A"))
        snap = dash.generate_snapshot()
        assert len(snap.recent_timeline) == 1

    def test_snapshot_includes_summary(self):
        registry = TrustRegistry()
        registry.register("tool-a", attestation=None)
        dash = SecurityDashboard(registry=registry)
        snap = dash.generate_snapshot()
        assert "total_tools" in snap.summary

    def test_snapshot_history(self):
        dash = SecurityDashboard()
        dash.generate_snapshot()
        dash.generate_snapshot()
        assert dash.snapshot_count == 2
        assert len(dash.get_snapshot_history()) == 2

    def test_latest_snapshot(self):
        dash = SecurityDashboard()
        dash.generate_snapshot()
        latest = dash.get_latest_snapshot()
        assert latest is not None

    def test_latest_snapshot_empty(self):
        dash = SecurityDashboard()
        assert dash.get_latest_snapshot() is None


# ── Snapshot comparison tests ────────────────────────────────────


class TestSnapshotComparison:
    def test_compare_snapshots(self):
        dash = SecurityDashboard()
        snap_a = dash.generate_snapshot()

        # Add CRL with emergency for different health
        crl = CertificateRevocationList()
        crl.revoke("bad", emergency=True)
        dash_b = SecurityDashboard(crl=crl)
        snap_b = dash_b.generate_snapshot()

        comparison = dash.compare_snapshots(snap_a, snap_b)
        assert "health_a" in comparison
        assert "health_b" in comparison
        assert "component_changes" in comparison

    def test_compare_compliance_delta(self):
        snap_a = DashboardSnapshot(compliance_score=0.7)
        snap_b = DashboardSnapshot(compliance_score=0.9)
        dash = SecurityDashboard()
        comparison = dash.compare_snapshots(snap_a, snap_b)
        assert comparison["compliance_delta"] == pytest.approx(0.2)


# ── Overall health determination tests ───────────────────────────


class TestOverallHealth:
    def test_all_unknown(self):
        dash = SecurityDashboard()
        components = [ComponentHealth(level=HealthLevel.UNKNOWN)]
        assert dash._determine_overall_health(components) == HealthLevel.UNKNOWN

    def test_all_healthy(self):
        dash = SecurityDashboard()
        components = [
            ComponentHealth(level=HealthLevel.HEALTHY),
            ComponentHealth(level=HealthLevel.HEALTHY),
        ]
        assert dash._determine_overall_health(components) == HealthLevel.HEALTHY

    def test_one_critical(self):
        dash = SecurityDashboard()
        components = [
            ComponentHealth(level=HealthLevel.HEALTHY),
            ComponentHealth(level=HealthLevel.CRITICAL),
        ]
        assert dash._determine_overall_health(components) == HealthLevel.CRITICAL

    def test_one_degraded(self):
        dash = SecurityDashboard()
        components = [
            ComponentHealth(level=HealthLevel.HEALTHY),
            ComponentHealth(level=HealthLevel.DEGRADED),
        ]
        assert dash._determine_overall_health(components) == HealthLevel.DEGRADED

    def test_mixed_unknown_and_healthy(self):
        dash = SecurityDashboard()
        components = [
            ComponentHealth(level=HealthLevel.UNKNOWN),
            ComponentHealth(level=HealthLevel.HEALTHY),
        ]
        assert dash._determine_overall_health(components) == HealthLevel.HEALTHY


# ── Import tests ─────────────────────────────────────────────────


class TestImports:
    def test_dashboard_imports(self):
        from fastmcp.server.security.dashboard import (
            SecurityDashboard,
        )

        assert SecurityDashboard is not None

    def test_top_level_imports(self):
        from fastmcp.server.security import (
            SecurityDashboard,
        )

        assert SecurityDashboard is not None


# ── Snapshot summary marketplace stats tests ─────────────────


class TestSnapshotMarketplaceStats:
    """Verify that snapshot summary correctly captures marketplace
    published_listings count.  Covers the fix where snapshot.py
    used stats.get('published') instead of stats.get('published_listings').
    """

    def test_published_tools_in_summary(self):
        marketplace = ToolMarketplace()
        marketplace.publish("tool-a", display_name="Tool A")
        marketplace.publish("tool-b", display_name="Tool B")
        marketplace.publish("tool-c", display_name="Tool C")

        dash = SecurityDashboard(marketplace=marketplace)
        snap = dash.generate_snapshot()
        assert snap.summary["published_tools"] == 3

    def test_published_tools_zero_when_empty(self):
        marketplace = ToolMarketplace()
        dash = SecurityDashboard(marketplace=marketplace)
        snap = dash.generate_snapshot()
        assert snap.summary["published_tools"] == 0

    def test_marketplace_health_message_includes_count(self):
        marketplace = ToolMarketplace()
        marketplace.publish("tool-x", display_name="Tool X")
        marketplace.publish("tool-y", display_name="Tool Y")

        dash = SecurityDashboard(marketplace=marketplace)
        health = dash._check_marketplace_health()
        assert "2" in health.message
        assert "published" in health.message.lower()

    def test_total_installs_in_summary(self):
        marketplace = ToolMarketplace()
        marketplace.publish("tool-a", display_name="Tool A")
        dash = SecurityDashboard(marketplace=marketplace)
        snap = dash.generate_snapshot()
        assert "total_installs" in snap.summary
