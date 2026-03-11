"""Dashboard snapshot generation and health monitoring.

Aggregates state from all SecureMCP components into dashboard-ready
snapshots, tracks health over time, and provides timeline entries
for activity feeds.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastmcp.server.security.alerts.bus import SecurityEventBus
from fastmcp.server.security.compliance.reports import ComplianceReporter, ReportType
from fastmcp.server.security.federation.crl import CertificateRevocationList
from fastmcp.server.security.federation.federation import TrustFederation
from fastmcp.server.security.gateway.tool_marketplace import ToolMarketplace
from fastmcp.server.security.registry.registry import TrustRegistry
from fastmcp.server.security.sandbox.enforcer import SandboxedRunner


class HealthLevel(enum.Enum):
    """Health level for a component."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class TimelineType(enum.Enum):
    """Type of timeline entry."""

    SECURITY_CHECK = "security_check"
    VIOLATION = "violation"
    REVOCATION = "revocation"
    CERTIFICATION = "certification"
    COMPLIANCE = "compliance"
    FEDERATION = "federation"
    SYSTEM = "system"


@dataclass
class ComponentHealth:
    """Health status of a single component."""

    component_id: str = ""
    name: str = ""
    level: HealthLevel = HealthLevel.UNKNOWN
    message: str = ""
    metrics: dict = field(default_factory=dict)
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        if not self.component_id:
            self.component_id = f"comp-{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> dict:
        return {
            "component_id": self.component_id,
            "name": self.name,
            "level": self.level.value,
            "message": self.message,
            "metrics": self.metrics,
            "checked_at": self.checked_at.isoformat(),
        }


@dataclass
class TimelineEntry:
    """An entry in the security activity timeline."""

    entry_id: str = ""
    entry_type: TimelineType = TimelineType.SYSTEM
    title: str = ""
    description: str = ""
    severity: str = "info"
    actor_id: str = ""
    resource_id: str = ""
    data: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        if not self.entry_id:
            self.entry_id = f"tl-{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "entry_type": self.entry_type.value,
            "title": self.title,
            "description": self.description,
            "severity": self.severity,
            "actor_id": self.actor_id,
            "resource_id": self.resource_id,
            "data": self.data,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class DashboardSnapshot:
    """A point-in-time snapshot of the security dashboard state."""

    snapshot_id: str = ""
    overall_health: HealthLevel = HealthLevel.UNKNOWN
    compliance_score: float = 0.0
    component_health: list[ComponentHealth] = field(default_factory=list)
    recent_timeline: list[TimelineEntry] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        if not self.snapshot_id:
            self.snapshot_id = f"snap-{uuid.uuid4().hex[:8]}"

    def add_component_health(self, health: ComponentHealth) -> None:
        self.component_health.append(health)

    def add_timeline_entry(self, entry: TimelineEntry) -> None:
        self.recent_timeline.append(entry)

    @property
    def healthy_count(self) -> int:
        return sum(1 for c in self.component_health if c.level == HealthLevel.HEALTHY)

    @property
    def critical_count(self) -> int:
        return sum(1 for c in self.component_health if c.level == HealthLevel.CRITICAL)

    @property
    def degraded_count(self) -> int:
        return sum(1 for c in self.component_health if c.level == HealthLevel.DEGRADED)

    @property
    def component_count(self) -> int:
        return len(self.component_health)

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "overall_health": self.overall_health.value,
            "compliance_score": self.compliance_score,
            "healthy_count": self.healthy_count,
            "critical_count": self.critical_count,
            "degraded_count": self.degraded_count,
            "component_count": self.component_count,
            "components": [c.to_dict() for c in self.component_health],
            "recent_timeline": [e.to_dict() for e in self.recent_timeline],
            "summary": self.summary,
            "generated_at": self.generated_at.isoformat(),
        }

    def to_summary_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "overall_health": self.overall_health.value,
            "compliance_score": self.compliance_score,
            "healthy_count": self.healthy_count,
            "critical_count": self.critical_count,
            "degraded_count": self.degraded_count,
            "component_count": self.component_count,
            "generated_at": self.generated_at.isoformat(),
        }


class SecurityDashboard:
    """Generates dashboard snapshots from SecureMCP components.

    Aggregates health, compliance, and activity data from all
    connected components into dashboard-ready snapshots.

    Components are optional — only connected components are included.
    """

    def __init__(
        self,
        *,
        registry: TrustRegistry | None = None,
        marketplace: ToolMarketplace | None = None,
        federation: TrustFederation | None = None,
        sandbox_runner: SandboxedRunner | None = None,
        compliance_reporter: ComplianceReporter | None = None,
        event_bus: SecurityEventBus | None = None,
        crl: CertificateRevocationList | None = None,
    ):
        self._registry = registry
        self._marketplace = marketplace
        self._federation = federation
        self._sandbox_runner = sandbox_runner
        self._compliance_reporter = compliance_reporter
        self._event_bus = event_bus
        self._crl = crl
        self._snapshot_history: list[DashboardSnapshot] = []
        self._timeline: list[TimelineEntry] = []

    # ── Timeline management ──────────────────────────────────────

    def add_timeline_entry(self, entry: TimelineEntry) -> None:
        """Add an entry to the activity timeline."""
        self._timeline.append(entry)

    def get_timeline(
        self, limit: int = 50, entry_type: TimelineType | None = None
    ) -> list[TimelineEntry]:
        """Get recent timeline entries, optionally filtered by type."""
        entries = self._timeline
        if entry_type:
            entries = [e for e in entries if e.entry_type == entry_type]
        return sorted(entries, key=lambda e: e.timestamp, reverse=True)[:limit]

    @property
    def timeline_count(self) -> int:
        return len(self._timeline)

    # ── Component health checks ──────────────────────────────────

    def _check_registry_health(self) -> ComponentHealth:
        """Check trust registry health."""
        if not self._registry:
            return ComponentHealth(
                name="Trust Registry",
                level=HealthLevel.UNKNOWN,
                message="Not configured",
            )

        total = self._registry.record_count
        all_records = self._registry.get_all()
        certified = sum(1 for r in all_records if r.is_certified)
        metrics = {"total_tools": total, "certified_count": certified}

        if total == 0:
            return ComponentHealth(
                name="Trust Registry",
                level=HealthLevel.DEGRADED,
                message="No tools registered",
                metrics=metrics,
            )

        return ComponentHealth(
            name="Trust Registry",
            level=HealthLevel.HEALTHY,
            message=f"{total} tools registered, {certified} certified",
            metrics=metrics,
        )

    def _check_marketplace_health(self) -> ComponentHealth:
        """Check marketplace health."""
        if not self._marketplace:
            return ComponentHealth(
                name="Marketplace",
                level=HealthLevel.UNKNOWN,
                message="Not configured",
            )

        stats = self._marketplace.get_statistics()
        published = stats.get("published_listings", 0)

        return ComponentHealth(
            name="Marketplace",
            level=HealthLevel.HEALTHY,
            message=f"{published} tools published",
            metrics=stats,
        )

    def _check_federation_health(self) -> ComponentHealth:
        """Check federation health."""
        if not self._federation:
            return ComponentHealth(
                name="Federation",
                level=HealthLevel.UNKNOWN,
                message="Not configured",
            )

        status = self._federation.get_federation_status()
        peer_count = status.get("peer_count", 0)
        active_peers = status.get("active_peers", 0)
        crl_entries = status.get("crl_entries", 0)

        if peer_count > 0 and active_peers == 0:
            level = HealthLevel.DEGRADED
            msg = f"All {peer_count} peers inactive"
        elif crl_entries > 5:
            level = HealthLevel.DEGRADED
            msg = f"{crl_entries} revocations active"
        else:
            level = HealthLevel.HEALTHY
            msg = f"{active_peers} active peers, {crl_entries} revocations"

        return ComponentHealth(
            name="Federation",
            level=level,
            message=msg,
            metrics=status,
        )

    def _check_sandbox_health(self) -> ComponentHealth:
        """Check sandbox runner health."""
        if not self._sandbox_runner:
            return ComponentHealth(
                name="Sandbox",
                level=HealthLevel.UNKNOWN,
                message="Not configured",
            )

        active = self._sandbox_runner.active_count
        completed = self._sandbox_runner.completed_count
        violations = self._sandbox_runner.get_violations()

        if len(violations) > 10:
            level = HealthLevel.DEGRADED
            msg = f"{len(violations)} violations recorded"
        else:
            level = HealthLevel.HEALTHY
            msg = f"{active} active, {completed} completed"

        return ComponentHealth(
            name="Sandbox",
            level=level,
            message=msg,
            metrics={
                "active": active,
                "completed": completed,
                "violations": len(violations),
            },
        )

    def _check_crl_health(self) -> ComponentHealth:
        """Check CRL health."""
        if not self._crl:
            return ComponentHealth(
                name="CRL",
                level=HealthLevel.UNKNOWN,
                message="Not configured",
            )

        entry_count = self._crl.entry_count
        emergency = len(self._crl.get_emergency_entries())

        if emergency > 0:
            level = HealthLevel.CRITICAL
            msg = f"{emergency} emergency revocations"
        elif entry_count > 10:
            level = HealthLevel.DEGRADED
            msg = f"{entry_count} active revocations"
        else:
            level = HealthLevel.HEALTHY
            msg = f"{entry_count} revocations"

        return ComponentHealth(
            name="CRL",
            level=level,
            message=msg,
            metrics={
                "entry_count": entry_count,
                "emergency_count": emergency,
            },
        )

    # ── Snapshot generation ──────────────────────────────────────

    def _determine_overall_health(
        self, components: list[ComponentHealth]
    ) -> HealthLevel:
        """Determine overall health from component health."""
        configured = [c for c in components if c.level != HealthLevel.UNKNOWN]
        if not configured:
            return HealthLevel.UNKNOWN

        if any(c.level == HealthLevel.CRITICAL for c in configured):
            return HealthLevel.CRITICAL
        if any(c.level == HealthLevel.DEGRADED for c in configured):
            return HealthLevel.DEGRADED
        return HealthLevel.HEALTHY

    def generate_snapshot(self) -> DashboardSnapshot:
        """Generate a complete dashboard snapshot.

        Checks all connected component health, runs compliance
        if available, and includes recent timeline entries.

        Returns:
            A DashboardSnapshot with current system state.
        """
        snapshot = DashboardSnapshot()

        # Component health checks
        checks = [
            self._check_registry_health(),
            self._check_marketplace_health(),
            self._check_federation_health(),
            self._check_sandbox_health(),
            self._check_crl_health(),
        ]

        for check in checks:
            snapshot.add_component_health(check)

        # Overall health
        snapshot.overall_health = self._determine_overall_health(checks)

        # Compliance score
        if self._compliance_reporter:
            report = self._compliance_reporter.generate_report(
                report_type=ReportType.SUMMARY
            )
            snapshot.compliance_score = report.compliance_score

        # Recent timeline
        recent = self.get_timeline(limit=20)
        for entry in recent:
            snapshot.add_timeline_entry(entry)

        # Summary stats
        snapshot.summary = self._build_summary()

        self._snapshot_history.append(snapshot)
        return snapshot

    def _build_summary(self) -> dict:
        """Build a summary dict of key metrics."""
        summary: dict[str, Any] = {}

        if self._registry:
            all_records = self._registry.get_all()
            summary["total_tools"] = self._registry.record_count
            summary["certified_tools"] = sum(1 for r in all_records if r.is_certified)

        if self._marketplace:
            stats = self._marketplace.get_statistics()
            summary["published_tools"] = stats.get("published_listings", 0)
            summary["total_installs"] = stats.get("total_installs", 0)

        if self._federation:
            status = self._federation.get_federation_status()
            summary["federation_peers"] = status.get("peer_count", 0)
            summary["active_revocations"] = status.get("crl_entries", 0)

        if self._crl:
            summary["crl_entries"] = self._crl.entry_count
            summary["emergency_revocations"] = len(self._crl.get_emergency_entries())

        if self._sandbox_runner:
            summary["active_sandboxes"] = self._sandbox_runner.active_count
            summary["sandbox_violations"] = len(self._sandbox_runner.get_violations())

        return summary

    # ── History ──────────────────────────────────────────────────

    def get_snapshot_history(self) -> list[DashboardSnapshot]:
        """Get all previously generated snapshots."""
        return list(self._snapshot_history)

    def get_latest_snapshot(self) -> DashboardSnapshot | None:
        """Get the most recently generated snapshot."""
        if self._snapshot_history:
            return self._snapshot_history[-1]
        return None

    @property
    def snapshot_count(self) -> int:
        return len(self._snapshot_history)

    def compare_snapshots(
        self, snap_a: DashboardSnapshot, snap_b: DashboardSnapshot
    ) -> dict:
        """Compare two snapshots to show trends."""
        a_health = {c.name: c.level.value for c in snap_a.component_health}
        b_health = {c.name: c.level.value for c in snap_b.component_health}

        changes = {}
        for name in set(list(a_health.keys()) + list(b_health.keys())):
            old = a_health.get(name, "unknown")
            new = b_health.get(name, "unknown")
            if old != new:
                changes[name] = {"from": old, "to": new}

        return {
            "snapshot_a_id": snap_a.snapshot_id,
            "snapshot_b_id": snap_b.snapshot_id,
            "health_a": snap_a.overall_health.value,
            "health_b": snap_b.overall_health.value,
            "compliance_delta": snap_b.compliance_score - snap_a.compliance_score,
            "component_changes": changes,
        }

    def get_statistics(self) -> dict:
        """Get dashboard statistics."""
        components = []
        if self._registry:
            components.append("registry")
        if self._marketplace:
            components.append("marketplace")
        if self._federation:
            components.append("federation")
        if self._sandbox_runner:
            components.append("sandbox_runner")
        if self._compliance_reporter:
            components.append("compliance_reporter")
        if self._event_bus:
            components.append("event_bus")
        if self._crl:
            components.append("crl")

        return {
            "components_configured": len(components),
            "components": components,
            "snapshot_count": self.snapshot_count,
            "timeline_entries": self.timeline_count,
        }
