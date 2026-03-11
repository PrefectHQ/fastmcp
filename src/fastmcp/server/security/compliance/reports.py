"""Compliance report generation.

Aggregates data from all SecureMCP components into structured
compliance reports with findings, scores, and recommendations.
"""

from __future__ import annotations

import enum
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastmcp.server.security.compliance.frameworks import (
    ComplianceFramework,
    ComplianceRequirement,
    ComplianceStatus,
    RequirementCategory,
)


class ReportType(enum.Enum):
    """Type of compliance report."""

    FULL = "full"
    SUMMARY = "summary"
    FINDINGS_ONLY = "findings_only"
    EXECUTIVE = "executive"


class FindingSeverity(enum.Enum):
    """Severity level for compliance findings."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass
class ReportFinding:
    """A single finding in a compliance report."""

    finding_id: str = ""
    requirement_id: str = ""
    requirement_name: str = ""
    status: ComplianceStatus = ComplianceStatus.NOT_ASSESSED
    severity: FindingSeverity = FindingSeverity.INFO
    category: RequirementCategory = RequirementCategory.RISK_MANAGEMENT
    message: str = ""
    recommendation: str = ""
    evidence: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        if not self.finding_id:
            self.finding_id = f"find-{uuid.uuid4().hex[:8]}"

    @property
    def is_compliant(self) -> bool:
        return self.status in (
            ComplianceStatus.COMPLIANT,
            ComplianceStatus.NOT_APPLICABLE,
        )

    def to_dict(self) -> dict:
        return {
            "finding_id": self.finding_id,
            "requirement_id": self.requirement_id,
            "requirement_name": self.requirement_name,
            "status": self.status.value,
            "severity": self.severity.value,
            "category": self.category.value,
            "message": self.message,
            "recommendation": self.recommendation,
            "evidence": self.evidence,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ReportSection:
    """A section grouping related findings in a report."""

    section_id: str = ""
    name: str = ""
    category: RequirementCategory = RequirementCategory.RISK_MANAGEMENT
    findings: list[ReportFinding] = field(default_factory=list)
    summary: str = ""

    def __post_init__(self):
        if not self.section_id:
            self.section_id = f"sec-{uuid.uuid4().hex[:8]}"

    def add_finding(self, finding: ReportFinding) -> None:
        self.findings.append(finding)

    @property
    def finding_count(self) -> int:
        return len(self.findings)

    @property
    def compliant_count(self) -> int:
        return sum(1 for f in self.findings if f.is_compliant)

    @property
    def non_compliant_count(self) -> int:
        return sum(1 for f in self.findings if not f.is_compliant)

    @property
    def compliance_rate(self) -> float:
        if not self.findings:
            return 1.0
        return self.compliant_count / self.finding_count

    def to_dict(self) -> dict:
        return {
            "section_id": self.section_id,
            "name": self.name,
            "category": self.category.value,
            "finding_count": self.finding_count,
            "compliant_count": self.compliant_count,
            "non_compliant_count": self.non_compliant_count,
            "compliance_rate": self.compliance_rate,
            "summary": self.summary,
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass
class ComplianceReport:
    """A complete compliance report."""

    report_id: str = ""
    report_type: ReportType = ReportType.FULL
    framework_id: str = ""
    framework_name: str = ""
    sections: list[ReportSection] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.report_id:
            self.report_id = f"rpt-{uuid.uuid4().hex[:8]}"

    def add_section(self, section: ReportSection) -> None:
        self.sections.append(section)

    def get_section(self, category: RequirementCategory) -> ReportSection | None:
        for s in self.sections:
            if s.category == category:
                return s
        return None

    @property
    def all_findings(self) -> list[ReportFinding]:
        findings = []
        for s in self.sections:
            findings.extend(s.findings)
        return findings

    @property
    def total_findings(self) -> int:
        return len(self.all_findings)

    @property
    def compliant_count(self) -> int:
        return sum(1 for f in self.all_findings if f.is_compliant)

    @property
    def non_compliant_count(self) -> int:
        return sum(1 for f in self.all_findings if not f.is_compliant)

    @property
    def compliance_score(self) -> float:
        """Overall compliance score 0.0 - 1.0."""
        findings = self.all_findings
        if not findings:
            return 1.0
        return self.compliant_count / len(findings)

    @property
    def critical_findings(self) -> list[ReportFinding]:
        return [
            f
            for f in self.all_findings
            if f.severity == FindingSeverity.CRITICAL and not f.is_compliant
        ]

    @property
    def high_findings(self) -> list[ReportFinding]:
        return [
            f
            for f in self.all_findings
            if f.severity == FindingSeverity.HIGH and not f.is_compliant
        ]

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "report_type": self.report_type.value,
            "framework_id": self.framework_id,
            "framework_name": self.framework_name,
            "compliance_score": self.compliance_score,
            "total_findings": self.total_findings,
            "compliant_count": self.compliant_count,
            "non_compliant_count": self.non_compliant_count,
            "critical_count": len(self.critical_findings),
            "high_count": len(self.high_findings),
            "section_count": len(self.sections),
            "sections": [s.to_dict() for s in self.sections],
            "generated_at": self.generated_at.isoformat(),
        }

    def to_summary_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "report_type": self.report_type.value,
            "framework_name": self.framework_name,
            "compliance_score": self.compliance_score,
            "total_findings": self.total_findings,
            "compliant_count": self.compliant_count,
            "non_compliant_count": self.non_compliant_count,
            "critical_count": len(self.critical_findings),
            "high_count": len(self.high_findings),
            "generated_at": self.generated_at.isoformat(),
        }


# Check function type: takes no args, returns (ComplianceStatus, message, evidence_dict)
CheckResult = tuple[ComplianceStatus, str, dict]
CheckFunction = Callable[[], CheckResult]


class ComplianceReporter:
    """Generates compliance reports by running checks against a framework.

    Register check functions for requirement check_keys, then generate
    reports that evaluate each requirement and produce findings.
    """

    def __init__(
        self,
        framework: ComplianceFramework | None = None,
    ):
        self._framework = framework or ComplianceFramework.securemcp_default()
        self._checks: dict[str, CheckFunction] = {}
        self._report_history: list[ComplianceReport] = []

    @property
    def framework(self) -> ComplianceFramework:
        return self._framework

    @property
    def registered_checks(self) -> list[str]:
        return list(self._checks.keys())

    @property
    def report_count(self) -> int:
        return len(self._report_history)

    def register_check(self, check_key: str, check_fn: CheckFunction) -> None:
        """Register a check function for a requirement check_key."""
        self._checks[check_key] = check_fn

    def unregister_check(self, check_key: str) -> bool:
        """Remove a registered check."""
        if check_key in self._checks:
            del self._checks[check_key]
            return True
        return False

    def _severity_for_requirement(
        self, requirement: ComplianceRequirement
    ) -> FindingSeverity:
        """Determine finding severity based on requirement properties."""
        if requirement.mandatory:
            category_severity = {
                RequirementCategory.ACCESS_CONTROL: FindingSeverity.CRITICAL,
                RequirementCategory.SANDBOXING: FindingSeverity.CRITICAL,
                RequirementCategory.FEDERATION: FindingSeverity.HIGH,
                RequirementCategory.CERTIFICATION: FindingSeverity.HIGH,
                RequirementCategory.CONSENT_MANAGEMENT: FindingSeverity.HIGH,
                RequirementCategory.AUDIT_LOGGING: FindingSeverity.MEDIUM,
                RequirementCategory.PROVENANCE: FindingSeverity.MEDIUM,
                RequirementCategory.RISK_MANAGEMENT: FindingSeverity.MEDIUM,
                RequirementCategory.DATA_PROTECTION: FindingSeverity.HIGH,
                RequirementCategory.INCIDENT_RESPONSE: FindingSeverity.MEDIUM,
            }
            return category_severity.get(requirement.category, FindingSeverity.MEDIUM)
        return FindingSeverity.LOW

    def _run_check(self, requirement: ComplianceRequirement) -> ReportFinding:
        """Run a single requirement check and produce a finding."""
        check_fn = self._checks.get(requirement.check_key)

        if check_fn is None:
            return ReportFinding(
                requirement_id=requirement.requirement_id,
                requirement_name=requirement.name,
                status=ComplianceStatus.NOT_ASSESSED,
                severity=self._severity_for_requirement(requirement),
                category=requirement.category,
                message=f"No check registered for '{requirement.check_key}'",
                recommendation="Register a check function for this requirement",
            )

        try:
            status, message, evidence = check_fn()
            severity = (
                FindingSeverity.INFO
                if status
                in (ComplianceStatus.COMPLIANT, ComplianceStatus.NOT_APPLICABLE)
                else self._severity_for_requirement(requirement)
            )
            return ReportFinding(
                requirement_id=requirement.requirement_id,
                requirement_name=requirement.name,
                status=status,
                severity=severity,
                category=requirement.category,
                message=message,
                evidence=evidence,
            )
        except Exception as exc:
            return ReportFinding(
                requirement_id=requirement.requirement_id,
                requirement_name=requirement.name,
                status=ComplianceStatus.NON_COMPLIANT,
                severity=self._severity_for_requirement(requirement),
                category=requirement.category,
                message=f"Check failed with error: {exc}",
                recommendation="Fix the check function or underlying component",
                evidence={"error": str(exc)},
            )

    def generate_report(
        self,
        report_type: ReportType = ReportType.FULL,
        categories: set[RequirementCategory] | None = None,
    ) -> ComplianceReport:
        """Generate a compliance report.

        Args:
            report_type: Type of report to generate.
            categories: If set, only include these categories.

        Returns:
            A ComplianceReport with findings grouped by category.
        """
        report = ComplianceReport(
            report_type=report_type,
            framework_id=self._framework.framework_id,
            framework_name=self._framework.name,
        )

        # Group requirements by category
        categories_to_check = categories or {
            r.category for r in self._framework.requirements
        }
        for category in sorted(categories_to_check, key=lambda c: c.value):
            requirements = self._framework.get_requirements_by_category(category)
            if not requirements:
                continue

            section = ReportSection(
                name=category.value.replace("_", " ").title(),
                category=category,
            )

            for req in requirements:
                finding = self._run_check(req)
                section.add_finding(finding)

            section.summary = (
                f"{section.compliant_count}/{section.finding_count} requirements met"
            )
            report.add_section(section)

        self._report_history.append(report)
        return report

    def get_report_history(self) -> list[ComplianceReport]:
        """Get all previously generated reports."""
        return list(self._report_history)

    def get_latest_report(self) -> ComplianceReport | None:
        """Get the most recently generated report."""
        if self._report_history:
            return self._report_history[-1]
        return None

    def compare_reports(
        self, report_a: ComplianceReport, report_b: ComplianceReport
    ) -> dict:
        """Compare two reports and show compliance trend.

        Returns dict with score_delta, new_issues, resolved_issues.
        """
        a_findings = {f.requirement_id: f for f in report_a.all_findings}
        b_findings = {f.requirement_id: f for f in report_b.all_findings}

        new_issues = []
        resolved_issues = []

        for req_id, finding_b in b_findings.items():
            finding_a = a_findings.get(req_id)
            if finding_a is None:
                if not finding_b.is_compliant:
                    new_issues.append(finding_b.to_dict())
            elif finding_a.is_compliant and not finding_b.is_compliant:
                new_issues.append(finding_b.to_dict())
            elif not finding_a.is_compliant and finding_b.is_compliant:
                resolved_issues.append(finding_b.to_dict())

        return {
            "report_a_id": report_a.report_id,
            "report_b_id": report_b.report_id,
            "score_a": report_a.compliance_score,
            "score_b": report_b.compliance_score,
            "score_delta": report_b.compliance_score - report_a.compliance_score,
            "new_issues": new_issues,
            "resolved_issues": resolved_issues,
            "new_issue_count": len(new_issues),
            "resolved_count": len(resolved_issues),
        }

    def get_statistics(self) -> dict:
        """Get reporter statistics."""
        return {
            "framework_id": self._framework.framework_id,
            "framework_name": self._framework.name,
            "total_requirements": self._framework.requirement_count,
            "mandatory_requirements": self._framework.mandatory_count,
            "registered_checks": len(self._checks),
            "report_count": self.report_count,
        }
