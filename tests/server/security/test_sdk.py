"""Tests for SDK & Integration Layer (Phase 19).

Covers the SecureMCPClient facade, security checks, provenance recording,
trust queries, tool profiles, compliance integration, decorators, and history.
"""

from __future__ import annotations

from fastmcp.server.security.alerts.bus import SecurityEventBus
from fastmcp.server.security.alerts.handlers import BufferedHandler
from fastmcp.server.security.certification.manifest import (
    PermissionScope,
    SecurityManifest,
)
from fastmcp.server.security.compliance.frameworks import ComplianceFramework
from fastmcp.server.security.compliance.reports import (
    ComplianceReporter,
)
from fastmcp.server.security.federation.crl import CertificateRevocationList
from fastmcp.server.security.federation.federation import TrustFederation
from fastmcp.server.security.gateway.tool_marketplace import (
    ToolMarketplace,
)
from fastmcp.server.security.registry.registry import TrustRegistry
from fastmcp.server.security.sandbox.enforcer import SandboxedRunner
from fastmcp.server.security.sdk.client import (
    SecureMCPClient,
    SecurityCheckResult,
    ToolSecurityProfile,
)
from fastmcp.server.security.sdk.decorators import (
    SecurityDecorator,
    SecurityDecoratorConfig,
)

# ── Helpers ─────────────────────────────────────────────────────


def _make_manifest(tool_name: str = "test-tool", **kwargs) -> SecurityManifest:
    defaults = {
        "tool_name": tool_name,
        "version": "1.0.0",
        "author": "acme",
    }
    defaults.update(kwargs)
    return SecurityManifest(**defaults)


def _make_client(**kwargs) -> SecureMCPClient:
    return SecureMCPClient(**kwargs)


# ── SecurityCheckResult tests ────────────────────────────────────


class TestSecurityCheckResult:
    def test_default(self):
        r = SecurityCheckResult()
        assert r.check_id.startswith("chk-")
        assert r.allowed is True

    def test_to_dict(self):
        r = SecurityCheckResult(tool_name="test", allowed=False, reasons=["revoked"])
        d = r.to_dict()
        assert d["tool_name"] == "test"
        assert d["allowed"] is False
        assert "revoked" in d["reasons"]


# ── ToolSecurityProfile tests ────────────────────────────────────


class TestToolSecurityProfile:
    def test_default(self):
        p = ToolSecurityProfile()
        assert p.trust_score == 0.0
        assert not p.is_certified

    def test_to_dict(self):
        p = ToolSecurityProfile(tool_name="test", trust_score=0.9, is_certified=True)
        d = p.to_dict()
        assert d["tool_name"] == "test"
        assert d["trust_score"] == 0.9


# ── SecureMCPClient basic tests ──────────────────────────────────


class TestSecureMCPClientBasic:
    def test_empty_client(self):
        client = _make_client()
        assert client.check_count == 0
        assert client.registry is None
        assert client.marketplace is None
        assert client.federation is None

    def test_statistics(self):
        registry = TrustRegistry()
        client = _make_client(registry=registry)
        stats = client.get_statistics()
        assert stats["components_configured"] == 1
        assert "registry" in stats["components"]

    def test_statistics_all_components(self):
        client = _make_client(
            registry=TrustRegistry(),
            marketplace=ToolMarketplace(),
            federation=TrustFederation(),
            sandbox_runner=SandboxedRunner(),
            event_bus=SecurityEventBus(),
            crl=CertificateRevocationList(),
        )
        stats = client.get_statistics()
        assert stats["components_configured"] == 6


# ── Security check tests ────────────────────────────────────────


class TestSecurityChecks:
    def test_check_unknown_tool(self):
        client = _make_client()
        result = client.check_tool("unknown-tool")
        assert result.allowed is True
        assert result.trust_score == 0.0

    def test_check_revoked_tool_crl(self):
        crl = CertificateRevocationList()
        crl.revoke("bad-tool")
        client = _make_client(crl=crl)
        result = client.check_tool("bad-tool")
        assert not result.allowed
        assert result.is_revoked

    def test_check_revoked_tool_federation(self):
        fed = TrustFederation()
        fed.local_crl.revoke("bad-tool")
        client = _make_client(federation=fed)
        result = client.check_tool("bad-tool")
        assert not result.allowed
        assert result.is_revoked

    def test_check_trust_score_threshold(self):
        registry = TrustRegistry()
        registry.register("low-trust", attestation=None)
        # Default score is 0.5
        client = _make_client(registry=registry)
        result = client.check_tool("low-trust", min_trust_score=0.9)
        assert not result.allowed
        assert len(result.reasons) > 0

    def test_check_trust_score_passes(self):
        registry = TrustRegistry()
        registry.register("good-tool", attestation=None)
        client = _make_client(registry=registry)
        result = client.check_tool("good-tool", min_trust_score=0.1)
        assert result.allowed
        assert result.trust_score > 0

    def test_check_with_sandbox(self):
        runner = SandboxedRunner()
        client = _make_client(sandbox_runner=runner)
        manifest = _make_manifest()
        result = client.check_tool("test-tool", manifest=manifest)
        assert result.allowed
        assert result.sandbox_context is not None

    def test_check_sandbox_blocked_by_crl(self):
        crl = CertificateRevocationList()
        crl.revoke("test-tool")
        runner = SandboxedRunner(crl=crl)
        client = _make_client(sandbox_runner=runner, crl=crl)
        manifest = _make_manifest()
        result = client.check_tool("test-tool", manifest=manifest)
        assert not result.allowed

    def test_check_history(self):
        client = _make_client()
        client.check_tool("tool-a")
        client.check_tool("tool-b")
        assert client.check_count == 2
        history = client.get_check_history()
        assert len(history) == 2

    def test_check_emits_event(self):
        bus = SecurityEventBus()
        handler = BufferedHandler(max_size=50)
        bus.subscribe(handler)
        client = _make_client(event_bus=bus)
        client.check_tool("test-tool")
        assert len(handler.events) == 1
        assert handler.events[0].layer == "sdk"


# ── Trust query tests ────────────────────────────────────────────


class TestTrustQueries:
    def test_get_trust_score(self):
        registry = TrustRegistry()
        registry.register("my-tool", attestation=None)
        client = _make_client(registry=registry)
        assert client.get_trust_score("my-tool") > 0

    def test_get_trust_score_missing(self):
        client = _make_client()
        assert client.get_trust_score("unknown") == 0.0

    def test_is_tool_revoked(self):
        crl = CertificateRevocationList()
        crl.revoke("bad-tool")
        client = _make_client(crl=crl)
        assert client.is_tool_revoked("bad-tool")
        assert not client.is_tool_revoked("good-tool")

    def test_is_tool_revoked_federation(self):
        fed = TrustFederation()
        fed.local_crl.revoke("bad-tool")
        client = _make_client(federation=fed)
        assert client.is_tool_revoked("bad-tool")

    def test_is_tool_certified_registry(self):
        registry = TrustRegistry()
        registry.register("my-tool", attestation=None)
        client = _make_client(registry=registry)
        # Default registration isn't certified
        assert not client.is_tool_certified("my-tool")

    def test_is_tool_certified_no_components(self):
        client = _make_client()
        assert not client.is_tool_certified("anything")


# ── Tool profile tests ───────────────────────────────────────────


class TestToolProfile:
    def test_empty_profile(self):
        client = _make_client()
        profile = client.get_tool_profile("unknown")
        assert profile.tool_name == "unknown"
        assert profile.trust_score == 0.0

    def test_profile_with_registry(self):
        registry = TrustRegistry()
        registry.register("my-tool", attestation=None)
        client = _make_client(registry=registry)
        profile = client.get_tool_profile("my-tool")
        assert profile.trust_score > 0

    def test_profile_with_marketplace(self):
        mp = ToolMarketplace()
        mp.publish("pub-tool", display_name="Published Tool")
        client = _make_client(marketplace=mp)
        profile = client.get_tool_profile("pub-tool")
        assert profile.is_published

    def test_profile_revoked(self):
        crl = CertificateRevocationList()
        crl.revoke("bad-tool")
        client = _make_client(crl=crl)
        profile = client.get_tool_profile("bad-tool")
        assert profile.is_revoked


# ── Compliance integration tests ─────────────────────────────────


class TestComplianceIntegration:
    def test_run_compliance_report(self):
        fw = ComplianceFramework(name="Test")
        reporter = ComplianceReporter(framework=fw)
        client = _make_client(compliance_reporter=reporter)
        report = client.run_compliance_report()
        assert report is not None

    def test_run_compliance_no_reporter(self):
        client = _make_client()
        assert client.run_compliance_report() is None


# ── Provenance recording tests ───────────────────────────────────


class TestProvenanceRecording:
    def test_record_action_no_provenance(self):
        client = _make_client()
        assert client.record_action("tool", "execute") is None


# ── SecurityDecoratorConfig tests ────────────────────────────────


class TestSecurityDecoratorConfig:
    def test_default(self):
        cfg = SecurityDecoratorConfig()
        assert cfg.config_id.startswith("sdcfg-")
        assert cfg.min_trust_score == 0.0
        assert cfg.record_provenance is True

    def test_with_permissions(self):
        cfg = SecurityDecoratorConfig(
            required_permissions={
                PermissionScope.NETWORK_ACCESS,
                PermissionScope.FILE_SYSTEM_READ,
            }
        )
        assert len(cfg.required_permissions) == 2

    def test_to_dict(self):
        cfg = SecurityDecoratorConfig(
            tool_name="test",
            sandbox_enabled=True,
            max_execution_seconds=60.0,
        )
        d = cfg.to_dict()
        assert d["tool_name"] == "test"
        assert d["sandbox_enabled"] is True
        assert d["max_execution_seconds"] == 60.0


# ── SecurityDecorator tests ──────────────────────────────────────


class TestSecurityDecorator:
    def test_default(self):
        dec = SecurityDecorator()
        assert dec.decorator_id.startswith("sd-")
        assert dec.tool_count == 0

    def test_register(self):
        dec = SecurityDecorator()
        cfg = dec.register("my-tool", min_trust_score=0.5)
        assert cfg.tool_name == "my-tool"
        assert cfg.min_trust_score == 0.5
        assert dec.tool_count == 1

    def test_register_with_config(self):
        dec = SecurityDecorator()
        config = SecurityDecoratorConfig(require_certification=True)
        cfg = dec.register("tool-a", config=config)
        assert cfg.tool_name == "tool-a"
        assert cfg.require_certification

    def test_unregister(self):
        dec = SecurityDecorator()
        dec.register("tool-a")
        assert dec.unregister("tool-a")
        assert dec.tool_count == 0
        assert not dec.unregister("nonexistent")

    def test_get_config(self):
        dec = SecurityDecorator()
        dec.register("tool-a", sandbox_enabled=True)
        cfg = dec.get_config("tool-a")
        assert cfg is not None
        assert cfg.sandbox_enabled
        assert dec.get_config("unknown") is None

    def test_get_tools_requiring_certification(self):
        dec = SecurityDecorator()
        dec.register("tool-a", require_certification=True)
        dec.register("tool-b", require_certification=False)
        assert dec.get_tools_requiring_certification() == ["tool-a"]

    def test_get_tools_with_sandbox(self):
        dec = SecurityDecorator()
        dec.register("tool-a", sandbox_enabled=True)
        dec.register("tool-b", sandbox_enabled=False)
        assert dec.get_tools_with_sandbox() == ["tool-a"]

    def test_to_dict(self):
        dec = SecurityDecorator()
        dec.register("tool-a")
        d = dec.to_dict()
        assert d["tool_count"] == 1
        assert "tool-a" in d["tools"]


# ── Import tests ─────────────────────────────────────────────────


class TestImports:
    def test_sdk_imports(self):
        from fastmcp.server.security.sdk import (
            SecureMCPClient,
        )

        assert SecureMCPClient is not None

    def test_top_level_imports(self):
        from fastmcp.server.security import (
            SecureMCPClient,
        )

        assert SecureMCPClient is not None
