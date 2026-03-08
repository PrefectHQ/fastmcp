"""Certification pipeline for SecureMCP tools.

Orchestrates the full certification flow: manifest validation,
score computation, attestation creation, and cryptographic signing.
Integrates with the existing ContractCryptoHandler for signatures
and the Marketplace for trust level updates.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from fastmcp.server.security.certification.attestation import (
    AttestationStatus,
    CertificationLevel,
    ToolAttestation,
    ValidationReport,
)
from fastmcp.server.security.certification.manifest import SecurityManifest
from fastmcp.server.security.certification.validator import ManifestValidator
from fastmcp.server.security.contracts.crypto import (
    ContractCryptoHandler,
    compute_digest,
)

if TYPE_CHECKING:
    from fastmcp.server.security.alerts.bus import SecurityEventBus
    from fastmcp.server.security.gateway.marketplace import Marketplace

logger = logging.getLogger(__name__)


# ── Mapping certification level → marketplace trust level ────────────

_CERT_TO_TRUST: dict[str, str] = {
    CertificationLevel.STRICT.value: "auditor_verified",
    CertificationLevel.STANDARD.value: "community_verified",
    CertificationLevel.BASIC.value: "self_certified",
    CertificationLevel.SELF_ATTESTED.value: "self_certified",
    CertificationLevel.UNCERTIFIED.value: "unverified",
}


class CertificationPipeline:
    """End-to-end certification pipeline for MCP tools.

    Takes a SecurityManifest through validation → scoring → attestation
    → signing. Optionally updates the marketplace trust level for the
    tool's server.

    Example::

        from fastmcp.server.security.contracts.crypto import (
            ContractCryptoHandler,
            SigningAlgorithm,
        )

        crypto = ContractCryptoHandler(
            algorithm=SigningAlgorithm.HMAC_SHA256,
            secret_key=b"certification-authority-key",
        )

        pipeline = CertificationPipeline(
            issuer_id="securemcp-ca",
            crypto_handler=crypto,
        )

        result = pipeline.certify(manifest)
        print(f"Level: {result.attestation.certification_level}")
        print(f"Valid: {result.attestation.is_valid()}")

    Args:
        issuer_id: Identity of the certification authority.
        crypto_handler: Handler for signing attestations.
        validator: ManifestValidator instance. Uses defaults if None.
        attestation_duration: How long attestations remain valid.
        marketplace: Optional marketplace to update trust levels in.
        event_bus: Optional event bus for certification alerts.
        min_level_for_signing: Minimum certification level to produce
            a signed attestation. Below this, attestation is PENDING.
    """

    def __init__(
        self,
        *,
        issuer_id: str = "securemcp-ca",
        crypto_handler: ContractCryptoHandler | None = None,
        validator: ManifestValidator | None = None,
        attestation_duration: timedelta = timedelta(days=90),
        marketplace: Marketplace | None = None,
        event_bus: SecurityEventBus | None = None,
        min_level_for_signing: CertificationLevel = CertificationLevel.BASIC,
    ) -> None:
        self._issuer_id = issuer_id
        self._crypto = crypto_handler
        self._validator = validator or ManifestValidator()
        self._attestation_duration = attestation_duration
        self._marketplace = marketplace
        self._event_bus = event_bus
        self._min_level_for_signing = min_level_for_signing

        # Internal registries
        self._attestations: dict[str, ToolAttestation] = {}
        self._reports: dict[str, ValidationReport] = {}

    def certify(
        self,
        manifest: SecurityManifest,
        *,
        requested_level: CertificationLevel | None = None,
    ) -> CertificationResult:
        """Run the full certification pipeline for a manifest.

        1. Validate the manifest
        2. Compute the manifest digest
        3. Determine certification level
        4. Create and optionally sign the attestation
        5. Optionally update marketplace trust level

        Args:
            manifest: The security manifest to certify.
            requested_level: If set, certify at this level (must be ≤ max).

        Returns:
            CertificationResult with the report and attestation.
        """
        # Step 1: Validate
        report = self._validator.validate(manifest)
        self._reports[report.report_id] = report

        # Step 2: Digest
        manifest_digest = compute_digest(manifest.to_dict())

        # Step 3: Determine level
        effective_level = report.max_certification_level
        if requested_level is not None:
            level_order = list(CertificationLevel)
            if level_order.index(requested_level) > level_order.index(effective_level):
                # Requested level exceeds what the tool qualifies for
                effective_level = report.max_certification_level
            else:
                effective_level = requested_level

        # Step 4: Create attestation
        attestation = ToolAttestation(
            manifest_id=manifest.manifest_id,
            tool_name=manifest.tool_name,
            tool_version=manifest.version,
            author=manifest.author,
            certification_level=effective_level,
            validation_report_id=report.report_id,
            validation_score=report.score,
            issuer_id=self._issuer_id,
            manifest_digest=manifest_digest,
        )
        attestation.set_default_expiry(self._attestation_duration)

        # Step 5: Sign if level meets threshold
        level_order = list(CertificationLevel)
        meets_min = level_order.index(effective_level) >= level_order.index(
            self._min_level_for_signing
        )

        if meets_min and self._crypto is not None:
            payload = attestation.signable_payload()
            sig_info = self._crypto.sign(payload, signer_id=self._issuer_id)
            attestation.signature = sig_info.signature
            attestation.status = AttestationStatus.VALID
        elif meets_min and self._crypto is None:
            # No crypto handler, but level meets minimum — mark valid unsigned
            attestation.status = AttestationStatus.VALID
        else:
            attestation.status = AttestationStatus.PENDING

        self._attestations[attestation.attestation_id] = attestation

        # Step 6: Update marketplace
        if self._marketplace is not None and attestation.status == AttestationStatus.VALID:
            self._update_marketplace_trust(manifest, effective_level)

        # Step 7: Emit event
        if self._event_bus is not None:
            self._emit_certification_event(attestation, report)

        return CertificationResult(
            report=report,
            attestation=attestation,
            manifest_digest=manifest_digest,
        )

    def verify_attestation(
        self,
        attestation: ToolAttestation,
        manifest: SecurityManifest | None = None,
    ) -> AttestationVerification:
        """Verify an attestation's validity and optionally its manifest binding.

        Checks:
        1. Attestation status and expiry
        2. Signature (if crypto handler available)
        3. Manifest digest match (if manifest provided)

        Args:
            attestation: The attestation to verify.
            manifest: Optional manifest to verify digest binding.

        Returns:
            AttestationVerification result.
        """
        issues: list[str] = []

        # Check status
        if attestation.status == AttestationStatus.REVOKED:
            issues.append("Attestation has been revoked")
        elif attestation.status == AttestationStatus.SUPERSEDED:
            issues.append("Attestation has been superseded by a newer version")
        elif attestation.status == AttestationStatus.EXPIRED:
            issues.append("Attestation has expired")
        elif attestation.status == AttestationStatus.PENDING:
            issues.append("Attestation is still pending (not yet signed)")

        # Check expiry
        if not attestation.is_valid() and attestation.status == AttestationStatus.VALID:
            issues.append("Attestation has expired")

        # Verify signature
        signature_valid = False
        if attestation.signature and self._crypto is not None:
            from fastmcp.server.security.contracts.crypto import SignatureInfo, SigningAlgorithm

            sig_info = SignatureInfo(
                algorithm=self._crypto.algorithm,
                signer_id=attestation.issuer_id,
                signature=attestation.signature,
            )
            payload = attestation.signable_payload()
            signature_valid = self._crypto.verify(payload, sig_info)
            if not signature_valid:
                issues.append("Signature verification failed")
        elif attestation.signature and self._crypto is None:
            issues.append("Cannot verify signature: no crypto handler configured")
        elif not attestation.signature:
            issues.append("Attestation is unsigned")

        # Verify manifest binding
        manifest_match = True
        if manifest is not None:
            expected_digest = compute_digest(manifest.to_dict())
            if expected_digest != attestation.manifest_digest:
                manifest_match = False
                issues.append(
                    "Manifest digest mismatch: attestation was issued for a different manifest version"
                )

        valid = len(issues) == 0 and attestation.is_valid()

        return AttestationVerification(
            valid=valid,
            signature_valid=signature_valid,
            manifest_match=manifest_match,
            issues=issues,
        )

    def revoke(self, attestation_id: str, *, reason: str = "") -> bool:
        """Revoke an attestation.

        Args:
            attestation_id: ID of the attestation to revoke.
            reason: Why the attestation is being revoked.

        Returns:
            True if found and revoked.
        """
        attestation = self._attestations.get(attestation_id)
        if attestation is None:
            return False

        attestation.status = AttestationStatus.REVOKED
        attestation.metadata["revocation_reason"] = reason

        if self._event_bus is not None:
            from fastmcp.server.security.alerts.models import (
                AlertSeverity,
                SecurityEvent,
                SecurityEventType,
            )

            self._event_bus.emit(
                SecurityEvent(
                    event_type=SecurityEventType.TRUST_CHANGED,
                    severity=AlertSeverity.WARNING,
                    layer="certification",
                    message=f"Attestation revoked for {attestation.tool_name}: {reason}",
                    resource_id=attestation_id,
                    data={
                        "tool_name": attestation.tool_name,
                        "reason": reason,
                    },
                )
            )

        return True

    def get_attestation(self, attestation_id: str) -> ToolAttestation | None:
        """Look up an attestation by ID."""
        return self._attestations.get(attestation_id)

    def get_report(self, report_id: str) -> ValidationReport | None:
        """Look up a validation report by ID."""
        return self._reports.get(report_id)

    def find_attestations(
        self,
        *,
        tool_name: str | None = None,
        author: str | None = None,
        min_level: CertificationLevel | None = None,
        valid_only: bool = False,
    ) -> list[ToolAttestation]:
        """Search attestations by criteria.

        Args:
            tool_name: Filter by tool name.
            author: Filter by author.
            min_level: Minimum certification level.
            valid_only: Only return currently valid attestations.

        Returns:
            Matching attestations.
        """
        level_order = list(CertificationLevel)
        results: list[ToolAttestation] = []

        for att in self._attestations.values():
            if tool_name is not None and att.tool_name != tool_name:
                continue
            if author is not None and att.author != author:
                continue
            if min_level is not None:
                if level_order.index(att.certification_level) < level_order.index(min_level):
                    continue
            if valid_only and not att.is_valid():
                continue
            results.append(att)

        return results

    def _update_marketplace_trust(
        self, manifest: SecurityManifest, level: CertificationLevel
    ) -> None:
        """Update marketplace trust level based on certification."""
        if self._marketplace is None:
            return

        from fastmcp.server.security.gateway.models import TrustLevel

        trust_value = _CERT_TO_TRUST.get(level.value, "unverified")
        trust_level = TrustLevel(trust_value)

        # Find server by tool name in marketplace
        for server in self._marketplace.get_all_servers():
            if manifest.tool_name in server.tags or server.name == manifest.tool_name:
                self._marketplace.update_trust_level(server.server_id, trust_level)
                break

    def _emit_certification_event(
        self, attestation: ToolAttestation, report: ValidationReport
    ) -> None:
        """Emit a security event for certification completion."""
        if self._event_bus is None:
            return

        from fastmcp.server.security.alerts.models import (
            AlertSeverity,
            SecurityEvent,
            SecurityEventType,
        )

        severity = AlertSeverity.INFO
        if attestation.status == AttestationStatus.PENDING:
            severity = AlertSeverity.WARNING

        self._event_bus.emit(
            SecurityEvent(
                event_type=SecurityEventType.TRUST_CHANGED,
                severity=severity,
                layer="certification",
                message=(
                    f"Tool '{attestation.tool_name}' certified at "
                    f"{attestation.certification_level.value} "
                    f"(score: {report.score:.2f})"
                ),
                resource_id=attestation.attestation_id,
                data={
                    "tool_name": attestation.tool_name,
                    "certification_level": attestation.certification_level.value,
                    "score": report.score,
                    "findings_count": len(report.findings),
                    "error_count": report.error_count,
                    "warning_count": report.warning_count,
                },
            )
        )


# ── Result types ─────────────────────────────────────────────────────


class CertificationResult:
    """Result from running the certification pipeline.

    Attributes:
        report: The validation report.
        attestation: The created attestation.
        manifest_digest: SHA-256 digest of the manifest.
    """

    def __init__(
        self,
        *,
        report: ValidationReport,
        attestation: ToolAttestation,
        manifest_digest: str,
    ) -> None:
        self.report = report
        self.attestation = attestation
        self.manifest_digest = manifest_digest

    @property
    def is_certified(self) -> bool:
        """Whether the tool achieved certification."""
        return self.attestation.status == AttestationStatus.VALID

    @property
    def certification_level(self) -> CertificationLevel:
        """The certification level achieved."""
        return self.attestation.certification_level

    @property
    def score(self) -> float:
        """Validation score."""
        return self.report.score


class AttestationVerification:
    """Result from verifying an attestation.

    Attributes:
        valid: Overall validity.
        signature_valid: Whether the signature checks out.
        manifest_match: Whether the manifest digest matches.
        issues: List of issues found.
    """

    def __init__(
        self,
        *,
        valid: bool,
        signature_valid: bool,
        manifest_match: bool,
        issues: list[str],
    ) -> None:
        self.valid = valid
        self.signature_valid = signature_valid
        self.manifest_match = manifest_match
        self.issues = issues
