"""Tests for Compliance Reporting (Phase 18).

Covers frameworks, requirements, report generation, findings,
sections, reporter checks, comparison, and history.
"""

from __future__ import annotations

import pytest

from fastmcp.server.security.compliance.frameworks import (
    ComplianceFramework,
    ComplianceRequirement,
    ComplianceStatus,
    RequirementCategory,
)
from fastmcp.server.security.compliance.reports import (
    ComplianceReport,
    ComplianceReporter,
    FindingSeverity,
    ReportFinding,
    ReportSection,
    ReportType,
)

# ── Requirement tests ─────────────────────────────────────────────


class TestComplianceRequirement:
    def test_default_requirement(self):
        req = ComplianceRequirement()
        assert req.requirement_id.startswith("req-")
        assert req.mandatory is True
        assert req.category == RequirementCategory.RISK_MANAGEMENT

    def test_custom_requirement(self):
        req = ComplianceRequirement(
            requirement_id="r-001",
            name="Test Req",
            description="A test requirement",
            category=RequirementCategory.ACCESS_CONTROL,
            check_key="test_check",
        )
        assert req.requirement_id == "r-001"
        assert req.check_key == "test_check"

    def test_auto_check_key(self):
        req = ComplianceRequirement(requirement_id="r-002")
        assert req.check_key == "r-002"

    def test_to_dict(self):
        req = ComplianceRequirement(
            name="Test", category=RequirementCategory.AUDIT_LOGGING
        )
        d = req.to_dict()
        assert d["name"] == "Test"
        assert d["category"] == "audit_logging"
        assert d["mandatory"] is True


# ── Framework tests ───────────────────────────────────────────────


class TestComplianceFramework:
    def test_default_framework(self):
        fw = ComplianceFramework()
        assert fw.framework_id.startswith("fw-")
        assert fw.requirement_count == 0

    def test_add_requirement(self):
        fw = ComplianceFramework()
        fw.add_requirement(ComplianceRequirement(name="Req A"))
        assert fw.requirement_count == 1

    def test_remove_requirement(self):
        fw = ComplianceFramework()
        req = ComplianceRequirement(requirement_id="r-1", name="Req A")
        fw.add_requirement(req)
        assert fw.remove_requirement("r-1")
        assert fw.requirement_count == 0

    def test_remove_nonexistent(self):
        fw = ComplianceFramework()
        assert not fw.remove_requirement("nonexistent")

    def test_get_requirement(self):
        fw = ComplianceFramework()
        req = ComplianceRequirement(requirement_id="r-1")
        fw.add_requirement(req)
        assert fw.get_requirement("r-1") is req
        assert fw.get_requirement("nope") is None

    def test_get_by_category(self):
        fw = ComplianceFramework()
        fw.add_requirement(
            ComplianceRequirement(category=RequirementCategory.ACCESS_CONTROL)
        )
        fw.add_requirement(
            ComplianceRequirement(category=RequirementCategory.AUDIT_LOGGING)
        )
        fw.add_requirement(
            ComplianceRequirement(category=RequirementCategory.ACCESS_CONTROL)
        )
        assert (
            len(fw.get_requirements_by_category(RequirementCategory.ACCESS_CONTROL))
            == 2
        )

    def test_mandatory_requirements(self):
        fw = ComplianceFramework()
        fw.add_requirement(ComplianceRequirement(mandatory=True))
        fw.add_requirement(ComplianceRequirement(mandatory=False))
        assert fw.mandatory_count == 1

    def test_to_dict(self):
        fw = ComplianceFramework(name="Test FW", version="2.0")
        fw.add_requirement(ComplianceRequirement())
        d = fw.to_dict()
        assert d["name"] == "Test FW"
        assert d["version"] == "2.0"
        assert d["requirement_count"] == 1

    def test_securemcp_default(self):
        fw = ComplianceFramework.securemcp_default()
        assert fw.name == "SecureMCP Security Framework"
        assert fw.requirement_count >= 10
        assert fw.mandatory_count >= 10


# ── ReportFinding tests ──────────────────────────────────────────


class TestReportFinding:
    def test_default_finding(self):
        f = ReportFinding()
        assert f.finding_id.startswith("find-")
        assert f.status == ComplianceStatus.NOT_ASSESSED

    def test_compliant_finding(self):
        f = ReportFinding(status=ComplianceStatus.COMPLIANT)
        assert f.is_compliant

    def test_non_compliant_finding(self):
        f = ReportFinding(status=ComplianceStatus.NON_COMPLIANT)
        assert not f.is_compliant

    def test_not_applicable_is_compliant(self):
        f = ReportFinding(status=ComplianceStatus.NOT_APPLICABLE)
        assert f.is_compliant

    def test_to_dict(self):
        f = ReportFinding(
            requirement_id="r-1",
            status=ComplianceStatus.COMPLIANT,
            severity=FindingSeverity.INFO,
            message="All good",
        )
        d = f.to_dict()
        assert d["requirement_id"] == "r-1"
        assert d["status"] == "compliant"
        assert d["severity"] == "info"


# ── ReportSection tests ──────────────────────────────────────────


class TestReportSection:
    def test_default_section(self):
        s = ReportSection()
        assert s.section_id.startswith("sec-")
        assert s.finding_count == 0

    def test_add_finding(self):
        s = ReportSection()
        s.add_finding(ReportFinding(status=ComplianceStatus.COMPLIANT))
        s.add_finding(ReportFinding(status=ComplianceStatus.NON_COMPLIANT))
        assert s.finding_count == 2
        assert s.compliant_count == 1
        assert s.non_compliant_count == 1

    def test_compliance_rate(self):
        s = ReportSection()
        s.add_finding(ReportFinding(status=ComplianceStatus.COMPLIANT))
        s.add_finding(ReportFinding(status=ComplianceStatus.COMPLIANT))
        s.add_finding(ReportFinding(status=ComplianceStatus.NON_COMPLIANT))
        assert s.compliance_rate == pytest.approx(2 / 3)

    def test_empty_compliance_rate(self):
        s = ReportSection()
        assert s.compliance_rate == 1.0

    def test_to_dict(self):
        s = ReportSection(
            name="Access Control", category=RequirementCategory.ACCESS_CONTROL
        )
        s.add_finding(ReportFinding(status=ComplianceStatus.COMPLIANT))
        d = s.to_dict()
        assert d["name"] == "Access Control"
        assert d["compliance_rate"] == 1.0


# ── ComplianceReport tests ───────────────────────────────────────


class TestComplianceReport:
    def test_default_report(self):
        r = ComplianceReport()
        assert r.report_id.startswith("rpt-")
        assert r.report_type == ReportType.FULL
        assert r.compliance_score == 1.0

    def test_add_section(self):
        r = ComplianceReport()
        s = ReportSection(category=RequirementCategory.ACCESS_CONTROL)
        r.add_section(s)
        assert len(r.sections) == 1

    def test_get_section(self):
        r = ComplianceReport()
        s = ReportSection(category=RequirementCategory.AUDIT_LOGGING)
        r.add_section(s)
        assert r.get_section(RequirementCategory.AUDIT_LOGGING) is s
        assert r.get_section(RequirementCategory.ACCESS_CONTROL) is None

    def test_compliance_score(self):
        r = ComplianceReport()
        s = ReportSection()
        s.add_finding(ReportFinding(status=ComplianceStatus.COMPLIANT))
        s.add_finding(ReportFinding(status=ComplianceStatus.NON_COMPLIANT))
        r.add_section(s)
        assert r.compliance_score == pytest.approx(0.5)

    def test_critical_findings(self):
        r = ComplianceReport()
        s = ReportSection()
        s.add_finding(
            ReportFinding(
                status=ComplianceStatus.NON_COMPLIANT,
                severity=FindingSeverity.CRITICAL,
            )
        )
        s.add_finding(
            ReportFinding(
                status=ComplianceStatus.COMPLIANT,
                severity=FindingSeverity.CRITICAL,
            )
        )
        r.add_section(s)
        assert len(r.critical_findings) == 1

    def test_high_findings(self):
        r = ComplianceReport()
        s = ReportSection()
        s.add_finding(
            ReportFinding(
                status=ComplianceStatus.NON_COMPLIANT,
                severity=FindingSeverity.HIGH,
            )
        )
        r.add_section(s)
        assert len(r.high_findings) == 1

    def test_to_dict(self):
        r = ComplianceReport(framework_name="Test FW")
        d = r.to_dict()
        assert d["framework_name"] == "Test FW"
        assert d["compliance_score"] == 1.0
        assert "sections" in d

    def test_to_summary_dict(self):
        r = ComplianceReport(framework_name="Test FW")
        d = r.to_summary_dict()
        assert d["framework_name"] == "Test FW"
        assert "sections" not in d


# ── ComplianceReporter tests ─────────────────────────────────────


def _compliant_check():
    return (ComplianceStatus.COMPLIANT, "All good", {"detail": "ok"})


def _non_compliant_check():
    return (ComplianceStatus.NON_COMPLIANT, "Issue found", {"detail": "bad"})


def _failing_check():
    raise RuntimeError("Check broke")


class TestComplianceReporter:
    def test_default_reporter(self):
        reporter = ComplianceReporter()
        assert reporter.framework.name == "SecureMCP Security Framework"
        assert reporter.report_count == 0

    def test_register_check(self):
        reporter = ComplianceReporter()
        reporter.register_check("test_check", _compliant_check)
        assert "test_check" in reporter.registered_checks

    def test_unregister_check(self):
        reporter = ComplianceReporter()
        reporter.register_check("test_check", _compliant_check)
        assert reporter.unregister_check("test_check")
        assert "test_check" not in reporter.registered_checks
        assert not reporter.unregister_check("nonexistent")

    def test_generate_report_no_checks(self):
        fw = ComplianceFramework(name="Minimal")
        fw.add_requirement(
            ComplianceRequirement(
                name="Req A",
                category=RequirementCategory.ACCESS_CONTROL,
                check_key="check_a",
            )
        )
        reporter = ComplianceReporter(framework=fw)
        report = reporter.generate_report()
        assert report.total_findings == 1
        finding = report.all_findings[0]
        assert finding.status == ComplianceStatus.NOT_ASSESSED

    def test_generate_report_compliant(self):
        fw = ComplianceFramework(name="Test")
        fw.add_requirement(
            ComplianceRequirement(
                name="Check A",
                category=RequirementCategory.AUDIT_LOGGING,
                check_key="check_a",
            )
        )
        reporter = ComplianceReporter(framework=fw)
        reporter.register_check("check_a", _compliant_check)
        report = reporter.generate_report()
        assert report.compliance_score == 1.0
        assert report.all_findings[0].status == ComplianceStatus.COMPLIANT

    def test_generate_report_non_compliant(self):
        fw = ComplianceFramework(name="Test")
        fw.add_requirement(
            ComplianceRequirement(
                name="Check A",
                category=RequirementCategory.ACCESS_CONTROL,
                check_key="check_a",
            )
        )
        reporter = ComplianceReporter(framework=fw)
        reporter.register_check("check_a", _non_compliant_check)
        report = reporter.generate_report()
        assert report.compliance_score == 0.0
        finding = report.all_findings[0]
        assert finding.status == ComplianceStatus.NON_COMPLIANT
        assert (
            finding.severity == FindingSeverity.CRITICAL
        )  # ACCESS_CONTROL + mandatory

    def test_generate_report_error_handling(self):
        fw = ComplianceFramework(name="Test")
        fw.add_requirement(
            ComplianceRequirement(
                name="Broken",
                category=RequirementCategory.RISK_MANAGEMENT,
                check_key="broken_check",
            )
        )
        reporter = ComplianceReporter(framework=fw)
        reporter.register_check("broken_check", _failing_check)
        report = reporter.generate_report()
        finding = report.all_findings[0]
        assert finding.status == ComplianceStatus.NON_COMPLIANT
        assert "error" in finding.evidence

    def test_generate_report_mixed(self):
        fw = ComplianceFramework(name="Mixed")
        fw.add_requirement(
            ComplianceRequirement(
                name="Good",
                category=RequirementCategory.AUDIT_LOGGING,
                check_key="good",
            )
        )
        fw.add_requirement(
            ComplianceRequirement(
                name="Bad",
                category=RequirementCategory.AUDIT_LOGGING,
                check_key="bad",
            )
        )
        reporter = ComplianceReporter(framework=fw)
        reporter.register_check("good", _compliant_check)
        reporter.register_check("bad", _non_compliant_check)
        report = reporter.generate_report()
        assert report.compliance_score == pytest.approx(0.5)
        assert report.compliant_count == 1
        assert report.non_compliant_count == 1

    def test_generate_report_category_filter(self):
        fw = ComplianceFramework(name="Multi")
        fw.add_requirement(
            ComplianceRequirement(
                name="AC",
                category=RequirementCategory.ACCESS_CONTROL,
                check_key="ac",
            )
        )
        fw.add_requirement(
            ComplianceRequirement(
                name="AL",
                category=RequirementCategory.AUDIT_LOGGING,
                check_key="al",
            )
        )
        reporter = ComplianceReporter(framework=fw)
        reporter.register_check("ac", _compliant_check)
        reporter.register_check("al", _non_compliant_check)
        report = reporter.generate_report(
            categories={RequirementCategory.ACCESS_CONTROL}
        )
        assert report.total_findings == 1
        assert report.compliance_score == 1.0

    def test_report_type(self):
        reporter = ComplianceReporter(framework=ComplianceFramework(name="Empty"))
        report = reporter.generate_report(report_type=ReportType.EXECUTIVE)
        assert report.report_type == ReportType.EXECUTIVE

    def test_report_history(self):
        reporter = ComplianceReporter(framework=ComplianceFramework(name="H"))
        reporter.generate_report()
        reporter.generate_report()
        assert reporter.report_count == 2
        assert len(reporter.get_report_history()) == 2
        assert reporter.get_latest_report() is not None

    def test_latest_report_empty(self):
        reporter = ComplianceReporter(framework=ComplianceFramework(name="E"))
        assert reporter.get_latest_report() is None

    def test_compare_reports(self):
        fw = ComplianceFramework(name="Compare")
        fw.add_requirement(
            ComplianceRequirement(
                requirement_id="r-1",
                name="Check A",
                category=RequirementCategory.ACCESS_CONTROL,
                check_key="ca",
            )
        )
        reporter = ComplianceReporter(framework=fw)

        # Report A: non-compliant
        reporter.register_check("ca", _non_compliant_check)
        report_a = reporter.generate_report()

        # Report B: compliant
        reporter.register_check("ca", _compliant_check)
        report_b = reporter.generate_report()

        comparison = reporter.compare_reports(report_a, report_b)
        assert comparison["score_delta"] == pytest.approx(1.0)
        assert comparison["resolved_count"] == 1
        assert comparison["new_issue_count"] == 0

    def test_compare_reports_new_issues(self):
        fw = ComplianceFramework(name="Compare")
        fw.add_requirement(
            ComplianceRequirement(
                requirement_id="r-1",
                name="Check A",
                category=RequirementCategory.ACCESS_CONTROL,
                check_key="ca",
            )
        )
        reporter = ComplianceReporter(framework=fw)

        # Report A: compliant
        reporter.register_check("ca", _compliant_check)
        report_a = reporter.generate_report()

        # Report B: non-compliant
        reporter.register_check("ca", _non_compliant_check)
        report_b = reporter.generate_report()

        comparison = reporter.compare_reports(report_a, report_b)
        assert comparison["score_delta"] == pytest.approx(-1.0)
        assert comparison["new_issue_count"] == 1
        assert comparison["resolved_count"] == 0

    def test_severity_mapping(self):
        fw = ComplianceFramework(name="Sev")
        fw.add_requirement(
            ComplianceRequirement(
                name="AC Req",
                category=RequirementCategory.ACCESS_CONTROL,
                check_key="ac",
                mandatory=True,
            )
        )
        fw.add_requirement(
            ComplianceRequirement(
                name="Optional Req",
                category=RequirementCategory.RISK_MANAGEMENT,
                check_key="opt",
                mandatory=False,
            )
        )
        reporter = ComplianceReporter(framework=fw)
        reporter.register_check("ac", _non_compliant_check)
        reporter.register_check("opt", _non_compliant_check)
        report = reporter.generate_report()

        findings = {f.requirement_name: f for f in report.all_findings}
        assert findings["AC Req"].severity == FindingSeverity.CRITICAL
        assert findings["Optional Req"].severity == FindingSeverity.LOW

    def test_statistics(self):
        reporter = ComplianceReporter()
        reporter.register_check("some_check", _compliant_check)
        stats = reporter.get_statistics()
        assert stats["registered_checks"] == 1
        assert stats["total_requirements"] >= 10


# ── Sections grouped by category ─────────────────────────────────


class TestSectionsGrouping:
    def test_multiple_categories_produce_sections(self):
        fw = ComplianceFramework(name="Multi")
        fw.add_requirement(
            ComplianceRequirement(
                category=RequirementCategory.ACCESS_CONTROL,
                check_key="ac",
            )
        )
        fw.add_requirement(
            ComplianceRequirement(
                category=RequirementCategory.AUDIT_LOGGING,
                check_key="al",
            )
        )
        fw.add_requirement(
            ComplianceRequirement(
                category=RequirementCategory.SANDBOXING,
                check_key="sb",
            )
        )
        reporter = ComplianceReporter(framework=fw)
        reporter.register_check("ac", _compliant_check)
        reporter.register_check("al", _compliant_check)
        reporter.register_check("sb", _non_compliant_check)
        report = reporter.generate_report()
        assert len(report.sections) == 3

    def test_section_summary(self):
        fw = ComplianceFramework(name="Sum")
        fw.add_requirement(
            ComplianceRequirement(
                category=RequirementCategory.ACCESS_CONTROL,
                check_key="a",
            )
        )
        fw.add_requirement(
            ComplianceRequirement(
                category=RequirementCategory.ACCESS_CONTROL,
                check_key="b",
            )
        )
        reporter = ComplianceReporter(framework=fw)
        reporter.register_check("a", _compliant_check)
        reporter.register_check("b", _non_compliant_check)
        report = reporter.generate_report()
        section = report.get_section(RequirementCategory.ACCESS_CONTROL)
        assert section is not None
        assert "1/2" in section.summary


# ── Default framework tests ──────────────────────────────────────


class TestDefaultFramework:
    def test_all_categories_covered(self):
        fw = ComplianceFramework.securemcp_default()
        categories = {r.category for r in fw.requirements}
        assert RequirementCategory.ACCESS_CONTROL in categories
        assert RequirementCategory.AUDIT_LOGGING in categories
        assert RequirementCategory.CONSENT_MANAGEMENT in categories
        assert RequirementCategory.SANDBOXING in categories
        assert RequirementCategory.CERTIFICATION in categories
        assert RequirementCategory.FEDERATION in categories
        assert RequirementCategory.PROVENANCE in categories

    def test_full_report_with_default_framework(self):
        reporter = ComplianceReporter()
        report = reporter.generate_report()
        # All findings are NOT_ASSESSED since no checks registered
        assert report.total_findings >= 10
        for finding in report.all_findings:
            assert finding.status == ComplianceStatus.NOT_ASSESSED


# ── Import tests ─────────────────────────────────────────────────


class TestImports:
    def test_compliance_imports(self):
        from fastmcp.server.security.compliance import (
            ComplianceReporter,
        )

        assert ComplianceReporter is not None

    def test_top_level_imports(self):
        from fastmcp.server.security import (
            ComplianceReporter,
        )

        assert ComplianceReporter is not None
