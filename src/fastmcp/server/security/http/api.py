"""Security HTTP API — Starlette-based endpoints for the SecureMCP platform.

Registers JSON routes on a FastMCP server to serve dashboard, marketplace,
compliance, audit, trust, and provenance data to frontends.

Usage with FastMCP::

    from fastmcp import FastMCP
    from fastmcp.server.security import SecurityConfig, attach_security
    from fastmcp.server.security.http import mount_security_routes

    server = FastMCP("my-server")
    attach_security(server, SecurityConfig(...))
    mount_security_routes(server)
    # Now GET /security/dashboard, /security/marketplace, etc. are live.

Standalone usage::

    api = SecurityAPI(
        dashboard=security_dashboard,
        marketplace=marketplace,
        registry=registry,
        compliance_reporter=reporter,
        provenance_ledger=ledger,
        event_bus=bus,
    )
    data = api.get_dashboard()
    data = api.get_marketplace()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from fastmcp.server.security.integration import get_security_context
from fastmcp.server.security.policy.serialization import (
    describe_policy_provider,
    policy_provider_from_config,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from fastmcp.server.security.alerts.bus import SecurityEventBus
    from fastmcp.server.security.compliance.reports import ComplianceReporter
    from fastmcp.server.security.dashboard.snapshot import SecurityDashboard
    from fastmcp.server.security.federation.crl import CertificateRevocationList
    from fastmcp.server.security.federation.federation import TrustFederation
    from fastmcp.server.security.gateway.tool_marketplace import ToolMarketplace
    from fastmcp.server.security.policy.audit import PolicyAuditLog
    from fastmcp.server.security.policy.engine import PolicyEngine
    from fastmcp.server.security.policy.governance import PolicyGovernor
    from fastmcp.server.security.policy.monitoring import PolicyMonitor
    from fastmcp.server.security.policy.validator import PolicyValidator
    from fastmcp.server.security.policy.versioning.manager import PolicyVersionManager
    from fastmcp.server.security.provenance.ledger import ProvenanceLedger
    from fastmcp.server.security.registry.registry import TrustRegistry

logger = logging.getLogger(__name__)


@dataclass
class SecurityAPI:
    """Facade for all SecureMCP HTTP API endpoints.

    Each method returns a plain dict (ready for JSON serialization).
    The ``mount_security_routes`` helper wires these to Starlette routes.

    Attributes:
        dashboard: SecurityDashboard for snapshot generation.
        marketplace: ToolMarketplace for listing/search.
        registry: TrustRegistry for trust score lookups.
        compliance_reporter: ComplianceReporter for report generation.
        provenance_ledger: ProvenanceLedger for audit trail queries.
        federation: TrustFederation for peer status.
        crl: CertificateRevocationList for revocation queries.
        event_bus: SecurityEventBus for recent events.
    """

    dashboard: SecurityDashboard | None = None
    marketplace: ToolMarketplace | None = None
    registry: TrustRegistry | None = None
    compliance_reporter: ComplianceReporter | None = None
    provenance_ledger: ProvenanceLedger | None = None
    federation: TrustFederation | None = None
    crl: CertificateRevocationList | None = None
    event_bus: SecurityEventBus | None = None
    policy_engine: PolicyEngine | None = None
    policy_audit_log: PolicyAuditLog | None = None
    policy_version_manager: PolicyVersionManager | None = None
    policy_validator: PolicyValidator | None = None
    policy_monitor: PolicyMonitor | None = None
    policy_governor: PolicyGovernor | None = None

    @classmethod
    def from_context(cls, ctx: Any) -> SecurityAPI:
        """Create a SecurityAPI from a SecurityContext.

        This is the recommended way to construct a SecurityAPI when
        using the SecurityOrchestrator::

            ctx = SecurityOrchestrator.bootstrap(config)
            api = SecurityAPI.from_context(ctx)
            mount_security_routes(server, api=api)

        Args:
            ctx: A SecurityContext returned by SecurityOrchestrator.bootstrap().

        Returns:
            A SecurityAPI wired to all components in the context.
        """
        return cls(
            dashboard=getattr(ctx, "dashboard", None),
            marketplace=getattr(ctx, "tool_marketplace", None),
            registry=getattr(ctx, "registry", None),
            compliance_reporter=getattr(ctx, "compliance_reporter", None),
            provenance_ledger=getattr(ctx, "provenance_ledger", None),
            federation=getattr(ctx, "federation", None),
            crl=getattr(ctx, "crl", None),
            event_bus=getattr(ctx, "event_bus", None),
            policy_engine=getattr(ctx, "policy_engine", None),
            policy_audit_log=getattr(ctx, "policy_audit_log", None),
            policy_version_manager=getattr(ctx, "policy_version_manager", None),
            policy_validator=getattr(ctx, "policy_validator", None),
            policy_monitor=getattr(ctx, "policy_monitor", None),
            policy_governor=getattr(ctx, "policy_governor", None),
        )

    # ── Dashboard ─────────────────────────────────────────────

    def get_dashboard(self) -> dict[str, Any]:
        """Generate full dashboard JSON for the React frontend."""
        if self.dashboard is None:
            return {"error": "Dashboard not configured", "status": 503}

        from fastmcp.server.security.dashboard.data_bridge import DashboardDataBridge

        bridge = DashboardDataBridge(dashboard=self.dashboard)
        return bridge.export()

    # ── Marketplace ───────────────────────────────────────────

    def get_marketplace(
        self,
        query: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        """Generate marketplace JSON for the React frontend."""
        if self.marketplace is None:
            return {"error": "Marketplace not configured", "status": 503}

        from fastmcp.server.security.gateway.marketplace_bridge import (
            MarketplaceDataBridge,
        )

        bridge = MarketplaceDataBridge(
            marketplace=self.marketplace,
            trust_registry=self.registry,
        )
        return bridge.export()

    def get_marketplace_listing(self, listing_id: str) -> dict[str, Any]:
        """Get detail view for a single marketplace listing."""
        if self.marketplace is None:
            return {"error": "Marketplace not configured", "status": 503}

        from fastmcp.server.security.gateway.marketplace_bridge import (
            MarketplaceDataBridge,
        )

        bridge = MarketplaceDataBridge(
            marketplace=self.marketplace,
            trust_registry=self.registry,
        )
        detail = bridge.build_listing_detail(listing_id)
        if detail is None:
            return {"error": "Listing not found", "status": 404}
        return detail

    def marketplace_install(
        self,
        listing_id: str,
        *,
        installer_id: str = "",
        version: str | None = None,
        verify_signature: bool = False,
    ) -> dict[str, Any]:
        """Install a tool from the marketplace."""
        if self.marketplace is None:
            return {"error": "Marketplace not configured", "status": 503}

        record = self.marketplace.install(
            listing_id,
            installer_id=installer_id,
            version=version,
            verify_signature=verify_signature,
        )
        if record is None:
            return {
                "error": "Install failed — listing not found or signature verification failed",
                "status": 400,
            }

        return {
            "install_id": record.install_id,
            "listing_id": listing_id,
            "version": record.version,
            "signature_verified": record.signature_verified,
            "installed_at": record.installed_at.isoformat(),
        }

    def marketplace_uninstall(
        self,
        listing_id: str,
        *,
        installer_id: str = "",
    ) -> dict[str, Any]:
        """Uninstall a tool."""
        if self.marketplace is None:
            return {"error": "Marketplace not configured", "status": 503}

        success = self.marketplace.uninstall(listing_id, installer_id=installer_id)
        return {"success": success, "listing_id": listing_id}

    def marketplace_moderate(
        self,
        listing_id: str,
        *,
        moderator_id: str,
        action: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """Moderate a tool listing."""
        if self.marketplace is None:
            return {"error": "Marketplace not configured", "status": 503}

        from fastmcp.server.security.gateway.tool_marketplace import ModerationAction

        try:
            mod_action = ModerationAction(action)
        except ValueError:
            return {"error": f"Invalid moderation action: {action}", "status": 400}

        decision = self.marketplace.moderate(
            listing_id,
            moderator_id=moderator_id,
            action=mod_action,
            reason=reason,
        )
        if decision is None:
            return {"error": "Listing not found", "status": 404}

        return decision.to_dict()

    def marketplace_moderation_queue(self) -> dict[str, Any]:
        """Get the moderation queue."""
        if self.marketplace is None:
            return {"error": "Marketplace not configured", "status": 503}

        from fastmcp.server.security.gateway.marketplace_bridge import (
            MarketplaceDataBridge,
        )

        bridge = MarketplaceDataBridge(
            marketplace=self.marketplace,
            trust_registry=self.registry,
        )
        return {"queue": bridge.build_moderation_queue()}

    def marketplace_version_history(self, listing_id: str) -> dict[str, Any]:
        """Get version history for a listing."""
        if self.marketplace is None:
            return {"error": "Marketplace not configured", "status": 503}

        from fastmcp.server.security.gateway.marketplace_bridge import (
            MarketplaceDataBridge,
        )

        bridge = MarketplaceDataBridge(
            marketplace=self.marketplace,
            trust_registry=self.registry,
        )
        return {"versions": bridge.build_version_history(listing_id)}

    def marketplace_yank_version(
        self,
        listing_id: str,
        version: str,
        *,
        reason: str = "",
    ) -> dict[str, Any]:
        """Yank a specific version."""
        if self.marketplace is None:
            return {"error": "Marketplace not configured", "status": 503}

        success = self.marketplace.yank_version(listing_id, version, reason=reason)
        return {"success": success, "listing_id": listing_id, "version": version}

    # ── Compliance ────────────────────────────────────────────

    def get_compliance_report(self, report_type: str = "full") -> dict[str, Any]:
        """Generate a compliance report."""
        if self.compliance_reporter is None:
            return {"error": "Compliance reporter not configured", "status": 503}

        from fastmcp.server.security.compliance.reports import ReportType

        type_map = {
            "full": ReportType.FULL,
            "summary": ReportType.SUMMARY,
            "findings": ReportType.FINDINGS_ONLY,
            "executive": ReportType.EXECUTIVE,
        }
        rt = type_map.get(report_type, ReportType.FULL)
        report = self.compliance_reporter.generate_report(report_type=rt)
        return report.to_dict()

    # ── Trust Registry ────────────────────────────────────────

    def get_trust_registry(self) -> dict[str, Any]:
        """Get trust registry overview."""
        if self.registry is None:
            return {"error": "Registry not configured", "status": 503}

        records = self.registry.get_all()
        tools = []
        for record in records:
            tools.append(
                {
                    "tool_name": record.tool_name,
                    "trust_score": round(record.trust_score.overall, 3),
                    "is_certified": record.is_certified,
                    "registered_at": record.registered_at.isoformat(),
                    "tags": sorted(record.tags) if record.tags else [],
                }
            )
        tools.sort(key=lambda t: t["trust_score"], reverse=True)

        return {
            "total_tools": self.registry.record_count,
            "tools": tools,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_trust_score(self, tool_name: str) -> dict[str, Any]:
        """Get trust score for a specific tool."""
        if self.registry is None:
            return {"error": "Registry not configured", "status": 503}

        score = self.registry.get_trust_score(tool_name)
        if score is None:
            return {"error": f"Tool '{tool_name}' not found", "status": 404}

        return {
            "tool_name": tool_name,
            "overall": round(score.overall, 3),
            "certification_component": round(score.certification_component, 3),
            "reputation_component": round(score.reputation_component, 3),
            "age_component": round(score.age_component, 3),
        }

    # ── Federation ────────────────────────────────────────────

    def get_federation_status(self) -> dict[str, Any]:
        """Get federation peer status."""
        if self.federation is None:
            return {"error": "Federation not configured", "status": 503}

        return self.federation.get_federation_status()

    # ── CRL ───────────────────────────────────────────────────

    def get_revocations(self) -> dict[str, Any]:
        """Get certificate revocation list."""
        if self.crl is None:
            return {"error": "CRL not configured", "status": 503}

        entries = self.crl.get_all_entries()
        return {
            "total_entries": len(entries),
            "entries": [e.to_dict() for e in entries],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def is_revoked(self, tool_name: str) -> dict[str, Any]:
        """Check if a tool is revoked."""
        if self.crl is None:
            return {"error": "CRL not configured", "status": 503}

        revoked = self.crl.is_revoked(tool_name)
        return {"tool_name": tool_name, "is_revoked": revoked}

    # ── Provenance ────────────────────────────────────────────

    def get_provenance(
        self,
        resource_id: str | None = None,
        actor_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Query provenance records."""
        if self.provenance_ledger is None:
            return {"error": "Provenance ledger not configured", "status": 503}

        records = self.provenance_ledger.get_records(
            resource_id=resource_id,
            actor_id=actor_id,
            limit=limit,
        )
        return {
            "total_records": len(records),
            "records": [r.to_dict() for r in records],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    # ── Policy ────────────────────────────────────────────────

    def get_policy_status(self) -> dict[str, Any]:
        """Get policy engine status and provider list."""
        if self.policy_engine is None:
            return {"error": "Policy engine not configured", "status": 503}

        providers = []
        for index, provider in enumerate(self.policy_engine.providers):
            providers.append(describe_policy_provider(provider, index=index))

        versioning: dict[str, Any] | None = None
        if self.policy_version_manager is not None:
            current = self.policy_version_manager.current_version
            versioning = {
                "enabled": True,
                "policy_set_id": self.policy_version_manager.policy_set_id,
                "version_count": self.policy_version_manager.version_count,
                "current_version": current.version_number if current else None,
            }

        governance: dict[str, Any] | None = None
        if self.policy_governor is not None:
            governance = {
                "enabled": True,
                "proposal_count": len(self.policy_governor.proposals),
                "pending_count": len(self.policy_governor.pending_proposals),
                "require_simulation": self.policy_governor.require_simulation,
                "require_approval": self.policy_governor.require_approval,
            }

        return {
            "evaluation_count": self.policy_engine.evaluation_count,
            "deny_count": self.policy_engine.deny_count,
            "fail_closed": self.policy_engine.fail_closed,
            "allow_hot_swap": self.policy_engine.allow_hot_swap,
            "provider_count": len(providers),
            "providers": providers,
            "has_audit_log": self.policy_audit_log is not None,
            "versioning": versioning,
            "governance": governance,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_policy_audit(
        self,
        actor_id: str | None = None,
        resource_id: str | None = None,
        decision: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Query the policy audit log."""
        if self.policy_audit_log is None:
            return {"error": "Policy audit log not configured", "status": 503}

        from fastmcp.server.security.policy.provider import PolicyDecision

        decision_filter = None
        if decision is not None:
            decision_map = {
                "allow": PolicyDecision.ALLOW,
                "deny": PolicyDecision.DENY,
                "defer": PolicyDecision.DEFER,
            }
            decision_filter = decision_map.get(decision.lower())

        entries = self.policy_audit_log.query(
            actor_id=actor_id,
            resource_id=resource_id,
            decision=decision_filter,
            limit=limit,
        )

        return {
            "total_entries": len(entries),
            "entries": [e.to_dict() for e in entries],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_policy_audit_statistics(self) -> dict[str, Any]:
        """Get aggregate statistics from the policy audit log."""
        if self.policy_audit_log is None:
            return {"error": "Policy audit log not configured", "status": 503}

        return self.policy_audit_log.get_statistics()

    async def simulate_policy(self, scenarios_data: list[dict]) -> dict[str, Any]:
        """Run a policy simulation against the current engine."""
        if self.policy_engine is None:
            return {"error": "Policy engine not configured", "status": 503}

        from fastmcp.server.security.policy.simulation import simulate

        scenarios = self._build_scenarios(scenarios_data)
        report = await simulate(self.policy_engine, scenarios)
        return report.to_dict()

    def get_policy_schema(self) -> dict[str, Any]:
        """Get the declarative policy schema for the editor UI."""
        from fastmcp.server.security.policy.declarative import dump_policy_schema

        return dump_policy_schema()

    # ── Policy Versioning ────────────────────────────────────

    def get_policy_versions(self) -> dict[str, Any]:
        """List all policy versions."""
        if self.policy_version_manager is None:
            return {"error": "Policy versioning not configured", "status": 503}

        from fastmcp.server.security.policy.versioning.models import (
            policy_version_to_dict,
        )

        versions = self.policy_version_manager.list_versions()
        current = self.policy_version_manager.current_version

        return {
            "policy_set_id": self.policy_version_manager.policy_set_id,
            "version_count": len(versions),
            "current_version": current.version_number if current else None,
            "versions": [policy_version_to_dict(v) for v in versions],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def rollback_policy_version(
        self, version_number: int, reason: str = ""
    ) -> dict[str, Any]:
        """Rollback to a specific policy version.

        Args:
            version_number: The 1-based version number to rollback to.
            reason: Reason for the rollback.
        """
        if self.policy_version_manager is None:
            return {"error": "Policy versioning not configured", "status": 503}

        from fastmcp.server.security.policy.versioning.models import (
            policy_version_to_dict,
        )

        try:
            version = self.policy_version_manager.rollback_to(
                version_number=version_number,
                reason=reason,
            )
        except ValueError as e:
            return {"error": str(e), "status": 400}

        if self.policy_engine is None:
            return {"error": "Policy engine not configured", "status": 503}

        try:
            await self.policy_engine.restore_version(version.policy_data, reason=reason)
        except Exception as e:
            return {"error": str(e), "status": 400}

        return {
            "status": "rolled_back",
            "version": policy_version_to_dict(version),
            "policy": self.get_policy_status(),
        }

    async def add_policy_provider(
        self,
        config: dict[str, Any],
        *,
        reason: str = "",
        author: str = "api",
    ) -> dict[str, Any]:
        """Add a new live policy provider from a config object."""

        if self.policy_engine is None:
            return {"error": "Policy engine not configured", "status": 503}

        validation_error = self._validate_policy_mutation(
            action="add",
            config=config,
            target_index=None,
        )
        if validation_error is not None:
            return validation_error

        provider = policy_provider_from_config(config)
        await self.policy_engine.add_provider(
            provider,
            reason=reason or "API add",
            author=author,
        )
        providers = self.get_policy_status().get("providers", [])
        created = providers[-1] if providers else None
        return {
            "status": "created",
            "provider": created,
            "policy": self.get_policy_status(),
        }

    async def update_policy_provider(
        self,
        index: int,
        config: dict[str, Any],
        *,
        reason: str = "",
        author: str = "api",
    ) -> dict[str, Any]:
        """Replace a live provider at a specific index."""

        if self.policy_engine is None:
            return {"error": "Policy engine not configured", "status": 503}

        validation_error = self._validate_policy_mutation(
            action="swap",
            config=config,
            target_index=index,
        )
        if validation_error is not None:
            return validation_error

        provider = policy_provider_from_config(config)
        try:
            record = await self.policy_engine.hot_swap(
                index,
                provider,
                reason=reason or "API edit",
                author=author,
            )
        except IndexError as e:
            return {"error": str(e), "status": 400}

        provider_payload = self.get_policy_status().get("providers", [])
        updated = provider_payload[index] if index < len(provider_payload) else None
        return {
            "status": "updated",
            "swap": {
                "old_policy_id": record.old_policy_id,
                "old_version": record.old_version,
                "new_policy_id": record.new_policy_id,
                "new_version": record.new_version,
                "reason": record.reason,
                "swapped_at": record.swapped_at.isoformat(),
            },
            "provider": updated,
            "policy": self.get_policy_status(),
        }

    async def delete_policy_provider(
        self,
        index: int,
        *,
        reason: str = "",
        author: str = "api",
    ) -> dict[str, Any]:
        """Remove a live provider by index."""

        if self.policy_engine is None:
            return {"error": "Policy engine not configured", "status": 503}

        validation_error = self._validate_policy_mutation(
            action="remove",
            config=None,
            target_index=index,
        )
        if validation_error is not None:
            return validation_error

        try:
            removed = await self.policy_engine.remove_provider(
                index,
                reason=reason or "API delete",
                author=author,
            )
        except IndexError as e:
            return {"error": str(e), "status": 400}

        return {
            "status": "deleted",
            "provider": describe_policy_provider(removed, index=index),
            "policy": self.get_policy_status(),
        }

    def diff_policy_versions(self, v1: int, v2: int) -> dict[str, Any]:
        """Get differences between two policy versions.

        Args:
            v1: First version number (1-based).
            v2: Second version number (1-based).
        """
        if self.policy_version_manager is None:
            return {"error": "Policy versioning not configured", "status": 503}

        try:
            diff = self.policy_version_manager.diff(v1, v2)
        except ValueError as e:
            return {"error": str(e), "status": 400}

        return {
            "v1": v1,
            "v2": v2,
            "diff": diff,
        }

    # ── Validation ─────────────────────────────────────────────

    def validate_policy(self, config: dict) -> dict[str, Any]:
        """Validate a declarative policy config.

        Args:
            config: The policy dict to validate.

        Returns:
            ValidationResult as a dict.
        """
        if self.policy_validator is None:
            return {"error": "Policy validator not configured", "status": 503}

        result = self.policy_validator.validate_declarative(config)
        return result.to_dict()

    def _validate_policy_mutation(
        self,
        *,
        action: str,
        config: dict[str, Any] | None,
        target_index: int | None,
    ) -> dict[str, Any] | None:
        """Validate a proposed live mutation before applying it."""

        if self.policy_engine is None:
            return {"error": "Policy engine not configured", "status": 503}

        current = list(self.policy_engine.providers)
        action_name = action.strip().lower()

        new_provider = None
        if config is not None:
            if (
                self.policy_validator is not None
                and config.get("type") != "python_class"
            ):
                result = self.policy_validator.validate_declarative(config)
                if not result.valid:
                    return {
                        "error": "Policy config failed validation.",
                        "status": 400,
                        "validation": result.to_dict(),
                    }
            try:
                new_provider = policy_provider_from_config(config)
            except Exception as exc:
                return {"error": str(exc), "status": 400}

        try:
            if action_name == "add":
                assert new_provider is not None
                current.append(new_provider)
            elif action_name == "swap":
                if target_index is None:
                    raise ValueError("Missing target index for swap.")
                assert new_provider is not None
                current[target_index] = new_provider
            elif action_name == "remove":
                if target_index is None:
                    raise ValueError("Missing target index for remove.")
                current.pop(target_index)
            else:
                raise ValueError(f"Unsupported mutation action: {action}")
        except (IndexError, ValueError) as exc:
            return {"error": str(exc), "status": 400}

        if self.policy_validator is None:
            return None

        semantic_result = self.policy_validator.validate_providers(current)
        if semantic_result.valid:
            return None

        return {
            "error": "Policy set failed semantic validation.",
            "status": 400,
            "validation": semantic_result.to_dict(),
        }

    def validate_providers(self) -> dict[str, Any]:
        """Validate the current engine providers for semantic issues."""
        if self.policy_validator is None:
            return {"error": "Policy validator not configured", "status": 503}
        if self.policy_engine is None:
            return {"error": "Policy engine not configured", "status": 503}

        result = self.policy_validator.validate_providers(self.policy_engine.providers)
        return result.to_dict()

    # ── Monitoring ────────────────────────────────────────────

    def get_policy_metrics(self) -> dict[str, Any]:
        """Get current policy monitoring metrics."""
        if self.policy_monitor is None:
            return {"error": "Policy monitor not configured", "status": 503}

        return self.policy_monitor.get_metrics()

    def get_policy_alerts(self, limit: int = 50) -> dict[str, Any]:
        """Get recent monitoring alerts.

        Args:
            limit: Maximum number of alerts to return.
        """
        if self.policy_monitor is None:
            return {"error": "Policy monitor not configured", "status": 503}

        alerts = self.policy_monitor.get_recent_alerts(limit=limit)
        return {
            "total_alerts": len(alerts),
            "alerts": alerts,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    # ── Governance ────────────────────────────────────────────

    def get_governance_proposals(self) -> dict[str, Any]:
        """List all governance proposals."""
        if self.policy_governor is None:
            return {"error": "Policy governance not configured", "status": 503}

        proposals = self.policy_governor.proposals
        current_version = (
            self.policy_version_manager.current_version
            if self.policy_version_manager is not None
            else None
        )
        serialized = [self._serialize_governance_proposal(p) for p in proposals]
        terminal_statuses = {"deployed", "rejected", "withdrawn"}
        return {
            "total_proposals": len(proposals),
            "pending_count": len(self.policy_governor.pending_proposals),
            "require_simulation": self.policy_governor.require_simulation,
            "require_approval": self.policy_governor.require_approval,
            "current_version": (
                current_version.version_number if current_version is not None else None
            ),
            "stale_count": sum(
                1
                for proposal in serialized
                if proposal.get("is_stale") is True
                and proposal.get("status") not in terminal_statuses
            ),
            "proposals": serialized,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_governance_proposal(self, proposal_id: str) -> dict[str, Any]:
        """Get a single proposal by ID."""
        if self.policy_governor is None:
            return {"error": "Policy governance not configured", "status": 503}

        proposal = self.policy_governor.get_proposal(proposal_id)
        if proposal is None:
            return {"error": f"Proposal not found: {proposal_id}", "status": 404}

        return self._serialize_governance_proposal(proposal)

    async def create_governance_proposal(
        self,
        *,
        action: str,
        config: dict[str, Any] | None,
        target_index: int | None,
        description: str = "",
        author: str = "api",
    ) -> dict[str, Any]:
        """Create and validate a governance proposal."""
        if self.policy_governor is None:
            return {"error": "Policy governance not configured", "status": 503}

        provider: Any = None
        action_name = action.strip().lower()

        if action_name in {"add", "swap"}:
            if config is None:
                return {
                    "error": "`config` is required for add and swap proposals.",
                    "status": 400,
                }

            if (
                self.policy_validator is not None
                and config.get("type") != "python_class"
            ):
                result = self.policy_validator.validate_declarative(config)
                if not result.valid:
                    return {
                        "error": "Policy config failed validation.",
                        "status": 400,
                        "validation": result.to_dict(),
                    }
            try:
                provider = policy_provider_from_config(config)
            except Exception as exc:
                return {"error": str(exc), "status": 400}

        try:
            if action_name == "add":
                assert provider is not None
                proposal = self.policy_governor.propose_add(
                    provider,
                    author=author,
                    description=description,
                )
            elif action_name == "swap":
                if target_index is None:
                    raise ValueError("`target_index` is required for swap proposals.")
                assert provider is not None
                proposal = self.policy_governor.propose_swap(
                    target_index,
                    provider,
                    author=author,
                    description=description,
                )
            elif action_name == "remove":
                if target_index is None:
                    raise ValueError("`target_index` is required for remove proposals.")
                proposal = self.policy_governor.propose_remove(
                    target_index,
                    author=author,
                    description=description,
                )
            else:
                raise ValueError(f"Unsupported proposal action: {action}")
            validation = self.policy_governor.validate_proposal(proposal.proposal_id)
        except (IndexError, KeyError, ValueError) as exc:
            return {"error": str(exc), "status": 400}

        return {
            "status": "created",
            "proposal": self._serialize_governance_proposal(proposal),
            "validation": validation.to_dict(),
            "governance": self.get_governance_proposals(),
        }

    def approve_governance_proposal(
        self,
        proposal_id: str,
        *,
        approver: str = "api",
        note: str = "",
    ) -> dict[str, Any]:
        """Approve a governance proposal."""
        if self.policy_governor is None:
            return {"error": "Policy governance not configured", "status": 503}

        try:
            proposal = self.policy_governor.approve(
                proposal_id,
                approver=approver,
                note=note,
            )
        except KeyError as exc:
            return {"error": str(exc), "status": 404}
        except ValueError as exc:
            return {"error": str(exc), "status": 400}

        return {
            "status": "approved",
            "proposal": self._serialize_governance_proposal(proposal),
            "governance": self.get_governance_proposals(),
        }

    async def deploy_governance_proposal(
        self,
        proposal_id: str,
        *,
        actor: str = "api",
        note: str = "",
    ) -> dict[str, Any]:
        """Deploy an approved governance proposal."""
        if self.policy_governor is None:
            return {"error": "Policy governance not configured", "status": 503}

        try:
            proposal = await self.policy_governor.deploy(
                proposal_id,
                actor=actor,
                note=note,
            )
        except KeyError as exc:
            return {"error": str(exc), "status": 404}
        except ValueError as exc:
            return {"error": str(exc), "status": 400}

        return {
            "status": "deployed",
            "proposal": self._serialize_governance_proposal(proposal),
            "policy": self.get_policy_status(),
            "versions": self.get_policy_versions(),
            "governance": self.get_governance_proposals(),
        }

    async def simulate_governance_proposal(
        self,
        proposal_id: str,
        *,
        scenarios_data: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Run simulation for a governance proposal."""
        if self.policy_governor is None:
            return {"error": "Policy governance not configured", "status": 503}

        scenarios = self._build_scenarios(scenarios_data)
        try:
            report = await self.policy_governor.simulate_proposal(
                proposal_id, scenarios
            )
        except KeyError as exc:
            return {"error": str(exc), "status": 404}
        except ValueError as exc:
            return {"error": str(exc), "status": 400}

        proposal = self.policy_governor.get_proposal(proposal_id)
        payload: dict[str, Any] = {
            "status": "simulated",
            "simulation": report.to_dict(),
            "governance": self.get_governance_proposals(),
        }
        if proposal is not None:
            payload["proposal"] = self._serialize_governance_proposal(proposal)
        return payload

    def reject_governance_proposal(
        self,
        proposal_id: str,
        *,
        reason: str = "",
        actor: str = "api",
    ) -> dict[str, Any]:
        """Reject a governance proposal."""
        if self.policy_governor is None:
            return {"error": "Policy governance not configured", "status": 503}

        try:
            proposal = self.policy_governor.reject(
                proposal_id,
                reason=reason,
                actor=actor,
            )
        except KeyError as exc:
            return {"error": str(exc), "status": 404}
        except ValueError as exc:
            return {"error": str(exc), "status": 400}

        return {
            "status": "rejected",
            "proposal": self._serialize_governance_proposal(proposal),
            "governance": self.get_governance_proposals(),
        }

    def withdraw_governance_proposal(
        self,
        proposal_id: str,
        *,
        actor: str = "api",
        note: str = "",
    ) -> dict[str, Any]:
        """Withdraw a governance proposal."""
        if self.policy_governor is None:
            return {"error": "Policy governance not configured", "status": 503}

        try:
            proposal = self.policy_governor.withdraw(
                proposal_id,
                actor=actor,
                note=note,
            )
        except KeyError as exc:
            return {"error": str(exc), "status": 404}
        except ValueError as exc:
            return {"error": str(exc), "status": 400}

        return {
            "status": "withdrawn",
            "proposal": self._serialize_governance_proposal(proposal),
            "governance": self.get_governance_proposals(),
        }

    def assign_governance_proposal(
        self,
        proposal_id: str,
        *,
        reviewer: str,
        actor: str = "api",
        note: str = "",
    ) -> dict[str, Any]:
        """Assign ownership of a governance proposal."""
        if self.policy_governor is None:
            return {"error": "Policy governance not configured", "status": 503}

        try:
            proposal = self.policy_governor.assign(
                proposal_id,
                reviewer=reviewer,
                actor=actor,
                note=note,
            )
        except KeyError as exc:
            return {"error": str(exc), "status": 404}
        except ValueError as exc:
            return {"error": str(exc), "status": 400}

        return {
            "status": "assigned",
            "proposal": self._serialize_governance_proposal(proposal),
            "governance": self.get_governance_proposals(),
        }

    def _serialize_governance_proposal(self, proposal: Any) -> dict[str, Any]:
        """Return a UI-friendly governance proposal payload."""
        payload = proposal.to_dict()
        current_version = (
            self.policy_version_manager.current_version
            if self.policy_version_manager is not None
            else None
        )
        live_version_number = (
            current_version.version_number if current_version is not None else None
        )
        payload["live_version_number"] = live_version_number
        payload["is_stale"] = (
            payload.get("base_version_number") is not None
            and live_version_number is not None
            and payload["base_version_number"] != live_version_number
        )
        if proposal.new_provider is not None:
            payload["provider"] = describe_policy_provider(
                proposal.new_provider,
                index=proposal.target_index
                if proposal.target_index is not None
                else -1,
            )
        return payload

    @staticmethod
    def _build_scenarios(scenarios_data: list[dict[str, Any]]) -> list[Any]:
        """Convert plain payloads to simulation scenarios."""
        from fastmcp.server.security.policy.simulation import Scenario

        scenarios: list[Scenario] = []
        for item in scenarios_data:
            scenarios.append(
                Scenario(
                    resource_id=str(item.get("resource_id", "unknown")),
                    action=str(item.get("action", "call_tool")),
                    actor_id=str(item.get("actor_id", "sim-actor")),
                    metadata=dict(item.get("metadata", {})),
                    tags=frozenset(item.get("tags", [])),
                    label=str(item.get("label", "")),
                )
            )
        return scenarios

    # ── Health ────────────────────────────────────────────────

    def get_health(self) -> dict[str, Any]:
        """Quick health check for the security layer."""
        components: dict[str, str] = {}

        if self.dashboard:
            components["dashboard"] = "ok"
        if self.marketplace:
            components["marketplace"] = "ok"
        if self.registry:
            components["registry"] = "ok"
        if self.compliance_reporter:
            components["compliance"] = "ok"
        if self.federation:
            components["federation"] = "ok"
        if self.crl:
            components["crl"] = "ok"
        if self.provenance_ledger:
            components["provenance"] = "ok"
        if self.event_bus:
            components["event_bus"] = "ok"
        if self.policy_engine:
            components["policy_engine"] = "ok"
        if self.policy_audit_log:
            components["policy_audit_log"] = "ok"
        if self.policy_version_manager:
            components["policy_versioning"] = "ok"
        if self.policy_validator:
            components["policy_validator"] = "ok"
        if self.policy_monitor:
            components["policy_monitor"] = "ok"
        if self.policy_governor:
            components["policy_governor"] = "ok"

        return {
            "status": "healthy" if components else "unconfigured",
            "components": components,
            "component_count": len(components),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# ── Route mounting ──────────────────────────────────────────────


def mount_security_routes(
    server: FastMCP,
    *,
    api: SecurityAPI | None = None,
    prefix: str = "/security",
) -> SecurityAPI:
    """Mount SecureMCP HTTP routes on a FastMCP server.

    If no ``api`` is provided, one is auto-constructed from the server's
    attached SecurityContext.

    Args:
        server: The FastMCP server instance.
        prefix: URL prefix for all security routes (default ``/security``).
        api: Optional pre-configured SecurityAPI. If None, built from
            the server's security context.

    Returns:
        The SecurityAPI instance (for further customization).

    Example::

        from fastmcp import FastMCP
        from fastmcp.server.security import SecurityConfig, attach_security
        from fastmcp.server.security.http import mount_security_routes

        server = FastMCP("secure-server")
        attach_security(server, SecurityConfig(...))
        api = mount_security_routes(server)
        server.run(transport="streamable-http")
    """
    if api is None:
        api = _build_api_from_server(server)

    # Dashboard
    @server.custom_route(f"{prefix}/dashboard", methods=["GET"])
    async def dashboard_endpoint(request: Request) -> JSONResponse:
        return JSONResponse(api.get_dashboard())

    # Marketplace
    @server.custom_route(f"{prefix}/marketplace", methods=["GET"])
    async def marketplace_endpoint(request: Request) -> JSONResponse:
        query = request.query_params.get("q")
        category = request.query_params.get("category")
        return JSONResponse(api.get_marketplace(query=query, category=category))

    @server.custom_route(f"{prefix}/marketplace/{{listing_id}}", methods=["GET"])
    async def marketplace_detail_endpoint(request: Request) -> JSONResponse:
        lid = request.path_params.get("listing_id", "")
        return JSONResponse(api.get_marketplace_listing(lid))

    @server.custom_route(
        f"{prefix}/marketplace/{{listing_id}}/install", methods=["POST"]
    )
    async def marketplace_install_endpoint(request: Request) -> JSONResponse:
        lid = request.path_params.get("listing_id", "")
        try:
            body = await request.json()
        except Exception:
            body = {}
        return JSONResponse(
            api.marketplace_install(
                lid,
                installer_id=body.get("installer_id", ""),
                version=body.get("version"),
                verify_signature=body.get("verify_signature", False),
            )
        )

    @server.custom_route(
        f"{prefix}/marketplace/{{listing_id}}/uninstall", methods=["POST"]
    )
    async def marketplace_uninstall_endpoint(request: Request) -> JSONResponse:
        lid = request.path_params.get("listing_id", "")
        try:
            body = await request.json()
        except Exception:
            body = {}
        return JSONResponse(
            api.marketplace_uninstall(lid, installer_id=body.get("installer_id", ""))
        )

    @server.custom_route(
        f"{prefix}/marketplace/{{listing_id}}/moderate", methods=["POST"]
    )
    async def marketplace_moderate_endpoint(request: Request) -> JSONResponse:
        lid = request.path_params.get("listing_id", "")
        try:
            body = await request.json()
        except Exception:
            body = {}
        return JSONResponse(
            api.marketplace_moderate(
                lid,
                moderator_id=body.get("moderator_id", ""),
                action=body.get("action", ""),
                reason=body.get("reason", ""),
            )
        )

    @server.custom_route(f"{prefix}/marketplace/moderation", methods=["GET"])
    async def marketplace_moderation_queue_endpoint(request: Request) -> JSONResponse:
        return JSONResponse(api.marketplace_moderation_queue())

    @server.custom_route(
        f"{prefix}/marketplace/{{listing_id}}/versions", methods=["GET"]
    )
    async def marketplace_versions_endpoint(request: Request) -> JSONResponse:
        lid = request.path_params.get("listing_id", "")
        return JSONResponse(api.marketplace_version_history(lid))

    @server.custom_route(
        f"{prefix}/marketplace/{{listing_id}}/versions/{{version}}/yank",
        methods=["POST"],
    )
    async def marketplace_yank_endpoint(request: Request) -> JSONResponse:
        lid = request.path_params.get("listing_id", "")
        ver = request.path_params.get("version", "")
        try:
            body = await request.json()
        except Exception:
            body = {}
        return JSONResponse(
            api.marketplace_yank_version(lid, ver, reason=body.get("reason", ""))
        )

    # Compliance
    @server.custom_route(f"{prefix}/compliance", methods=["GET"])
    async def compliance_endpoint(request: Request) -> JSONResponse:
        report_type = request.query_params.get("type", "full")
        return JSONResponse(api.get_compliance_report(report_type=report_type))

    # Trust registry
    @server.custom_route(f"{prefix}/trust", methods=["GET"])
    async def trust_registry_endpoint(request: Request) -> JSONResponse:
        return JSONResponse(api.get_trust_registry())

    @server.custom_route(f"{prefix}/trust/{{tool_name}}", methods=["GET"])
    async def trust_score_endpoint(request: Request) -> JSONResponse:
        name = request.path_params.get("tool_name", "")
        return JSONResponse(api.get_trust_score(name))

    # Federation
    @server.custom_route(f"{prefix}/federation", methods=["GET"])
    async def federation_endpoint(request: Request) -> JSONResponse:
        return JSONResponse(api.get_federation_status())

    # CRL
    @server.custom_route(f"{prefix}/revocations", methods=["GET"])
    async def revocations_endpoint(request: Request) -> JSONResponse:
        return JSONResponse(api.get_revocations())

    @server.custom_route(f"{prefix}/revocations/{{tool_name}}", methods=["GET"])
    async def revocation_check_endpoint(request: Request) -> JSONResponse:
        name = request.path_params.get("tool_name", "")
        return JSONResponse(api.is_revoked(name))

    # Provenance
    @server.custom_route(f"{prefix}/provenance", methods=["GET"])
    async def provenance_endpoint(request: Request) -> JSONResponse:
        resource = request.query_params.get("resource")
        actor = request.query_params.get("actor")
        limit = int(request.query_params.get("limit", "50"))
        return JSONResponse(
            api.get_provenance(resource_id=resource, actor_id=actor, limit=limit)
        )

    # Policy
    @server.custom_route(f"{prefix}/policy", methods=["GET"])
    async def policy_status_endpoint(request: Request) -> JSONResponse:
        return JSONResponse(api.get_policy_status())

    @server.custom_route(f"{prefix}/policy/audit", methods=["GET"])
    async def policy_audit_endpoint(request: Request) -> JSONResponse:
        actor = request.query_params.get("actor")
        resource = request.query_params.get("resource")
        decision = request.query_params.get("decision")
        limit = int(request.query_params.get("limit", "50"))
        return JSONResponse(
            api.get_policy_audit(
                actor_id=actor,
                resource_id=resource,
                decision=decision,
                limit=limit,
            )
        )

    @server.custom_route(f"{prefix}/policy/audit/stats", methods=["GET"])
    async def policy_audit_stats_endpoint(request: Request) -> JSONResponse:
        return JSONResponse(api.get_policy_audit_statistics())

    @server.custom_route(f"{prefix}/policy/simulate", methods=["POST"])
    async def policy_simulate_endpoint(request: Request) -> JSONResponse:
        body = await request.json()
        scenarios = body.get("scenarios", [])
        result = await api.simulate_policy(scenarios)
        return JSONResponse(result)

    @server.custom_route(f"{prefix}/policy/schema", methods=["GET"])
    async def policy_schema_endpoint(request: Request) -> JSONResponse:
        return JSONResponse(api.get_policy_schema())

    # Policy Versioning
    @server.custom_route(f"{prefix}/policy/versions", methods=["GET"])
    async def policy_versions_endpoint(request: Request) -> JSONResponse:
        return JSONResponse(api.get_policy_versions())

    @server.custom_route(f"{prefix}/policy/versions/rollback", methods=["POST"])
    async def policy_rollback_endpoint(request: Request) -> JSONResponse:
        body = await request.json()
        version_number = body.get("version_number", 0)
        reason = body.get("reason", "")
        return JSONResponse(await api.rollback_policy_version(version_number, reason))

    @server.custom_route(f"{prefix}/policy/versions/diff", methods=["GET"])
    async def policy_diff_endpoint(request: Request) -> JSONResponse:
        v1 = int(request.query_params.get("v1", "0"))
        v2 = int(request.query_params.get("v2", "0"))
        return JSONResponse(api.diff_policy_versions(v1, v2))

    # Validation
    @server.custom_route(f"{prefix}/policy/validate", methods=["POST"])
    async def policy_validate_endpoint(request: Request) -> JSONResponse:
        body = await request.json()
        config = body.get("config", {})
        return JSONResponse(api.validate_policy(config))

    @server.custom_route(f"{prefix}/policy/validate/providers", methods=["GET"])
    async def policy_validate_providers_endpoint(request: Request) -> JSONResponse:
        return JSONResponse(api.validate_providers())

    # Monitoring
    @server.custom_route(f"{prefix}/policy/metrics", methods=["GET"])
    async def policy_metrics_endpoint(request: Request) -> JSONResponse:
        return JSONResponse(api.get_policy_metrics())

    @server.custom_route(f"{prefix}/policy/alerts", methods=["GET"])
    async def policy_alerts_endpoint(request: Request) -> JSONResponse:
        limit = int(request.query_params.get("limit", "50"))
        return JSONResponse(api.get_policy_alerts(limit=limit))

    # Governance
    @server.custom_route(f"{prefix}/policy/governance", methods=["GET"])
    async def policy_governance_endpoint(request: Request) -> JSONResponse:
        return JSONResponse(api.get_governance_proposals())

    @server.custom_route(f"{prefix}/policy/governance/{{proposal_id}}", methods=["GET"])
    async def policy_governance_detail_endpoint(request: Request) -> JSONResponse:
        pid = request.path_params.get("proposal_id", "")
        return JSONResponse(api.get_governance_proposal(pid))

    @server.custom_route(f"{prefix}/policy/governance/proposals", methods=["POST"])
    async def policy_governance_create_endpoint(request: Request) -> JSONResponse:
        body = await request.json()
        payload = await api.create_governance_proposal(
            action=str(body.get("action", "")),
            config=body.get("config") if isinstance(body.get("config"), dict) else None,
            target_index=(
                int(body["target_index"])
                if body.get("target_index") is not None
                else None
            ),
            description=str(body.get("description", "")),
            author=str(body.get("author", "api")),
        )
        status_code = (
            payload["status"] if isinstance(payload.get("status"), int) else 200
        )
        return JSONResponse(payload, status_code=status_code)

    @server.custom_route(
        f"{prefix}/policy/governance/{{proposal_id}}/approve",
        methods=["POST"],
    )
    async def policy_governance_approve_endpoint(request: Request) -> JSONResponse:
        pid = request.path_params.get("proposal_id", "")
        try:
            body = await request.json()
        except Exception:
            body = {}
        payload = api.approve_governance_proposal(
            pid,
            approver=str(body.get("approver", "api")),
            note=str(body.get("note", body.get("reason", ""))),
        )
        status_code = (
            payload["status"] if isinstance(payload.get("status"), int) else 200
        )
        return JSONResponse(payload, status_code=status_code)

    @server.custom_route(
        f"{prefix}/policy/governance/{{proposal_id}}/assign",
        methods=["POST"],
    )
    async def policy_governance_assign_endpoint(request: Request) -> JSONResponse:
        pid = request.path_params.get("proposal_id", "")
        try:
            body = await request.json()
        except Exception:
            body = {}
        payload = api.assign_governance_proposal(
            pid,
            reviewer=str(body.get("reviewer", "")),
            actor=str(body.get("actor", "api")),
            note=str(body.get("note", "")),
        )
        status_code = (
            payload["status"] if isinstance(payload.get("status"), int) else 200
        )
        return JSONResponse(payload, status_code=status_code)

    @server.custom_route(
        f"{prefix}/policy/governance/{{proposal_id}}/simulate",
        methods=["POST"],
    )
    async def policy_governance_simulate_endpoint(request: Request) -> JSONResponse:
        pid = request.path_params.get("proposal_id", "")
        try:
            body = await request.json()
        except Exception:
            body = {}
        scenarios = body.get("scenarios")
        payload = await api.simulate_governance_proposal(
            pid,
            scenarios_data=scenarios if isinstance(scenarios, list) else [],
        )
        status_code = (
            payload["status"] if isinstance(payload.get("status"), int) else 200
        )
        return JSONResponse(payload, status_code=status_code)

    @server.custom_route(
        f"{prefix}/policy/governance/{{proposal_id}}/deploy",
        methods=["POST"],
    )
    async def policy_governance_deploy_endpoint(request: Request) -> JSONResponse:
        pid = request.path_params.get("proposal_id", "")
        try:
            body = await request.json()
        except Exception:
            body = {}
        payload = await api.deploy_governance_proposal(
            pid,
            actor=str(body.get("actor", "api")),
            note=str(body.get("note", body.get("reason", ""))),
        )
        status_code = (
            payload["status"] if isinstance(payload.get("status"), int) else 200
        )
        return JSONResponse(payload, status_code=status_code)

    @server.custom_route(
        f"{prefix}/policy/governance/{{proposal_id}}/reject",
        methods=["POST"],
    )
    async def policy_governance_reject_endpoint(request: Request) -> JSONResponse:
        pid = request.path_params.get("proposal_id", "")
        try:
            body = await request.json()
        except Exception:
            body = {}
        payload = api.reject_governance_proposal(
            pid,
            reason=str(body.get("reason", "")),
            actor=str(body.get("actor", "api")),
        )
        status_code = (
            payload["status"] if isinstance(payload.get("status"), int) else 200
        )
        return JSONResponse(payload, status_code=status_code)

    @server.custom_route(
        f"{prefix}/policy/governance/{{proposal_id}}/withdraw",
        methods=["POST"],
    )
    async def policy_governance_withdraw_endpoint(request: Request) -> JSONResponse:
        pid = request.path_params.get("proposal_id", "")
        try:
            body = await request.json()
        except Exception:
            body = {}
        payload = api.withdraw_governance_proposal(
            pid,
            actor=str(body.get("actor", "api")),
            note=str(body.get("note", body.get("reason", ""))),
        )
        status_code = (
            payload["status"] if isinstance(payload.get("status"), int) else 200
        )
        return JSONResponse(payload, status_code=status_code)

    # Health
    @server.custom_route(f"{prefix}/health", methods=["GET"])
    async def health_endpoint(request: Request) -> JSONResponse:
        return JSONResponse(api.get_health())

    logger.info("SecureMCP HTTP routes mounted at %s/*", prefix)
    return api


def _build_api_from_server(server: FastMCP) -> SecurityAPI:
    """Auto-construct a SecurityAPI from a FastMCP server's security context."""
    ctx = get_security_context(server)
    if ctx is None:
        logger.warning("No security context found on server; API will be empty")
        return SecurityAPI()

    # The SecurityContext has real fields for all components.
    # Use ctx.dashboard (auto-created by bootstrap) or build one.
    dashboard = getattr(ctx, "dashboard", None)

    return SecurityAPI(
        dashboard=dashboard,
        marketplace=getattr(ctx, "tool_marketplace", None),
        registry=getattr(ctx, "registry", None),
        compliance_reporter=getattr(ctx, "compliance_reporter", None),
        provenance_ledger=getattr(ctx, "provenance_ledger", None),
        federation=getattr(ctx, "federation", None),
        crl=getattr(ctx, "crl", None),
        event_bus=getattr(ctx, "event_bus", None),
        policy_engine=getattr(ctx, "policy_engine", None),
        policy_audit_log=getattr(ctx, "policy_audit_log", None),
        policy_version_manager=getattr(ctx, "policy_version_manager", None),
        policy_validator=getattr(ctx, "policy_validator", None),
        policy_monitor=getattr(ctx, "policy_monitor", None),
        policy_governor=getattr(ctx, "policy_governor", None),
    )
