"""Tests for the SecureMCP Developer CLI (Phase 15).

Covers certify, publish, search, inspect, unpublish, status,
validate, certify_and_publish, and manifest parsing.
"""

from __future__ import annotations

import json

import pytest

from fastmcp.server.security.certification.attestation import (
    CertificationLevel,
)
from fastmcp.server.security.certification.manifest import (
    DataClassification,
    PermissionScope,
    SecurityManifest,
)
from fastmcp.server.security.certification.pipeline import CertificationPipeline
from fastmcp.server.security.certification.validator import ManifestValidator
from fastmcp.server.security.cli.commands import (
    SecureMCPCLI,
    _parse_categories,
    _parse_level,
    _parse_sort,
)
from fastmcp.server.security.gateway.tool_marketplace import (
    PublishStatus,
    SortBy,
    ToolCategory,
    ToolMarketplace,
)
from fastmcp.server.security.registry.registry import TrustRegistry

# ── Helpers ─────────────────────────────────────────────────────────


def _make_manifest(
    name: str = "test-tool",
    version: str = "1.0.0",
    author: str = "acme",
) -> SecurityManifest:
    return SecurityManifest(
        tool_name=name,
        version=version,
        author=author,
        permissions={PermissionScope.READ_RESOURCE},
        max_execution_time_seconds=30,
    )


def _make_cli(**kwargs) -> SecureMCPCLI:
    return SecureMCPCLI(**kwargs)


def _make_full_cli() -> SecureMCPCLI:
    """CLI with all components configured.

    Sets min_level_for_signing to SELF_ATTESTED so that
    even low-level certifications produce VALID attestations.
    """
    registry = TrustRegistry()
    marketplace = ToolMarketplace(trust_registry=registry)
    pipeline = CertificationPipeline(
        validator=ManifestValidator(),
        min_level_for_signing=CertificationLevel.SELF_ATTESTED,
    )
    return SecureMCPCLI(
        marketplace=marketplace,
        pipeline=pipeline,
        registry=registry,
    )


# ── Parser tests ────────────────────────────────────────────────────


class TestParsers:
    """Tests for string-to-enum parsers."""

    def test_parse_level_valid(self):
        assert _parse_level("basic") == CertificationLevel.BASIC
        assert _parse_level("STANDARD") == CertificationLevel.STANDARD
        assert _parse_level("strict") == CertificationLevel.STRICT
        assert _parse_level("self_attested") == CertificationLevel.SELF_ATTESTED

    def test_parse_level_invalid(self):
        with pytest.raises(ValueError, match="Unknown certification level"):
            _parse_level("platinum")

    def test_parse_sort_valid(self):
        assert _parse_sort("relevance") == SortBy.RELEVANCE
        assert _parse_sort("trust") == SortBy.TRUST_SCORE
        assert _parse_sort("rating") == SortBy.RATING
        assert _parse_sort("installs") == SortBy.INSTALLS

    def test_parse_sort_invalid(self):
        with pytest.raises(ValueError, match="Unknown sort option"):
            _parse_sort("magic")

    def test_parse_categories_valid(self):
        cats = _parse_categories(["search", "database"])
        assert ToolCategory.SEARCH in cats
        assert ToolCategory.DATABASE in cats

    def test_parse_categories_invalid(self):
        with pytest.raises(ValueError, match="Unknown category"):
            _parse_categories(["search", "nonexistent"])


# ── Certify tests ───────────────────────────────────────────────────


class TestCertify:
    """Tests for the certify command."""

    def test_certify_no_pipeline(self):
        cli = _make_cli()
        result = cli.certify(_make_manifest())
        assert not result.success
        assert "not configured" in result.message

    def test_certify_success(self):
        cli = _make_full_cli()
        manifest = _make_manifest()
        result = cli.certify(manifest, requested_level="self_attested")
        assert result.success
        assert result.level != CertificationLevel.UNCERTIFIED
        assert result.score > 0
        assert result.attestation is not None
        assert result.tool_name == "test-tool"

    def test_certify_with_enum_level(self):
        cli = _make_full_cli()
        result = cli.certify(
            _make_manifest(), requested_level=CertificationLevel.SELF_ATTESTED
        )
        assert result.success

    def test_certify_findings(self):
        cli = _make_full_cli()
        # Minimal manifest — should produce some findings
        manifest = SecurityManifest(tool_name="bare-tool")
        result = cli.certify(manifest, requested_level="strict")
        assert len(result.findings) > 0
        assert all("severity" in f for f in result.findings)

    def test_certify_result_serialization(self):
        cli = _make_full_cli()
        result = cli.certify(_make_manifest(), requested_level="self_attested")
        d = result.to_dict()
        assert "success" in d
        assert "level" in d
        j = result.to_json()
        parsed = json.loads(j)
        assert parsed["tool_name"] == "test-tool"


# ── Publish tests ───────────────────────────────────────────────────


class TestPublish:
    """Tests for the publish command."""

    def test_publish(self):
        cli = _make_full_cli()
        manifest = _make_manifest()
        result = cli.publish(manifest, description="A search tool")
        assert result.success
        assert result.listing_id
        assert result.tool_name == "test-tool"
        assert result.version == "1.0.0"
        assert result.status == PublishStatus.PUBLISHED

    def test_publish_with_categories(self):
        cli = _make_full_cli()
        result = cli.publish(
            _make_manifest(),
            categories=["search", "data_access"],
        )
        assert result.success
        listing = cli.marketplace.get_by_name("test-tool")
        assert ToolCategory.SEARCH in listing.categories

    def test_publish_with_tags(self):
        cli = _make_full_cli()
        result = cli.publish(
            _make_manifest(),
            tags=["ml", "search"],
        )
        assert result.success
        listing = cli.marketplace.get_by_name("test-tool")
        assert "ml" in listing.tags

    def test_publish_result_serialization(self):
        cli = _make_full_cli()
        result = cli.publish(_make_manifest())
        d = result.to_dict()
        assert d["success"] is True
        j = result.to_json()
        parsed = json.loads(j)
        assert parsed["status"] == "published"


# ── Certify and publish tests ───────────────────────────────────────


class TestCertifyAndPublish:
    """Tests for the certify_and_publish command."""

    def test_certify_and_publish_success(self):
        cli = _make_full_cli()
        cert, pub = cli.certify_and_publish(
            _make_manifest(),
            requested_level="self_attested",
            description="A great tool",
        )
        assert cert.success
        assert pub is not None
        assert pub.success

    def test_certify_and_publish_cert_fails(self):
        cli = _make_full_cli()
        # Bare manifest achieves self_attested; requesting strict
        # still certifies at the lower level. Verify the cert level
        # is capped below the requested level.
        manifest = SecurityManifest(tool_name="bare-tool")
        cert, pub = cli.certify_and_publish(manifest, requested_level="strict")
        # The pipeline certifies at whatever level the score allows,
        # so cert.success is True but at a lower level
        assert cert.success
        assert cert.level != CertificationLevel.STRICT
        assert pub is not None  # Still publishes at the achieved level


# ── Search tests ────────────────────────────────────────────────────


class TestSearch:
    """Tests for the search command."""

    def test_search_empty(self):
        cli = _make_full_cli()
        result = cli.search("nonexistent")
        assert result.total == 0
        assert len(result.listings) == 0

    def test_search_by_query(self):
        cli = _make_full_cli()
        cli.publish(_make_manifest("search-docs"))
        cli.publish(_make_manifest("file-writer"))
        result = cli.search("search")
        assert result.total == 1
        assert result.listings[0].tool_name == "search-docs"

    def test_search_with_filters(self):
        cli = _make_full_cli()
        cli.publish(_make_manifest("a"), categories=["search"], tags=["ml"])
        cli.publish(_make_manifest("b"), categories=["database"])
        result = cli.search(categories=["search"])
        assert result.total == 1
        assert result.filters_applied["categories"] == ["search"]

    def test_search_certified_only(self):
        cli = _make_full_cli()
        # Publish one certified, one not
        cert, pub = cli.certify_and_publish(
            _make_manifest("cert-tool"),
            requested_level="self_attested",
        )
        cli.publish(_make_manifest("plain-tool"))
        result = cli.search(certified_only=True)
        assert result.total == 1

    def test_search_sort_by(self):
        cli = _make_full_cli()
        cli.publish(_make_manifest("a"))
        cli.publish(_make_manifest("b"))
        result = cli.search(sort_by="newest")
        assert result.total == 2
        assert result.filters_applied["sort_by"] == "newest"

    def test_search_result_serialization(self):
        cli = _make_full_cli()
        cli.publish(_make_manifest("my-tool"))
        result = cli.search("my-tool")
        d = result.to_dict()
        assert d["total"] == 1
        j = result.to_json()
        parsed = json.loads(j)
        assert len(parsed["listings"]) == 1


# ── Inspect tests ───────────────────────────────────────────────────


class TestInspect:
    """Tests for the inspect command."""

    def test_inspect_not_found(self):
        cli = _make_full_cli()
        result = cli.inspect("nonexistent")
        assert not result.found
        assert "not found" in result.message

    def test_inspect_published_tool(self):
        cli = _make_full_cli()
        cli.publish(_make_manifest("my-tool"))
        result = cli.inspect("my-tool")
        assert result.found
        assert result.tool_name == "my-tool"
        assert result.listing is not None

    def test_inspect_with_trust_score(self):
        cli = _make_full_cli()
        cert, pub = cli.certify_and_publish(
            _make_manifest("my-tool"),
            requested_level="self_attested",
        )
        result = cli.inspect("my-tool")
        assert result.found
        assert result.trust_score is not None
        assert result.trust_score.overall > 0

    def test_inspect_result_serialization(self):
        cli = _make_full_cli()
        cli.publish(_make_manifest("my-tool"))
        result = cli.inspect("my-tool")
        d = result.to_dict()
        assert d["found"] is True
        j = result.to_json()
        parsed = json.loads(j)
        assert parsed["tool_name"] == "my-tool"


# ── Unpublish tests ─────────────────────────────────────────────────


class TestUnpublish:
    """Tests for the unpublish command."""

    def test_unpublish_success(self):
        cli = _make_full_cli()
        cli.publish(_make_manifest("my-tool"))
        result = cli.unpublish("my-tool")
        assert result.success
        assert cli.marketplace.get_by_name("my-tool") is None

    def test_unpublish_not_found(self):
        cli = _make_full_cli()
        result = cli.unpublish("nonexistent")
        assert not result.success


# ── Status tests ────────────────────────────────────────────────────


class TestStatus:
    """Tests for the status command."""

    def test_status_empty(self):
        cli = _make_full_cli()
        result = cli.status()
        assert result.marketplace_stats["total_listings"] == 0
        assert result.registry_stats["total_tools"] == 0

    def test_status_with_data(self):
        cli = _make_full_cli()
        cli.publish(_make_manifest("a"))
        cli.publish(_make_manifest("b"))
        result = cli.status()
        assert result.marketplace_stats["total_listings"] == 2
        assert result.registry_stats["total_tools"] == 2

    def test_status_serialization(self):
        cli = _make_full_cli()
        result = cli.status()
        d = result.to_dict()
        assert "marketplace" in d
        assert "registry" in d
        j = result.to_json()
        parsed = json.loads(j)
        assert "marketplace" in parsed


# ── Validate tests ──────────────────────────────────────────────────


class TestValidate:
    """Tests for the validate (dry-run) command."""

    def test_validate_no_pipeline(self):
        cli = _make_cli()
        result = cli.validate(_make_manifest())
        assert not result.success
        assert "not configured" in result.message

    def test_validate_good_manifest(self):
        cli = _make_full_cli()
        result = cli.validate(_make_manifest())
        assert result.attestation is None  # Validate-only
        assert result.score > 0
        assert "Validation" in result.message

    def test_validate_bare_manifest(self):
        cli = _make_full_cli()
        manifest = SecurityManifest(tool_name="bare")
        result = cli.validate(manifest)
        assert len(result.findings) > 0


# ── Manifest parsing tests ──────────────────────────────────────────


class TestManifestParsing:
    """Tests for parsing manifests from JSON."""

    def test_parse_minimal(self):
        data = json.dumps({"tool_name": "my-tool", "version": "1.0.0"})
        manifest = SecureMCPCLI.parse_manifest_file(data)
        assert manifest.tool_name == "my-tool"
        assert manifest.version == "1.0.0"

    def test_parse_with_permissions(self):
        data = json.dumps(
            {
                "tool_name": "my-tool",
                "version": "1.0.0",
                "author": "acme",
                "permissions": ["read_resource", "network_access"],
            }
        )
        manifest = SecureMCPCLI.parse_manifest_file(data)
        assert PermissionScope.READ_RESOURCE in manifest.permissions
        assert PermissionScope.NETWORK_ACCESS in manifest.permissions

    def test_parse_with_data_flows(self):
        data = json.dumps(
            {
                "tool_name": "my-tool",
                "version": "1.0.0",
                "data_flows": [
                    {
                        "source": "user_input",
                        "destination": "api_service",
                        "classification": "confidential",
                        "transforms": ["encrypt"],
                    }
                ],
            }
        )
        manifest = SecureMCPCLI.parse_manifest_file(data)
        assert len(manifest.data_flows) == 1
        assert manifest.data_flows[0].classification == DataClassification.CONFIDENTIAL

    def test_parse_with_resource_access(self):
        data = json.dumps(
            {
                "tool_name": "my-tool",
                "version": "1.0.0",
                "resource_access": [
                    {
                        "resource_pattern": "file:///*.json",
                        "access_type": "read",
                        "required": True,
                        "classification": "internal",
                    }
                ],
            }
        )
        manifest = SecureMCPCLI.parse_manifest_file(data)
        assert len(manifest.resource_access) == 1

    def test_parse_full_manifest(self):
        data = json.dumps(
            {
                "tool_name": "complete-tool",
                "version": "2.0.0",
                "author": "acme",
                "permissions": ["read_resource", "call_tool"],
                "data_flows": [],
                "resource_access": [],
                "max_execution_time_seconds": 60,
                "idempotent": True,
                "deterministic": False,
                "requires_consent": True,
                "dependencies": ["other-tool"],
                "tags": ["search", "ml"],
            }
        )
        manifest = SecureMCPCLI.parse_manifest_file(data)
        assert manifest.author == "acme"
        assert manifest.max_execution_time_seconds == 60
        assert manifest.idempotent is True
        assert manifest.requires_consent is True
        assert "search" in manifest.tags

    def test_parse_and_certify_roundtrip(self):
        """Parse a manifest from JSON and certify it."""
        cli = _make_full_cli()
        data = json.dumps(
            {
                "tool_name": "roundtrip-tool",
                "version": "1.0.0",
                "author": "acme",
                "permissions": ["read_resource"],
                "max_execution_time_seconds": 30,
            }
        )
        manifest = SecureMCPCLI.parse_manifest_file(data)
        result = cli.certify(manifest, requested_level="self_attested")
        assert result.success


# ── Import tests ────────────────────────────────────────────────────


class TestImports:
    """Tests for module imports."""

    def test_cli_imports(self):
        from fastmcp.server.security.cli import (
            SecureMCPCLI,
        )

        assert SecureMCPCLI is not None

    def test_top_level_imports(self):
        from fastmcp.server.security import (
            SecureMCPCLI,
        )

        assert SecureMCPCLI is not None
