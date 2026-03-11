"""High-level SDK client for SecureMCP.

Provides a unified facade over all SecureMCP components.
Tool developers use this to check permissions, record provenance,
verify trust, and run compliance checks — all through one interface.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastmcp.server.security.alerts.bus import SecurityEventBus
from fastmcp.server.security.alerts.models import (
    AlertSeverity,
    SecurityEvent,
    SecurityEventType,
)
from fastmcp.server.security.certification.manifest import (
    SecurityManifest,
)
from fastmcp.server.security.certification.pipeline import CertificationPipeline
from fastmcp.server.security.compliance.reports import (
    ComplianceReport,
    ComplianceReporter,
    ReportType,
)
from fastmcp.server.security.federation.crl import CertificateRevocationList
from fastmcp.server.security.federation.federation import TrustFederation
from fastmcp.server.security.gateway.tool_marketplace import ToolMarketplace
from fastmcp.server.security.policy.engine import PolicyEngine
from fastmcp.server.security.provenance.ledger import ProvenanceLedger
from fastmcp.server.security.provenance.records import ProvenanceAction
from fastmcp.server.security.registry.registry import TrustRegistry
from fastmcp.server.security.sandbox.enforcer import (
    ExecutionContext,
    SandboxedRunner,
)


@dataclass
class SecurityCheckResult:
    """Result of a comprehensive security check."""

    check_id: str = ""
    tool_name: str = ""
    allowed: bool = True
    trust_score: float = 0.0
    is_certified: bool = False
    is_revoked: bool = False
    policy_allowed: bool = True
    sandbox_context: ExecutionContext | None = None
    reasons: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        if not self.check_id:
            self.check_id = f"chk-{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> dict:
        return {
            "check_id": self.check_id,
            "tool_name": self.tool_name,
            "allowed": self.allowed,
            "trust_score": self.trust_score,
            "is_certified": self.is_certified,
            "is_revoked": self.is_revoked,
            "policy_allowed": self.policy_allowed,
            "reasons": self.reasons,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ToolSecurityProfile:
    """Aggregated security profile for a tool."""

    tool_name: str = ""
    trust_score: float = 0.0
    is_certified: bool = False
    certification_level: str = ""
    is_revoked: bool = False
    is_published: bool = False
    install_count: int = 0
    average_rating: float = 0.0
    violation_count: int = 0
    provenance_records: int = 0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "trust_score": self.trust_score,
            "is_certified": self.is_certified,
            "certification_level": self.certification_level,
            "is_revoked": self.is_revoked,
            "is_published": self.is_published,
            "install_count": self.install_count,
            "average_rating": self.average_rating,
            "violation_count": self.violation_count,
            "provenance_records": self.provenance_records,
        }


class SecureMCPClient:
    """Unified SDK client for SecureMCP security operations.

    Provides a single entry point for tool developers to:
    - Check if a tool is allowed to execute
    - Record provenance for tool operations
    - Query trust scores and certification status
    - Run compliance reports
    - Get aggregated security profiles

    Components are optional — only those provided are used.
    """

    def __init__(
        self,
        *,
        registry: TrustRegistry | None = None,
        policy_engine: PolicyEngine | None = None,
        marketplace: ToolMarketplace | None = None,
        pipeline: CertificationPipeline | None = None,
        provenance: ProvenanceLedger | None = None,
        federation: TrustFederation | None = None,
        sandbox_runner: SandboxedRunner | None = None,
        compliance_reporter: ComplianceReporter | None = None,
        event_bus: SecurityEventBus | None = None,
        crl: CertificateRevocationList | None = None,
    ):
        self._registry = registry
        self._policy_engine = policy_engine
        self._marketplace = marketplace
        self._pipeline = pipeline
        self._provenance = provenance
        self._federation = federation
        self._sandbox_runner = sandbox_runner
        self._compliance_reporter = compliance_reporter
        self._event_bus = event_bus
        self._crl = crl
        self._check_history: list[SecurityCheckResult] = []

    # ── Component accessors ──────────────────────────────────────

    @property
    def registry(self) -> TrustRegistry | None:
        return self._registry

    @property
    def marketplace(self) -> ToolMarketplace | None:
        return self._marketplace

    @property
    def federation(self) -> TrustFederation | None:
        return self._federation

    @property
    def check_count(self) -> int:
        return len(self._check_history)

    # ── Security checks ──────────────────────────────────────────

    def check_tool(
        self,
        tool_name: str,
        *,
        actor_id: str = "",
        manifest: SecurityManifest | None = None,
        min_trust_score: float = 0.0,
    ) -> SecurityCheckResult:
        """Run a comprehensive security check on a tool.

        Checks trust score, revocation status, certification,
        and optionally creates a sandbox context.

        Args:
            tool_name: Name of the tool to check.
            actor_id: ID of the actor requesting execution.
            manifest: Optional manifest for sandbox context creation.
            min_trust_score: Minimum trust score required.

        Returns:
            SecurityCheckResult with aggregated findings.
        """
        result = SecurityCheckResult(tool_name=tool_name)
        reasons = []

        # Check CRL
        if self._crl and self._crl.is_revoked(tool_name):
            result.is_revoked = True
            result.allowed = False
            reasons.append(f"Tool '{tool_name}' is revoked")

        # Check federation CRL
        if self._federation and self._federation.local_crl.is_revoked(tool_name):
            result.is_revoked = True
            result.allowed = False
            reasons.append(f"Tool '{tool_name}' is revoked (federation)")

        # Check trust score
        if self._registry:
            record = self._registry.get(tool_name)
            if record:
                result.trust_score = record.trust_score.overall
                result.is_certified = record.is_certified
                if record.trust_score.overall < min_trust_score:
                    result.allowed = False
                    reasons.append(
                        f"Trust score {record.trust_score.overall:.2f} < {min_trust_score:.2f}"
                    )

        # Check marketplace for certification
        if self._marketplace:
            listing = self._marketplace.get_by_name(tool_name)
            if listing and listing.is_certified:
                result.is_certified = True

        # Create sandbox context if manifest provided
        if self._sandbox_runner and manifest:
            ctx = self._sandbox_runner.start(manifest, actor_id=actor_id)
            result.sandbox_context = ctx
            if ctx.blocked:
                result.allowed = False
                reasons.append(f"Sandbox blocked: {ctx.block_reason}")

        result.reasons = reasons
        self._check_history.append(result)

        if self._event_bus:
            self._event_bus.emit(
                SecurityEvent(
                    event_type=SecurityEventType.TRUST_CHANGED,
                    severity=AlertSeverity.INFO,
                    layer="sdk",
                    message=f"Security check for '{tool_name}': {'allowed' if result.allowed else 'denied'}",
                    resource_id=tool_name,
                    data=result.to_dict(),
                )
            )

        return result

    # ── Provenance recording ─────────────────────────────────────

    def record_action(
        self,
        tool_name: str,
        action: str,
        *,
        actor_id: str = "",
        input_hash: str = "",
        output_hash: str = "",
        metadata: dict | None = None,
    ) -> str | None:
        """Record a provenance action for a tool execution.

        Returns the record ID if provenance is available, else None.
        """
        if not self._provenance:
            return None

        record = self._provenance.record(
            ProvenanceAction(
                tool_name=tool_name,
                action=action,
                actor_id=actor_id,
                input_hash=input_hash,
                output_hash=output_hash,
                metadata=metadata or {},
            )
        )
        return record.record_id

    # ── Trust queries ────────────────────────────────────────────

    def get_trust_score(self, tool_name: str) -> float:
        """Get the current trust score for a tool."""
        if self._registry:
            record = self._registry.get(tool_name)
            if record:
                return record.trust_score.overall
        return 0.0

    def is_tool_revoked(self, tool_name: str) -> bool:
        """Check if a tool is revoked in any CRL."""
        if self._crl and self._crl.is_revoked(tool_name):
            return True
        if self._federation and self._federation.local_crl.is_revoked(tool_name):
            return True
        return False

    def is_tool_certified(self, tool_name: str) -> bool:
        """Check if a tool has valid certification."""
        if self._registry:
            record = self._registry.get(tool_name)
            if record and record.is_certified:
                return True
        if self._marketplace:
            listing = self._marketplace.get_by_name(tool_name)
            if listing and listing.is_certified:
                return True
        return False

    # ── Security profiles ────────────────────────────────────────

    def get_tool_profile(self, tool_name: str) -> ToolSecurityProfile:
        """Get an aggregated security profile for a tool."""
        profile = ToolSecurityProfile(tool_name=tool_name)

        if self._registry:
            record = self._registry.get(tool_name)
            if record:
                profile.trust_score = record.trust_score.overall
                profile.is_certified = record.is_certified

        if self._marketplace:
            listing = self._marketplace.get_by_name(tool_name)
            if listing:
                profile.is_published = True
                profile.install_count = listing.install_count
                profile.average_rating = listing.average_rating
                if listing.is_certified:
                    profile.is_certified = True
                    profile.certification_level = listing.certification_level.value

        if self._crl:
            profile.is_revoked = self._crl.is_revoked(tool_name)
        elif self._federation:
            profile.is_revoked = self._federation.local_crl.is_revoked(tool_name)

        if self._provenance:
            records = self._provenance.query(tool_name=tool_name)
            profile.provenance_records = len(records)

        return profile

    # ── Compliance ───────────────────────────────────────────────

    def run_compliance_report(
        self,
        report_type: ReportType = ReportType.FULL,
    ) -> ComplianceReport | None:
        """Run a compliance report if reporter is available."""
        if not self._compliance_reporter:
            return None
        return self._compliance_reporter.generate_report(report_type=report_type)

    # ── History & stats ──────────────────────────────────────────

    def get_check_history(self) -> list[SecurityCheckResult]:
        """Get all security check results."""
        return list(self._check_history)

    def get_statistics(self) -> dict:
        """Get SDK usage statistics."""
        components = []
        if self._registry:
            components.append("registry")
        if self._policy_engine:
            components.append("policy_engine")
        if self._marketplace:
            components.append("marketplace")
        if self._pipeline:
            components.append("pipeline")
        if self._provenance:
            components.append("provenance")
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
            "check_count": self.check_count,
            "components_configured": len(components),
            "components": components,
        }
