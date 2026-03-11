"""Tests for Phase 12: Tool Certification & Attestation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastmcp.server.security.certification.attestation import (
    AttestationStatus,
    CertificationLevel,
    ToolAttestation,
    ValidationFinding,
    ValidationReport,
    ValidationSeverity,
)
from fastmcp.server.security.certification.manifest import (
    DataClassification,
    DataFlowDeclaration,
    PermissionScope,
    ResourceAccessDeclaration,
    SecurityManifest,
)
from fastmcp.server.security.certification.pipeline import (
    CertificationPipeline,
)
from fastmcp.server.security.certification.validator import (
    DataFlowRule,
    ManifestValidator,
    PermissionConsistencyRule,
    RequiredFieldsRule,
    ResourceAccessRule,
    SecurityBestPracticesRule,
    ValidationRule,
)
from fastmcp.server.security.contracts.crypto import (
    ContractCryptoHandler,
    SigningAlgorithm,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _good_manifest(**overrides) -> SecurityManifest:
    """Create a well-formed manifest for testing."""
    defaults = dict(
        tool_name="test-tool",
        version="1.0.0",
        author="test-author",
        description="A test tool for validation",
        permissions={PermissionScope.READ_RESOURCE},
        data_flows=[
            DataFlowDeclaration(
                source="input.query",
                destination="output.result",
                classification=DataClassification.INTERNAL,
                description="Query → results",
            ),
        ],
        resource_access=[
            ResourceAccessDeclaration(
                resource_pattern="docs://*",
                access_type="read",
                description="Read documents",
            ),
        ],
    )
    defaults.update(overrides)
    return SecurityManifest(**defaults)


def _crypto() -> ContractCryptoHandler:
    return ContractCryptoHandler(
        algorithm=SigningAlgorithm.HMAC_SHA256,
        secret_key=b"test-certification-key",
    )


# ═══════════════════════════════════════════════════════════════════
# SecurityManifest
# ═══════════════════════════════════════════════════════════════════


class TestSecurityManifest:
    def test_default_manifest(self):
        m = SecurityManifest()
        assert m.tool_name == ""
        assert m.version == "0.0.0"
        assert m.permissions == set()
        assert m.data_flows == []
        assert m.resource_access == []

    def test_manifest_to_dict(self):
        m = _good_manifest()
        d = m.to_dict()
        assert d["tool_name"] == "test-tool"
        assert d["version"] == "1.0.0"
        assert d["author"] == "test-author"
        assert "read_resource" in d["permissions"]
        assert len(d["data_flows"]) == 1
        assert len(d["resource_access"]) == 1

    def test_manifest_to_dict_deterministic(self):
        m = _good_manifest()
        assert m.to_dict() == m.to_dict()

    def test_data_flow_declaration(self):
        f = DataFlowDeclaration(
            source="input.x",
            destination="output.y",
            classification=DataClassification.PII,
            transforms=["encrypt", "hash"],
            retention="30d",
        )
        assert f.classification == DataClassification.PII
        assert len(f.transforms) == 2
        assert f.retention == "30d"

    def test_resource_access_declaration(self):
        r = ResourceAccessDeclaration(
            resource_pattern="file://data/*",
            access_type="write",
            required=False,
            classification=DataClassification.CONFIDENTIAL,
        )
        assert r.access_type == "write"
        assert not r.required


# ═══════════════════════════════════════════════════════════════════
# ValidationReport and ValidationFinding
# ═══════════════════════════════════════════════════════════════════


class TestValidationReport:
    def test_empty_report(self):
        r = ValidationReport()
        assert not r.has_errors
        assert not r.has_critical
        assert r.error_count == 0
        assert r.warning_count == 0

    def test_report_with_findings(self):
        r = ValidationReport(
            findings=[
                ValidationFinding(severity=ValidationSeverity.ERROR, message="err1"),
                ValidationFinding(severity=ValidationSeverity.WARNING, message="warn1"),
                ValidationFinding(severity=ValidationSeverity.WARNING, message="warn2"),
                ValidationFinding(severity=ValidationSeverity.INFO, message="info1"),
            ]
        )
        assert r.has_errors
        assert not r.has_critical
        assert r.error_count == 1
        assert r.warning_count == 2

    def test_report_with_critical(self):
        r = ValidationReport(
            findings=[
                ValidationFinding(severity=ValidationSeverity.CRITICAL, message="crit"),
            ]
        )
        assert r.has_critical
        assert r.has_errors  # critical counts as error-level

    def test_findings_by_severity(self):
        r = ValidationReport(
            findings=[
                ValidationFinding(severity=ValidationSeverity.ERROR, message="e"),
                ValidationFinding(severity=ValidationSeverity.WARNING, message="w"),
            ]
        )
        assert len(r.findings_by_severity(ValidationSeverity.ERROR)) == 1
        assert len(r.findings_by_severity(ValidationSeverity.INFO)) == 0

    def test_report_to_dict(self):
        r = ValidationReport(tool_name="test", score=0.85)
        d = r.to_dict()
        assert d["tool_name"] == "test"
        assert d["score"] == 0.85


# ═══════════════════════════════════════════════════════════════════
# ToolAttestation
# ═══════════════════════════════════════════════════════════════════


class TestToolAttestation:
    def test_default_attestation(self):
        a = ToolAttestation()
        assert a.status == AttestationStatus.PENDING
        assert a.certification_level == CertificationLevel.UNCERTIFIED
        assert not a.is_valid()

    def test_valid_attestation(self):
        a = ToolAttestation(status=AttestationStatus.VALID)
        a.set_default_expiry(timedelta(days=90))
        assert a.is_valid()

    def test_expired_attestation(self):
        a = ToolAttestation(
            status=AttestationStatus.VALID,
            issued_at=datetime.now(timezone.utc) - timedelta(days=100),
        )
        a.set_default_expiry(timedelta(days=90))
        assert not a.is_valid()

    def test_revoked_attestation(self):
        a = ToolAttestation(status=AttestationStatus.REVOKED)
        assert not a.is_valid()

    def test_signable_payload(self):
        a = ToolAttestation(
            tool_name="test",
            certification_level=CertificationLevel.STANDARD,
        )
        payload = a.signable_payload()
        assert payload["tool_name"] == "test"
        assert payload["certification_level"] == "standard"
        assert "signature" not in payload
        assert "status" not in payload

    def test_to_dict_includes_signature_and_status(self):
        a = ToolAttestation(
            tool_name="test",
            signature="abc123",
            status=AttestationStatus.VALID,
        )
        d = a.to_dict()
        assert d["signature"] == "abc123"
        assert d["status"] == "valid"


# ═══════════════════════════════════════════════════════════════════
# RequiredFieldsRule
# ═══════════════════════════════════════════════════════════════════


class TestRequiredFieldsRule:
    def test_good_manifest(self):
        findings = RequiredFieldsRule().validate(_good_manifest())
        assert len(findings) == 0

    def test_missing_tool_name(self):
        findings = RequiredFieldsRule().validate(_good_manifest(tool_name=""))
        assert any(f.severity == ValidationSeverity.CRITICAL for f in findings)
        assert any("tool_name" in f.field_path for f in findings)

    def test_missing_version(self):
        findings = RequiredFieldsRule().validate(_good_manifest(version="0.0.0"))
        assert any(f.severity == ValidationSeverity.ERROR for f in findings)

    def test_missing_author(self):
        findings = RequiredFieldsRule().validate(_good_manifest(author=""))
        assert any(f.severity == ValidationSeverity.ERROR for f in findings)

    def test_missing_description(self):
        findings = RequiredFieldsRule().validate(_good_manifest(description=""))
        assert any(f.severity == ValidationSeverity.WARNING for f in findings)


# ═══════════════════════════════════════════════════════════════════
# PermissionConsistencyRule
# ═══════════════════════════════════════════════════════════════════


class TestPermissionConsistencyRule:
    def test_consistent_read(self):
        m = _good_manifest()
        findings = PermissionConsistencyRule().validate(m)
        assert len(findings) == 0

    def test_missing_read_permission(self):
        m = _good_manifest(permissions=set())  # no permissions but has resource reads
        findings = PermissionConsistencyRule().validate(m)
        assert any("READ_RESOURCE" in f.message for f in findings)

    def test_missing_write_permission(self):
        m = _good_manifest(
            permissions={PermissionScope.READ_RESOURCE},
            resource_access=[
                ResourceAccessDeclaration(resource_pattern="x://", access_type="write"),
            ],
        )
        findings = PermissionConsistencyRule().validate(m)
        assert any("WRITE_RESOURCE" in f.message for f in findings)

    def test_network_flow_without_permission(self):
        m = _good_manifest(
            data_flows=[
                DataFlowDeclaration(
                    source="input.x",
                    destination="https://api.example.com/data",
                    classification=DataClassification.INTERNAL,
                ),
            ],
        )
        findings = PermissionConsistencyRule().validate(m)
        assert any("NETWORK_ACCESS" in f.message for f in findings)

    def test_sensitive_data_permission_mismatch(self):
        m = _good_manifest(
            permissions={PermissionScope.READ_RESOURCE, PermissionScope.SENSITIVE_DATA},
        )
        findings = PermissionConsistencyRule().validate(m)
        assert any("SENSITIVE_DATA" in f.message for f in findings)


# ═══════════════════════════════════════════════════════════════════
# DataFlowRule
# ═══════════════════════════════════════════════════════════════════


class TestDataFlowRule:
    def test_no_flows_warning(self):
        m = _good_manifest(data_flows=[])
        findings = DataFlowRule().validate(m)
        assert any(f.severity == ValidationSeverity.WARNING for f in findings)

    def test_missing_source(self):
        m = _good_manifest(
            data_flows=[DataFlowDeclaration(destination="output.x")],
        )
        findings = DataFlowRule().validate(m)
        assert any("no source" in f.message for f in findings)

    def test_missing_destination(self):
        m = _good_manifest(
            data_flows=[DataFlowDeclaration(source="input.x")],
        )
        findings = DataFlowRule().validate(m)
        assert any("no destination" in f.message for f in findings)

    def test_sensitive_network_flow_no_transforms(self):
        m = _good_manifest(
            data_flows=[
                DataFlowDeclaration(
                    source="input.x",
                    destination="https://api.example.com",
                    classification=DataClassification.PII,
                ),
            ],
        )
        findings = DataFlowRule().validate(m)
        assert any("transforms" in f.message.lower() for f in findings)

    def test_sensitive_flow_with_transforms_ok(self):
        m = _good_manifest(
            data_flows=[
                DataFlowDeclaration(
                    source="input.x",
                    destination="https://api.example.com",
                    classification=DataClassification.PII,
                    transforms=["encrypt"],
                    retention="7d",
                ),
            ],
        )
        findings = DataFlowRule().validate(m)
        assert not any(f.severity == ValidationSeverity.ERROR for f in findings)

    def test_sensitive_no_retention(self):
        m = _good_manifest(
            data_flows=[
                DataFlowDeclaration(
                    source="input.x",
                    destination="output.y",
                    classification=DataClassification.PHI,
                    retention="none",
                ),
            ],
        )
        findings = DataFlowRule().validate(m)
        assert any("retention" in f.message.lower() for f in findings)


# ═══════════════════════════════════════════════════════════════════
# ResourceAccessRule
# ═══════════════════════════════════════════════════════════════════


class TestResourceAccessRule:
    def test_valid_access(self):
        m = _good_manifest()
        findings = ResourceAccessRule().validate(m)
        assert not any(f.severity == ValidationSeverity.ERROR for f in findings)

    def test_empty_pattern(self):
        m = _good_manifest(
            resource_access=[ResourceAccessDeclaration(resource_pattern="")],
        )
        findings = ResourceAccessRule().validate(m)
        assert any("no pattern" in f.message for f in findings)

    def test_invalid_access_type(self):
        m = _good_manifest(
            resource_access=[
                ResourceAccessDeclaration(
                    resource_pattern="x://", access_type="delete"
                ),
            ],
        )
        findings = ResourceAccessRule().validate(m)
        assert any("Invalid access type" in f.message for f in findings)

    def test_broad_wildcard_warning(self):
        m = _good_manifest(
            resource_access=[
                ResourceAccessDeclaration(
                    resource_pattern="file://**", access_type="read"
                ),
            ],
        )
        findings = ResourceAccessRule().validate(m)
        assert any("wildcard" in f.message.lower() for f in findings)


# ═══════════════════════════════════════════════════════════════════
# SecurityBestPracticesRule
# ═══════════════════════════════════════════════════════════════════


class TestSecurityBestPracticesRule:
    def test_subprocess_warning(self):
        m = _good_manifest(
            permissions={
                PermissionScope.READ_RESOURCE,
                PermissionScope.SUBPROCESS_EXEC,
            },
        )
        findings = SecurityBestPracticesRule().validate(m)
        assert any("SUBPROCESS_EXEC" in f.message for f in findings)

    def test_cross_origin_warning(self):
        m = _good_manifest(
            permissions={PermissionScope.READ_RESOURCE, PermissionScope.CROSS_ORIGIN},
        )
        findings = SecurityBestPracticesRule().validate(m)
        assert any("CROSS_ORIGIN" in f.message for f in findings)

    def test_long_execution_time_warning(self):
        m = _good_manifest(max_execution_time_seconds=600)
        findings = SecurityBestPracticesRule().validate(m)
        assert any("execution time" in f.message.lower() for f in findings)

    def test_regulated_data_without_consent(self):
        m = _good_manifest(
            data_flows=[
                DataFlowDeclaration(
                    source="input.x",
                    destination="output.y",
                    classification=DataClassification.PII,
                ),
            ],
            requires_consent=False,
        )
        findings = SecurityBestPracticesRule().validate(m)
        assert any("consent" in f.message.lower() for f in findings)

    def test_regulated_data_with_consent_ok(self):
        m = _good_manifest(
            data_flows=[
                DataFlowDeclaration(
                    source="input.x",
                    destination="output.y",
                    classification=DataClassification.PII,
                ),
            ],
            requires_consent=True,
        )
        findings = SecurityBestPracticesRule().validate(m)
        assert not any("consent" in f.message.lower() for f in findings)


# ═══════════════════════════════════════════════════════════════════
# ManifestValidator
# ═══════════════════════════════════════════════════════════════════


class TestManifestValidator:
    def test_good_manifest_high_score(self):
        v = ManifestValidator()
        report = v.validate(_good_manifest())
        assert report.score >= 0.8
        assert report.max_certification_level in (
            CertificationLevel.STANDARD,
            CertificationLevel.STRICT,
        )

    def test_empty_manifest_low_score(self):
        v = ManifestValidator()
        report = v.validate(SecurityManifest())
        assert report.score < 0.5
        assert report.has_errors

    def test_certification_level_capped_by_permissions(self):
        v = ManifestValidator()
        m = _good_manifest(
            permissions={
                PermissionScope.READ_RESOURCE,
                PermissionScope.SUBPROCESS_EXEC,
            },
        )
        report = v.validate(m)
        level_order = list(CertificationLevel)
        assert level_order.index(report.max_certification_level) <= level_order.index(
            CertificationLevel.BASIC
        )

    def test_custom_rule(self):
        class NoNetworkRule:
            rule_id = "no_network"

            def validate(self, manifest):
                if PermissionScope.NETWORK_ACCESS in manifest.permissions:
                    return [
                        ValidationFinding(
                            severity=ValidationSeverity.CRITICAL,
                            category="custom",
                            message="Network access is forbidden",
                        )
                    ]
                return []

        v = ManifestValidator()
        v.add_rule(NoNetworkRule())
        m = _good_manifest(
            permissions={PermissionScope.READ_RESOURCE, PermissionScope.NETWORK_ACCESS},
        )
        report = v.validate(m)
        assert any("forbidden" in f.message for f in report.findings)

    def test_remove_rule(self):
        v = ManifestValidator()
        assert v.remove_rule("required_fields")
        assert not v.remove_rule("nonexistent")
        report = v.validate(SecurityManifest())
        # Without required fields rule, missing tool_name won't be flagged as CRITICAL
        assert not any(
            f.category == "structure" and f.severity == ValidationSeverity.CRITICAL
            for f in report.findings
        )

    def test_rule_exception_handled(self):
        class BrokenRule:
            rule_id = "broken"

            def validate(self, manifest):
                raise RuntimeError("boom")

        v = ManifestValidator(rules=[BrokenRule()])
        report = v.validate(_good_manifest())
        assert any("failed" in f.message.lower() for f in report.findings)

    def test_validation_rule_protocol(self):
        assert isinstance(RequiredFieldsRule(), ValidationRule)
        assert isinstance(PermissionConsistencyRule(), ValidationRule)

    def test_rules_property(self):
        v = ManifestValidator()
        assert len(v.rules) == 5  # 5 default rules


# ═══════════════════════════════════════════════════════════════════
# CertificationPipeline
# ═══════════════════════════════════════════════════════════════════


class TestCertificationPipeline:
    def test_certify_good_manifest(self):
        pipeline = CertificationPipeline(crypto_handler=_crypto())
        result = pipeline.certify(_good_manifest())
        assert result.is_certified
        assert result.score >= 0.8
        assert result.attestation.signature != ""
        assert result.attestation.status == AttestationStatus.VALID

    def test_certify_bad_manifest_pending(self):
        pipeline = CertificationPipeline(crypto_handler=_crypto())
        result = pipeline.certify(SecurityManifest())
        assert not result.is_certified
        assert result.attestation.status == AttestationStatus.PENDING

    def test_certify_without_crypto(self):
        pipeline = CertificationPipeline()
        result = pipeline.certify(_good_manifest())
        # Should still be marked valid if level meets min, just unsigned
        assert result.attestation.signature == ""
        assert result.attestation.status == AttestationStatus.VALID

    def test_manifest_digest_binding(self):
        pipeline = CertificationPipeline(crypto_handler=_crypto())
        m = _good_manifest()
        result = pipeline.certify(m)
        assert result.manifest_digest != ""
        assert result.attestation.manifest_digest == result.manifest_digest

    def test_requested_level_cap(self):
        pipeline = CertificationPipeline(crypto_handler=_crypto())
        # Request a level lower than what the tool qualifies for
        result = pipeline.certify(
            _good_manifest(),
            requested_level=CertificationLevel.BASIC,
        )
        assert result.certification_level == CertificationLevel.BASIC

    def test_requested_level_too_high(self):
        pipeline = CertificationPipeline(crypto_handler=_crypto())
        # Request STRICT but the tool may not qualify
        m = _good_manifest(
            permissions={
                PermissionScope.READ_RESOURCE,
                PermissionScope.SUBPROCESS_EXEC,
            },
        )
        result = pipeline.certify(m, requested_level=CertificationLevel.STRICT)
        # Should be capped at what the tool actually qualifies for
        level_order = list(CertificationLevel)
        assert level_order.index(result.certification_level) <= level_order.index(
            CertificationLevel.BASIC
        )

    def test_attestation_stored(self):
        pipeline = CertificationPipeline(crypto_handler=_crypto())
        result = pipeline.certify(_good_manifest())
        found = pipeline.get_attestation(result.attestation.attestation_id)
        assert found is not None
        assert found.tool_name == "test-tool"

    def test_report_stored(self):
        pipeline = CertificationPipeline(crypto_handler=_crypto())
        result = pipeline.certify(_good_manifest())
        found = pipeline.get_report(result.report.report_id)
        assert found is not None

    def test_find_attestations_by_tool(self):
        pipeline = CertificationPipeline(crypto_handler=_crypto())
        pipeline.certify(_good_manifest(tool_name="tool-a"))
        pipeline.certify(_good_manifest(tool_name="tool-b"))
        results = pipeline.find_attestations(tool_name="tool-a")
        assert len(results) == 1
        assert results[0].tool_name == "tool-a"

    def test_find_attestations_by_author(self):
        pipeline = CertificationPipeline(crypto_handler=_crypto())
        pipeline.certify(_good_manifest(author="acme"))
        pipeline.certify(_good_manifest(author="other"))
        results = pipeline.find_attestations(author="acme")
        assert len(results) == 1

    def test_find_attestations_valid_only(self):
        pipeline = CertificationPipeline(crypto_handler=_crypto())
        pipeline.certify(_good_manifest(tool_name="good"))
        pipeline.certify(SecurityManifest(tool_name="bad"))  # will be PENDING
        results = pipeline.find_attestations(valid_only=True)
        assert all(a.is_valid() for a in results)

    def test_find_attestations_by_min_level(self):
        pipeline = CertificationPipeline(crypto_handler=_crypto())
        pipeline.certify(_good_manifest())
        results = pipeline.find_attestations(min_level=CertificationLevel.STANDARD)
        for a in results:
            level_order = list(CertificationLevel)
            assert level_order.index(a.certification_level) >= level_order.index(
                CertificationLevel.STANDARD
            )


# ═══════════════════════════════════════════════════════════════════
# Attestation Verification
# ═══════════════════════════════════════════════════════════════════


class TestAttestationVerification:
    def test_verify_valid_attestation(self):
        pipeline = CertificationPipeline(crypto_handler=_crypto())
        result = pipeline.certify(_good_manifest())
        verification = pipeline.verify_attestation(result.attestation)
        assert verification.valid
        assert verification.signature_valid

    def test_verify_with_matching_manifest(self):
        pipeline = CertificationPipeline(crypto_handler=_crypto())
        m = _good_manifest()
        result = pipeline.certify(m)
        verification = pipeline.verify_attestation(result.attestation, manifest=m)
        assert verification.valid
        assert verification.manifest_match

    def test_verify_with_wrong_manifest(self):
        pipeline = CertificationPipeline(crypto_handler=_crypto())
        m = _good_manifest()
        result = pipeline.certify(m)
        # Verify against a different manifest
        other = _good_manifest(tool_name="different-tool")
        verification = pipeline.verify_attestation(result.attestation, manifest=other)
        assert not verification.valid
        assert not verification.manifest_match
        assert any("digest mismatch" in issue for issue in verification.issues)

    def test_verify_tampered_signature(self):
        pipeline = CertificationPipeline(crypto_handler=_crypto())
        result = pipeline.certify(_good_manifest())
        result.attestation.signature = "tampered"
        verification = pipeline.verify_attestation(result.attestation)
        assert not verification.valid
        assert not verification.signature_valid

    def test_verify_revoked_attestation(self):
        pipeline = CertificationPipeline(crypto_handler=_crypto())
        result = pipeline.certify(_good_manifest())
        pipeline.revoke(result.attestation.attestation_id, reason="compromised")
        verification = pipeline.verify_attestation(result.attestation)
        assert not verification.valid
        assert any("revoked" in issue for issue in verification.issues)

    def test_verify_unsigned_attestation(self):
        pipeline = CertificationPipeline()  # no crypto
        result = pipeline.certify(_good_manifest())
        verification = pipeline.verify_attestation(result.attestation)
        assert any("unsigned" in issue.lower() for issue in verification.issues)

    def test_verify_no_crypto_handler(self):
        # Pipeline without crypto verifying a signed attestation
        sign_pipeline = CertificationPipeline(crypto_handler=_crypto())
        result = sign_pipeline.certify(_good_manifest())

        verify_pipeline = CertificationPipeline()  # no crypto
        verification = verify_pipeline.verify_attestation(result.attestation)
        assert any("no crypto" in issue.lower() for issue in verification.issues)


# ═══════════════════════════════════════════════════════════════════
# Revocation
# ═══════════════════════════════════════════════════════════════════


class TestRevocation:
    def test_revoke_existing(self):
        pipeline = CertificationPipeline(crypto_handler=_crypto())
        result = pipeline.certify(_good_manifest())
        assert pipeline.revoke(result.attestation.attestation_id, reason="test")
        assert result.attestation.status == AttestationStatus.REVOKED
        assert result.attestation.metadata["revocation_reason"] == "test"

    def test_revoke_nonexistent(self):
        pipeline = CertificationPipeline()
        assert not pipeline.revoke("nonexistent-id")

    def test_revoked_attestation_invalid(self):
        pipeline = CertificationPipeline(crypto_handler=_crypto())
        result = pipeline.certify(_good_manifest())
        pipeline.revoke(result.attestation.attestation_id)
        assert not result.attestation.is_valid()


# ═══════════════════════════════════════════════════════════════════
# Event Bus Integration
# ═══════════════════════════════════════════════════════════════════


class TestEventBusIntegration:
    def test_certification_emits_event(self):
        from fastmcp.server.security.alerts.bus import SecurityEventBus
        from fastmcp.server.security.alerts.handlers import BufferedHandler

        bus = SecurityEventBus()
        handler = BufferedHandler()
        bus.subscribe(handler)

        pipeline = CertificationPipeline(
            crypto_handler=_crypto(),
            event_bus=bus,
        )
        pipeline.certify(_good_manifest())
        assert len(handler.events) == 1
        assert "certified" in handler.events[0].message.lower()

    def test_revocation_emits_event(self):
        from fastmcp.server.security.alerts.bus import SecurityEventBus
        from fastmcp.server.security.alerts.handlers import BufferedHandler

        bus = SecurityEventBus()
        handler = BufferedHandler()
        bus.subscribe(handler)

        pipeline = CertificationPipeline(
            crypto_handler=_crypto(),
            event_bus=bus,
        )
        result = pipeline.certify(_good_manifest())
        pipeline.revoke(result.attestation.attestation_id, reason="test")
        assert len(handler.events) == 2
        assert "revoked" in handler.events[1].message.lower()


# ═══════════════════════════════════════════════════════════════════
# Marketplace Integration
# ═══════════════════════════════════════════════════════════════════


class TestMarketplaceIntegration:
    def test_certification_updates_marketplace_trust(self):
        from fastmcp.server.security.gateway.marketplace import Marketplace
        from fastmcp.server.security.gateway.models import TrustLevel

        marketplace = Marketplace()
        reg = marketplace.register(
            name="test-tool",
            endpoint="http://localhost",
            tags={"test-tool"},
        )
        assert reg.trust_level == TrustLevel.UNVERIFIED

        pipeline = CertificationPipeline(
            crypto_handler=_crypto(),
            marketplace=marketplace,
        )
        pipeline.certify(_good_manifest())

        updated = marketplace.get(reg.server_id)
        assert updated is not None
        assert updated.trust_level != TrustLevel.UNVERIFIED


# ═══════════════════════════════════════════════════════════════════
# Config Integration
# ═══════════════════════════════════════════════════════════════════


class TestConfigIntegration:
    def test_certification_config_creates_pipeline(self):
        from fastmcp.server.security.config import CertificationConfig

        config = CertificationConfig(issuer_id="test-ca")
        pipeline = config.get_pipeline()
        assert pipeline._issuer_id == "test-ca"

    def test_certification_config_with_crypto(self):
        from fastmcp.server.security.config import CertificationConfig

        config = CertificationConfig(crypto_handler=_crypto())
        pipeline = config.get_pipeline()
        result = pipeline.certify(_good_manifest())
        assert result.attestation.signature != ""

    def test_certification_config_in_security_config(self):
        from fastmcp.server.security.config import CertificationConfig, SecurityConfig

        config = SecurityConfig(
            certification=CertificationConfig(issuer_id="my-ca"),
        )
        assert config.is_certification_enabled()

    def test_security_config_no_certification(self):
        from fastmcp.server.security.config import SecurityConfig

        config = SecurityConfig()
        assert not config.is_certification_enabled()

    def test_certification_config_pre_built_pipeline(self):
        from fastmcp.server.security.config import CertificationConfig

        custom_pipeline = CertificationPipeline(issuer_id="custom")
        config = CertificationConfig(pipeline=custom_pipeline)
        assert config.get_pipeline() is custom_pipeline


# ═══════════════════════════════════════════════════════════════════
# Import Tests
# ═══════════════════════════════════════════════════════════════════


class TestImports:
    def test_import_from_certification_package(self):
        pass

    def test_import_from_security_package(self):
        pass
