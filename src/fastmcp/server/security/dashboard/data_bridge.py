"""Dashboard data bridge — generates frontend-ready JSON from backend state.

Converts SecurityDashboard snapshots and component state into the exact
data structures consumed by the SecureMCP React dashboard. Supports both
one-shot export and incremental updates.

Usage:
    bridge = DashboardDataBridge(dashboard=security_dashboard)
    data = bridge.export()
    json_string = bridge.export_json()
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastmcp.server.security.dashboard.snapshot import (
    DashboardSnapshot,
    HealthLevel,
    SecurityDashboard,
)

# ── Mapping helpers ─────────────────────────────────────────────

_TIMELINE_TYPE_TO_ICON: dict[str, str] = {
    "security_check": "shield",
    "violation": "alert",
    "revocation": "x",
    "certification": "check",
    "compliance": "bar",
    "federation": "globe",
    "system": "trending",
}

_SEVERITY_TO_BADGE: dict[str, str] = {
    "info": "info",
    "warning": "warning",
    "critical": "critical",
    "error": "critical",
}

_COMPONENT_ICONS: dict[str, str] = {
    "Trust Registry": "shield",
    "Marketplace": "package",
    "Federation": "globe",
    "Sandbox": "lock",
    "CRL": "filecheck",
    "Compliance": "check",
    "Event Bus": "activity",
    "Provenance": "eye",
}


def _relative_time(dt: datetime) -> str:
    """Format a datetime as a human-readable relative time string."""
    now = datetime.now(timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())

    if seconds < 60:
        return "just now"
    if seconds < 3600:
        mins = seconds // 60
        return f"{mins} min ago"
    if seconds < 86400:
        hrs = seconds // 3600
        return f"{hrs} hr ago"
    days = seconds // 86400
    return f"{days}d ago"


# ── Data bridge ─────────────────────────────────────────────────


@dataclass
class DashboardDataBridge:
    """Converts SecurityDashboard state into React-dashboard-compatible dicts.

    The exported structure matches the constants consumed by the React
    SecureMCPDashboard component: TRUST_TIMELINE, COMPLIANCE_DATA,
    TOOL_CATEGORIES, COMPONENTS, TIMELINE_EVENTS, and TOP_TOOLS.

    Attributes:
        dashboard: The SecurityDashboard instance to read from.
        snapshot: An optional pre-generated snapshot.  If *None*, a fresh
            snapshot is generated on each ``export()`` call.
    """

    dashboard: SecurityDashboard
    snapshot: DashboardSnapshot | None = None

    # ── Snapshot helpers ──────────────────────────────────────

    def _ensure_snapshot(self) -> DashboardSnapshot:
        if self.snapshot is not None:
            return self.snapshot
        return self.dashboard.generate_snapshot()

    # ── Trust timeline ────────────────────────────────────────

    def build_trust_timeline(self) -> list[dict[str, Any]]:
        """Build TRUST_TIMELINE from snapshot history.

        Returns a list of ``{time, score, violations, checks}`` dicts
        derived from historical snapshots.  If no history exists, a
        single-entry list from the current snapshot is returned.
        """
        history = self.dashboard.get_snapshot_history()
        if not history:
            snap = self._ensure_snapshot()
            return [
                {
                    "time": "Now",
                    "score": round(snap.compliance_score, 2),
                    "violations": snap.critical_count + snap.degraded_count,
                    "checks": snap.component_count,
                }
            ]

        entries = []
        for snap in history[-7:]:  # last 7 for chart
            entries.append(
                {
                    "time": snap.generated_at.strftime("%H:%M"),
                    "score": round(snap.compliance_score, 2),
                    "violations": snap.critical_count + snap.degraded_count,
                    "checks": snap.component_count,
                }
            )
        # Mark last entry as "Now"
        if entries:
            entries[-1]["time"] = "Now"
        return entries

    # ── Compliance data ───────────────────────────────────────

    def build_compliance_data(self) -> list[dict[str, Any]]:
        """Build COMPLIANCE_DATA from the compliance reporter.

        Returns a list of ``{name, score, total, passed}`` dicts per
        compliance requirement category.
        """
        snap = self._ensure_snapshot()
        summary = snap.summary

        # If compliance reporter is wired up, use report sections
        if self.dashboard._compliance_reporter:
            from fastmcp.server.security.compliance.reports import ReportType

            report = self.dashboard._compliance_reporter.generate_report(
                report_type=ReportType.FULL
            )
            categories: list[dict[str, Any]] = []
            for section in report.sections:
                total = len(section.findings)
                passed = sum(1 for f in section.findings if f.is_compliant)
                score = round((passed / total) * 100) if total > 0 else 100
                categories.append(
                    {
                        "name": section.name,
                        "score": score,
                        "total": total,
                        "passed": passed,
                    }
                )
            if categories:
                return categories

        # Fallback: single entry from snapshot compliance score
        score_pct = round(snap.compliance_score * 100)
        return [
            {
                "name": "Overall",
                "score": score_pct,
                "total": 1,
                "passed": 1 if score_pct >= 50 else 0,
            }
        ]

    # ── Tool categories ───────────────────────────────────────

    def build_tool_categories(self) -> list[dict[str, Any]]:
        """Build TOOL_CATEGORIES from the registry.

        Groups registered tools and assigns colours.  Currently uses a
        simple count since TrustRecord doesn't carry category metadata.
        """
        snap = self._ensure_snapshot()
        total = snap.summary.get("total_tools", 0)
        certified = snap.summary.get("certified_tools", 0)
        published = snap.summary.get("published_tools", 0)
        uncertified = max(total - certified, 0)
        unpublished = max(total - published, 0)

        categories = []
        palette = ["#6366f1", "#8b5cf6", "#a78bfa", "#c4b5fd", "#ddd6fe"]
        entries = [
            ("Certified", certified),
            ("Published", published),
            ("Uncertified", uncertified),
            ("Unpublished", unpublished),
            ("Total", total),
        ]
        # Deduplicate and only keep non-zero
        seen: set[str] = set()
        for name, val in entries:
            if val > 0 and name not in seen:
                seen.add(name)
                categories.append(
                    {
                        "name": name,
                        "value": val,
                        "color": palette[len(categories) % len(palette)],
                    }
                )
        return categories or [{"name": "No Tools", "value": 0, "color": "#e5e7eb"}]

    # ── Component health ──────────────────────────────────────

    def build_components(self) -> list[dict[str, Any]]:
        """Build COMPONENTS from snapshot component health."""
        snap = self._ensure_snapshot()
        result = []
        for comp in snap.component_health:
            icon = _COMPONENT_ICONS.get(comp.name, "shield")
            entry: dict[str, Any] = {
                "name": comp.name,
                "status": comp.level.value,
                "icon": icon,
            }
            # Merge metrics into the component dict
            if comp.metrics:
                entry.update(comp.metrics)
            result.append(entry)
        return result

    # ── Timeline events ───────────────────────────────────────

    def build_timeline_events(self) -> list[dict[str, Any]]:
        """Build TIMELINE_EVENTS from dashboard timeline."""
        entries = self.dashboard.get_timeline(limit=20)
        result = []
        for i, entry in enumerate(entries):
            icon = _TIMELINE_TYPE_TO_ICON.get(entry.entry_type.value, "shield")
            result.append(
                {
                    "id": i + 1,
                    "type": entry.entry_type.value,
                    "title": entry.title or entry.description,
                    "severity": entry.severity,
                    "time": _relative_time(entry.timestamp),
                    "icon": icon,
                }
            )
        return result

    # ── Top tools ─────────────────────────────────────────────

    def build_top_tools(self) -> list[dict[str, Any]]:
        """Build TOP_TOOLS from registry and marketplace data.

        Combines trust scores, certification status, and marketplace
        stats into a per-tool summary.
        """
        tools: list[dict[str, Any]] = []

        if not self.dashboard._registry:
            return tools

        all_records = self.dashboard._registry.get_all()
        for record in all_records:
            trust = round(record.trust_score.overall, 2)
            certified = record.is_certified
            name = record.tool_name if hasattr(record, "tool_name") else str(record)

            entry: dict[str, Any] = {
                "name": name,
                "trust": trust,
                "certified": certified,
                "installs": 0,
                "rating": 0.0,
                "status": "certified" if certified else "registered",
            }

            # Enrich from marketplace if available
            if self.dashboard._marketplace:
                listing = self.dashboard._marketplace.get_by_name(name)
                if listing:
                    entry["installs"] = listing.install_count
                    entry["rating"] = round(listing.average_rating, 1)
                    entry["status"] = (
                        listing.status.value
                        if hasattr(listing, "status")
                        else "published"
                    )

            tools.append(entry)

        # Sort by trust score descending
        tools.sort(key=lambda t: t["trust"], reverse=True)
        return tools[:20]  # Top 20

    # ── Health banner ─────────────────────────────────────────

    def build_health_banner(self) -> dict[str, Any]:
        """Build the overall health banner data."""
        snap = self._ensure_snapshot()
        summary = snap.summary

        health_label = {
            "healthy": "Healthy",
            "degraded": "Degraded",
            "critical": "Critical",
            "unknown": "Unknown",
        }

        configured = [
            c for c in snap.component_health if c.level != HealthLevel.UNKNOWN
        ]
        healthy = sum(1 for c in configured if c.level == HealthLevel.HEALTHY)
        total_components = len(configured)

        return {
            "overall_health": snap.overall_health.value,
            "health_label": health_label.get(snap.overall_health.value, "Unknown"),
            "all_operational": snap.critical_count == 0 and snap.degraded_count == 0,
            "healthy_components": healthy,
            "total_components": total_components,
            "compliance_score": round(snap.compliance_score * 100),
            "total_tools": summary.get("total_tools", 0),
            "avg_trust": 0.0,  # computed below
            "revocations": summary.get(
                "active_revocations", summary.get("crl_entries", 0)
            ),
            "federation_peers": summary.get("federation_peers", 0),
        }

    # ── Full export ───────────────────────────────────────────

    def export(self) -> dict[str, Any]:
        """Export all dashboard data as a single dict.

        Returns a dict matching the structure expected by the React
        SecureMCPDashboard component::

            {
                "trust_timeline": [...],
                "compliance_data": [...],
                "tool_categories": [...],
                "components": [...],
                "timeline_events": [...],
                "top_tools": [...],
                "health_banner": {...},
                "generated_at": "...",
            }
        """
        snap = self._ensure_snapshot()
        self.snapshot = snap  # cache for consistency

        top_tools = self.build_top_tools()
        banner = self.build_health_banner()

        # Compute average trust from top_tools
        if top_tools:
            avg = sum(t["trust"] for t in top_tools) / len(top_tools)
            banner["avg_trust"] = round(avg, 2)

        return {
            "trust_timeline": self.build_trust_timeline(),
            "compliance_data": self.build_compliance_data(),
            "tool_categories": self.build_tool_categories(),
            "components": self.build_components(),
            "timeline_events": self.build_timeline_events(),
            "top_tools": top_tools,
            "health_banner": banner,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def export_json(self, indent: int = 2) -> str:
        """Export all dashboard data as a JSON string."""
        return json.dumps(self.export(), indent=indent, default=str)
