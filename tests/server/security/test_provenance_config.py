"""Tests for ProvenanceConfig and server integration."""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.security import attach_security, get_security_context
from fastmcp.server.security.config import ProvenanceConfig, SecurityConfig
from fastmcp.server.security.middleware.provenance_recording import (
    ProvenanceRecordingMiddleware,
)
from fastmcp.server.security.provenance.ledger import ProvenanceLedger


class TestProvenanceConfig:
    def test_default_config(self):
        config = ProvenanceConfig()
        assert config.ledger is None
        assert config.ledger_id == "default"
        assert config.record_list_operations is False

    def test_get_ledger_creates_new(self):
        config = ProvenanceConfig(ledger_id="test-ledger")
        ledger = config.get_ledger()
        assert isinstance(ledger, ProvenanceLedger)
        assert ledger.ledger_id == "test-ledger"

    def test_get_ledger_uses_existing(self):
        existing = ProvenanceLedger(ledger_id="existing")
        config = ProvenanceConfig(ledger=existing)
        assert config.get_ledger() is existing


class TestSecurityConfigProvenance:
    def test_provenance_disabled_by_default(self):
        config = SecurityConfig()
        assert not config.is_provenance_enabled()

    def test_provenance_enabled_when_configured(self):
        config = SecurityConfig(provenance=ProvenanceConfig())
        assert config.is_provenance_enabled()

    def test_provenance_disabled_when_master_off(self):
        config = SecurityConfig(
            provenance=ProvenanceConfig(),
            enabled=False,
        )
        assert not config.is_provenance_enabled()


class TestServerProvenanceIntegration:
    def test_server_with_provenance(self):
        config = SecurityConfig(provenance=ProvenanceConfig())
        mcp = FastMCP("test")

        attach_security(mcp, config)

        assert any(isinstance(m, ProvenanceRecordingMiddleware) for m in mcp.middleware)

    def test_server_without_provenance(self):
        mcp = FastMCP("test")
        assert not any(
            isinstance(m, ProvenanceRecordingMiddleware) for m in mcp.middleware
        )

    def test_server_provenance_disabled_no_middleware(self):
        config = SecurityConfig(
            provenance=ProvenanceConfig(),
            enabled=False,
        )
        mcp = FastMCP("test")

        attach_security(mcp, config)

        assert not any(
            isinstance(m, ProvenanceRecordingMiddleware) for m in mcp.middleware
        )

    def test_server_stores_ledger_reference(self):
        config = SecurityConfig(provenance=ProvenanceConfig())
        mcp = FastMCP("test")

        ctx = attach_security(mcp, config)

        assert get_security_context(mcp) is ctx
        assert isinstance(ctx.provenance_ledger, ProvenanceLedger)
