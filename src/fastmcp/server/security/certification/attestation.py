"""Attestation and certification records for SecureMCP tools.

An attestation is the cryptographically signed proof that a tool's
manifest has been validated and the tool has achieved a specific
certification level.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any


class CertificationLevel(Enum):
    """Levels of certification a tool can achieve.

    Higher levels require stricter validation and are trusted more
    broadly in the marketplace.
    """

    UNCERTIFIED = "uncertified"
    SELF_ATTESTED = "self_attested"
    BASIC = "basic"
    STANDARD = "standard"
    STRICT = "strict"


class AttestationStatus(Enum):
    """Lifecycle status of an attestation."""

    PENDING = "pending"
    VALID = "valid"
    EXPIRED = "expired"
    REVOKED = "revoked"
    SUPERSEDED = "superseded"


class ValidationSeverity(Enum):
    """Severity of a validation finding."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(frozen=True)
class ValidationFinding:
    """A single finding from manifest validation.

    Findings describe issues, recommendations, or confirmations
    discovered during the certification process.

    Attributes:
        finding_id: Unique identifier.
        severity: How serious the finding is.
        category: What area this finding relates to (e.g., "permissions", "data_flow").
        message: Human-readable description of the finding.
        field_path: Dot-separated path to the manifest field (e.g., "data_flows[0].destination").
        suggestion: Recommended fix or improvement.
    """

    finding_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    severity: ValidationSeverity = ValidationSeverity.INFO
    category: str = ""
    message: str = ""
    field_path: str = ""
    suggestion: str = ""


@dataclass
class ValidationReport:
    """Complete report from manifest validation.

    Aggregates all findings and computes an overall score that
    determines the maximum certification level achievable.

    Attributes:
        report_id: Unique report identifier.
        manifest_id: The manifest that was validated.
        tool_name: Tool name from the manifest.
        findings: All validation findings.
        score: Normalized score from 0.0 (fail) to 1.0 (perfect).
        max_certification_level: Highest certification the tool qualifies for.
        validated_at: When validation was performed.
        metadata: Additional report data.
    """

    report_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    manifest_id: str = ""
    tool_name: str = ""
    findings: list[ValidationFinding] = field(default_factory=list)
    score: float = 0.0
    max_certification_level: CertificationLevel = CertificationLevel.UNCERTIFIED
    validated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        """Whether the report contains ERROR or CRITICAL findings."""
        return any(
            f.severity in (ValidationSeverity.ERROR, ValidationSeverity.CRITICAL)
            for f in self.findings
        )

    @property
    def has_critical(self) -> bool:
        """Whether the report contains CRITICAL findings."""
        return any(f.severity == ValidationSeverity.CRITICAL for f in self.findings)

    @property
    def error_count(self) -> int:
        """Number of ERROR-level findings."""
        return sum(1 for f in self.findings if f.severity == ValidationSeverity.ERROR)

    @property
    def warning_count(self) -> int:
        """Number of WARNING-level findings."""
        return sum(1 for f in self.findings if f.severity == ValidationSeverity.WARNING)

    def findings_by_severity(
        self, severity: ValidationSeverity
    ) -> list[ValidationFinding]:
        """Get findings filtered by severity."""
        return [f for f in self.findings if f.severity == severity]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "report_id": self.report_id,
            "manifest_id": self.manifest_id,
            "tool_name": self.tool_name,
            "findings": [
                {
                    "finding_id": f.finding_id,
                    "severity": f.severity.value,
                    "category": f.category,
                    "message": f.message,
                    "field_path": f.field_path,
                    "suggestion": f.suggestion,
                }
                for f in self.findings
            ],
            "score": self.score,
            "max_certification_level": self.max_certification_level.value,
            "validated_at": self.validated_at.isoformat(),
            "metadata": self.metadata,
        }


@dataclass
class ToolAttestation:
    """Cryptographically signed certification record for a tool.

    Once a tool passes validation, an attestation is created and signed.
    This attestation can be presented to any SecureMCP server as proof
    of certification, verified independently via the signature.

    Attributes:
        attestation_id: Unique identifier.
        manifest_id: The manifest this certifies.
        tool_name: Tool name from the manifest.
        tool_version: Tool version from the manifest.
        author: Tool author from the manifest.
        certification_level: The level achieved.
        status: Current lifecycle status.
        validation_report_id: ID of the validation report.
        validation_score: Score from validation.
        issued_at: When the attestation was created.
        expires_at: When it expires.
        issuer_id: Who issued the attestation.
        signature: Cryptographic signature over the attestation payload.
        manifest_digest: SHA-256 digest of the manifest, binding attestation to exact manifest.
        metadata: Additional attestation data.
    """

    attestation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    manifest_id: str = ""
    tool_name: str = ""
    tool_version: str = ""
    author: str = ""
    certification_level: CertificationLevel = CertificationLevel.UNCERTIFIED
    status: AttestationStatus = AttestationStatus.PENDING
    validation_report_id: str = ""
    validation_score: float = 0.0
    issued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    issuer_id: str = ""
    signature: str = ""
    manifest_digest: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_valid(self) -> bool:
        """Check if this attestation is currently valid."""
        if self.status != AttestationStatus.VALID:
            return False
        if self.expires_at is not None:
            return datetime.now(timezone.utc) < self.expires_at
        return True

    def set_default_expiry(self, duration: timedelta = timedelta(days=90)) -> None:
        """Set expiry relative to issue time."""
        self.expires_at = self.issued_at + duration

    def signable_payload(self) -> dict[str, Any]:
        """Get the payload that should be signed.

        Returns a deterministic dictionary of the fields that
        constitute the attestation's identity and claims.
        """
        return {
            "attestation_id": self.attestation_id,
            "manifest_id": self.manifest_id,
            "manifest_digest": self.manifest_digest,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "author": self.author,
            "certification_level": self.certification_level.value,
            "validation_report_id": self.validation_report_id,
            "validation_score": self.validation_score,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "issuer_id": self.issuer_id,
        }

    def to_dict(self) -> dict[str, Any]:
        """Full serialization including signature."""
        d = self.signable_payload()
        d["status"] = self.status.value
        d["signature"] = self.signature
        d["metadata"] = self.metadata
        return d
