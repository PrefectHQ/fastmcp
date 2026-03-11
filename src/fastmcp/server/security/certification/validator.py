"""Manifest validation and static analysis for SecureMCP tools.

The ManifestValidator performs structural, semantic, and security
checks on a SecurityManifest to produce a ValidationReport. It
supports pluggable validation rules via the ValidationRule protocol.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from fastmcp.server.security.certification.attestation import (
    CertificationLevel,
    ValidationFinding,
    ValidationReport,
    ValidationSeverity,
)
from fastmcp.server.security.certification.manifest import (
    DataClassification,
    PermissionScope,
    SecurityManifest,
)

logger = logging.getLogger(__name__)


# ── Pluggable validation rules ──────────────────────────────────────


@runtime_checkable
class ValidationRule(Protocol):
    """Protocol for pluggable validation rules.

    Implement this to add custom validation logic beyond the
    built-in checks.
    """

    @property
    def rule_id(self) -> str:
        """Unique identifier for this rule."""
        ...

    def validate(self, manifest: SecurityManifest) -> list[ValidationFinding]:
        """Run this rule against a manifest.

        Returns a list of findings (may be empty if no issues).
        """
        ...


# ── Built-in validation rules ───────────────────────────────────────


@dataclass(frozen=True)
class RequiredFieldsRule:
    """Validates that all required manifest fields are populated."""

    rule_id: str = "required_fields"

    def validate(self, manifest: SecurityManifest) -> list[ValidationFinding]:
        findings: list[ValidationFinding] = []

        if not manifest.tool_name:
            findings.append(
                ValidationFinding(
                    severity=ValidationSeverity.CRITICAL,
                    category="structure",
                    message="tool_name is required",
                    field_path="tool_name",
                    suggestion="Provide a non-empty tool name.",
                )
            )

        if not manifest.version or manifest.version == "0.0.0":
            findings.append(
                ValidationFinding(
                    severity=ValidationSeverity.ERROR,
                    category="structure",
                    message="version must be set to a valid version string",
                    field_path="version",
                    suggestion="Use semantic versioning (e.g., '1.0.0').",
                )
            )

        if not manifest.author:
            findings.append(
                ValidationFinding(
                    severity=ValidationSeverity.ERROR,
                    category="structure",
                    message="author is required for certification",
                    field_path="author",
                    suggestion="Provide the tool author or organization name.",
                )
            )

        if not manifest.description:
            findings.append(
                ValidationFinding(
                    severity=ValidationSeverity.WARNING,
                    category="structure",
                    message="description is empty",
                    field_path="description",
                    suggestion="Provide a description of what the tool does.",
                )
            )

        return findings


@dataclass(frozen=True)
class PermissionConsistencyRule:
    """Validates that declared permissions are consistent with data flows and resource access."""

    rule_id: str = "permission_consistency"

    def validate(self, manifest: SecurityManifest) -> list[ValidationFinding]:
        findings: list[ValidationFinding] = []

        # If tool has resource access declarations, it should have READ or WRITE resource permission
        if manifest.resource_access:
            has_read = any(r.access_type == "read" for r in manifest.resource_access)
            has_write = any(r.access_type == "write" for r in manifest.resource_access)

            if has_read and PermissionScope.READ_RESOURCE not in manifest.permissions:
                findings.append(
                    ValidationFinding(
                        severity=ValidationSeverity.ERROR,
                        category="permissions",
                        message="Resource read access declared but READ_RESOURCE permission not requested",
                        field_path="permissions",
                        suggestion="Add PermissionScope.READ_RESOURCE to permissions.",
                    )
                )

            if has_write and PermissionScope.WRITE_RESOURCE not in manifest.permissions:
                findings.append(
                    ValidationFinding(
                        severity=ValidationSeverity.ERROR,
                        category="permissions",
                        message="Resource write access declared but WRITE_RESOURCE permission not requested",
                        field_path="permissions",
                        suggestion="Add PermissionScope.WRITE_RESOURCE to permissions.",
                    )
                )

        # If tool has network destinations in data flows, it should have NETWORK_ACCESS
        for i, flow in enumerate(manifest.data_flows):
            if _is_network_destination(flow.destination):
                if PermissionScope.NETWORK_ACCESS not in manifest.permissions:
                    findings.append(
                        ValidationFinding(
                            severity=ValidationSeverity.ERROR,
                            category="permissions",
                            message=(
                                f"Data flow to network destination '{flow.destination}' "
                                f"but NETWORK_ACCESS permission not requested"
                            ),
                            field_path=f"data_flows[{i}].destination",
                            suggestion="Add PermissionScope.NETWORK_ACCESS to permissions.",
                        )
                    )
                break  # One finding is enough

        # If tool declares SENSITIVE_DATA permission, data flows should have classification >= CONFIDENTIAL
        if PermissionScope.SENSITIVE_DATA in manifest.permissions:
            high_classifications = {
                DataClassification.CONFIDENTIAL,
                DataClassification.RESTRICTED,
                DataClassification.PII,
                DataClassification.PHI,
                DataClassification.FINANCIAL,
            }
            has_sensitive_flow = any(
                f.classification in high_classifications for f in manifest.data_flows
            )
            if not has_sensitive_flow and manifest.data_flows:
                findings.append(
                    ValidationFinding(
                        severity=ValidationSeverity.WARNING,
                        category="permissions",
                        message="SENSITIVE_DATA permission declared but no data flows with sensitive classification",
                        field_path="permissions",
                        suggestion="Either remove SENSITIVE_DATA permission or update data flow classifications.",
                    )
                )

        return findings


@dataclass(frozen=True)
class DataFlowRule:
    """Validates data flow declarations for completeness and security."""

    rule_id: str = "data_flow"

    def validate(self, manifest: SecurityManifest) -> list[ValidationFinding]:
        findings: list[ValidationFinding] = []

        if not manifest.data_flows:
            findings.append(
                ValidationFinding(
                    severity=ValidationSeverity.WARNING,
                    category="data_flow",
                    message="No data flows declared",
                    field_path="data_flows",
                    suggestion="Declare all data flow paths for full certification.",
                )
            )
            return findings

        sensitive_classifications = {
            DataClassification.PII,
            DataClassification.PHI,
            DataClassification.FINANCIAL,
            DataClassification.RESTRICTED,
        }

        for i, flow in enumerate(manifest.data_flows):
            # Flows must have source and destination
            if not flow.source:
                findings.append(
                    ValidationFinding(
                        severity=ValidationSeverity.ERROR,
                        category="data_flow",
                        message=f"Data flow {flow.flow_id} has no source",
                        field_path=f"data_flows[{i}].source",
                        suggestion="Specify where data comes from.",
                    )
                )
            if not flow.destination:
                findings.append(
                    ValidationFinding(
                        severity=ValidationSeverity.ERROR,
                        category="data_flow",
                        message=f"Data flow {flow.flow_id} has no destination",
                        field_path=f"data_flows[{i}].destination",
                        suggestion="Specify where data goes.",
                    )
                )

            # Sensitive data flowing to network should have transforms
            if (
                flow.classification in sensitive_classifications
                and _is_network_destination(flow.destination)
                and not flow.transforms
            ):
                findings.append(
                    ValidationFinding(
                        severity=ValidationSeverity.ERROR,
                        category="data_flow",
                        message=(
                            f"Sensitive data ({flow.classification.value}) flows to "
                            f"network destination without transforms"
                        ),
                        field_path=f"data_flows[{i}].transforms",
                        suggestion="Apply transforms (e.g., 'encrypt', 'redact', 'hash') to sensitive network flows.",
                    )
                )

            # Sensitive data should have bounded retention
            if flow.classification in sensitive_classifications and flow.retention in (
                "none",
                "",
            ):
                findings.append(
                    ValidationFinding(
                        severity=ValidationSeverity.WARNING,
                        category="data_flow",
                        message=(
                            f"Sensitive data ({flow.classification.value}) has "
                            f"no retention policy specified"
                        ),
                        field_path=f"data_flows[{i}].retention",
                        suggestion="Specify retention period (e.g., 'session', '7d', '30d').",
                    )
                )

        return findings


@dataclass(frozen=True)
class ResourceAccessRule:
    """Validates resource access declarations."""

    rule_id: str = "resource_access"

    def validate(self, manifest: SecurityManifest) -> list[ValidationFinding]:
        findings: list[ValidationFinding] = []

        for i, access in enumerate(manifest.resource_access):
            if not access.resource_pattern:
                findings.append(
                    ValidationFinding(
                        severity=ValidationSeverity.ERROR,
                        category="resource_access",
                        message="Resource access declaration has no pattern",
                        field_path=f"resource_access[{i}].resource_pattern",
                        suggestion="Specify a resource URI pattern.",
                    )
                )

            if access.access_type not in ("read", "write"):
                findings.append(
                    ValidationFinding(
                        severity=ValidationSeverity.ERROR,
                        category="resource_access",
                        message=f"Invalid access type: '{access.access_type}'",
                        field_path=f"resource_access[{i}].access_type",
                        suggestion="Use 'read' or 'write'.",
                    )
                )

            # Wildcard patterns are a warning at higher cert levels
            if access.resource_pattern.endswith("**"):
                findings.append(
                    ValidationFinding(
                        severity=ValidationSeverity.WARNING,
                        category="resource_access",
                        message=f"Broad wildcard pattern '{access.resource_pattern}' may limit certification level",
                        field_path=f"resource_access[{i}].resource_pattern",
                        suggestion="Use more specific resource patterns for higher certification.",
                    )
                )

        return findings


@dataclass(frozen=True)
class SecurityBestPracticesRule:
    """Validates adherence to security best practices."""

    rule_id: str = "best_practices"

    def validate(self, manifest: SecurityManifest) -> list[ValidationFinding]:
        findings: list[ValidationFinding] = []

        # SUBPROCESS_EXEC is high-risk
        if PermissionScope.SUBPROCESS_EXEC in manifest.permissions:
            findings.append(
                ValidationFinding(
                    severity=ValidationSeverity.WARNING,
                    category="best_practices",
                    message="SUBPROCESS_EXEC permission is high-risk and limits certification level",
                    field_path="permissions",
                    suggestion="Avoid subprocess execution if possible; limits max certification to BASIC.",
                )
            )

        # CROSS_ORIGIN needs justification
        if PermissionScope.CROSS_ORIGIN in manifest.permissions:
            findings.append(
                ValidationFinding(
                    severity=ValidationSeverity.WARNING,
                    category="best_practices",
                    message="CROSS_ORIGIN permission requires careful review",
                    field_path="permissions",
                    suggestion="Document why cross-origin access is needed in metadata.",
                )
            )

        # Very long execution times
        if manifest.max_execution_time_seconds > 300:
            findings.append(
                ValidationFinding(
                    severity=ValidationSeverity.WARNING,
                    category="best_practices",
                    message=f"Execution time limit ({manifest.max_execution_time_seconds}s) is very high",
                    field_path="max_execution_time_seconds",
                    suggestion="Consider reducing max execution time or documenting the need.",
                )
            )

        # Tools requiring consent should flag it
        sensitive_classifications = {
            DataClassification.PII,
            DataClassification.PHI,
            DataClassification.FINANCIAL,
        }
        has_regulated_data = any(
            f.classification in sensitive_classifications for f in manifest.data_flows
        )
        if has_regulated_data and not manifest.requires_consent:
            findings.append(
                ValidationFinding(
                    severity=ValidationSeverity.ERROR,
                    category="best_practices",
                    message="Tool handles regulated data but does not require consent",
                    field_path="requires_consent",
                    suggestion="Set requires_consent=True for tools handling PII/PHI/financial data.",
                )
            )

        return findings


# ── Helpers ──────────────────────────────────────────────────────────


_NETWORK_PATTERN = re.compile(r"^https?://|^wss?://|^ftp://")


def _is_network_destination(destination: str) -> bool:
    """Check if a destination looks like a network address."""
    return bool(_NETWORK_PATTERN.match(destination))


# ── Default rules ────────────────────────────────────────────────────

DEFAULT_RULES: list[Any] = [
    RequiredFieldsRule(),
    PermissionConsistencyRule(),
    DataFlowRule(),
    ResourceAccessRule(),
    SecurityBestPracticesRule(),
]


# ── Scoring thresholds ───────────────────────────────────────────────

#: Score thresholds for certification levels.
#: A tool must reach at least the threshold score to qualify.
CERTIFICATION_THRESHOLDS: dict[CertificationLevel, float] = {
    CertificationLevel.STRICT: 0.95,
    CertificationLevel.STANDARD: 0.80,
    CertificationLevel.BASIC: 0.60,
    CertificationLevel.SELF_ATTESTED: 0.30,
    CertificationLevel.UNCERTIFIED: 0.0,
}

#: Permissions that cap the maximum certification level.
PERMISSION_CAPS: dict[PermissionScope, CertificationLevel] = {
    PermissionScope.SUBPROCESS_EXEC: CertificationLevel.BASIC,
    PermissionScope.FILE_SYSTEM_WRITE: CertificationLevel.STANDARD,
}


# ── ManifestValidator ────────────────────────────────────────────────


class ManifestValidator:
    """Validates a SecurityManifest and produces a ValidationReport.

    Runs a set of validation rules (built-in + custom) against the
    manifest, computes a score, and determines the maximum certification
    level the tool qualifies for.

    Example::

        validator = ManifestValidator()
        report = validator.validate(manifest)

        print(f"Score: {report.score}")
        print(f"Max level: {report.max_certification_level}")
        for finding in report.findings:
            print(f"  [{finding.severity.value}] {finding.message}")

    Args:
        rules: Validation rules to apply. Defaults to built-in rules.
        certification_thresholds: Custom score thresholds per level.
        permission_caps: Custom permission-based caps on certification.
    """

    def __init__(
        self,
        *,
        rules: list[Any] | None = None,
        certification_thresholds: dict[CertificationLevel, float] | None = None,
        permission_caps: dict[PermissionScope, CertificationLevel] | None = None,
    ) -> None:
        self._rules: list[Any] = (
            list(rules) if rules is not None else list(DEFAULT_RULES)
        )
        self._thresholds = certification_thresholds or dict(CERTIFICATION_THRESHOLDS)
        self._permission_caps = permission_caps or dict(PERMISSION_CAPS)

    def validate(self, manifest: SecurityManifest) -> ValidationReport:
        """Run all validation rules and produce a report.

        Args:
            manifest: The manifest to validate.

        Returns:
            A ValidationReport with findings, score, and max certification level.
        """
        all_findings: list[ValidationFinding] = []

        for rule in self._rules:
            try:
                findings = rule.validate(manifest)
                all_findings.extend(findings)
            except Exception:
                logger.exception(
                    "Validation rule %s failed", getattr(rule, "rule_id", "unknown")
                )
                all_findings.append(
                    ValidationFinding(
                        severity=ValidationSeverity.WARNING,
                        category="internal",
                        message=f"Validation rule '{getattr(rule, 'rule_id', 'unknown')}' failed with an exception",
                    )
                )

        score = self._compute_score(all_findings)
        max_level = self._compute_max_level(score, manifest)

        return ValidationReport(
            manifest_id=manifest.manifest_id,
            tool_name=manifest.tool_name,
            findings=all_findings,
            score=score,
            max_certification_level=max_level,
        )

    def add_rule(self, rule: Any) -> None:
        """Add a custom validation rule."""
        self._rules.append(rule)

    def remove_rule(self, rule_id: str) -> bool:
        """Remove a rule by ID. Returns True if found and removed."""
        for i, rule in enumerate(self._rules):
            if getattr(rule, "rule_id", None) == rule_id:
                self._rules.pop(i)
                return True
        return False

    @property
    def rules(self) -> list[Any]:
        """Current validation rules."""
        return list(self._rules)

    def _compute_score(self, findings: list[ValidationFinding]) -> float:
        """Compute a normalized score from findings.

        Scoring:
        - Start at 1.0
        - CRITICAL: -0.4 each
        - ERROR: -0.15 each
        - WARNING: -0.05 each
        - INFO: no penalty

        Score is clamped to [0.0, 1.0].
        """
        score = 1.0
        penalties = {
            ValidationSeverity.CRITICAL: 0.4,
            ValidationSeverity.ERROR: 0.15,
            ValidationSeverity.WARNING: 0.05,
            ValidationSeverity.INFO: 0.0,
        }

        for finding in findings:
            score -= penalties.get(finding.severity, 0.0)

        return max(0.0, min(1.0, score))

    def _compute_max_level(
        self, score: float, manifest: SecurityManifest
    ) -> CertificationLevel:
        """Determine the maximum certification level from score and permissions.

        The level is the highest one whose threshold the score meets,
        further capped by any permission-based restrictions.
        """
        # Any CRITICAL finding → uncertified
        # (handled by score being very low, but be explicit)
        if score < self._thresholds.get(CertificationLevel.SELF_ATTESTED, 0.3):
            return CertificationLevel.UNCERTIFIED

        # Find highest level by score
        level = CertificationLevel.UNCERTIFIED
        for cert_level in [
            CertificationLevel.STRICT,
            CertificationLevel.STANDARD,
            CertificationLevel.BASIC,
            CertificationLevel.SELF_ATTESTED,
        ]:
            threshold = self._thresholds.get(cert_level, 1.0)
            if score >= threshold:
                level = cert_level
                break

        # Apply permission caps
        level_order = list(CertificationLevel)
        for permission, cap in self._permission_caps.items():
            if permission in manifest.permissions:
                if level_order.index(level) > level_order.index(cap):
                    level = cap

        return level
