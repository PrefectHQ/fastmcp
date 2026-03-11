"""Tests for SecureMCP-owned settings and helper integration."""

from __future__ import annotations

import pytest

from fastmcp import FastMCP
from fastmcp.server.security import attach_security, get_security_context
from fastmcp.server.security.config import PolicyConfig, SecurityConfig
from fastmcp.server.security.middleware.policy_enforcement import (
    PolicyEnforcementMiddleware,
)
from fastmcp.server.security.orchestrator import SecurityContext
from fastmcp.server.security.policy.provider import AllowAllPolicy
from fastmcp.server.security.settings import SecuritySettings, get_security_settings
from fastmcp.settings import Settings


def _clear_security_env(monkeypatch) -> None:
    for name in [
        "SECUREMCP_ENABLED",
        "SECUREMCP_POLICY_FAIL_CLOSED",
        "SECUREMCP_POLICY_BYPASS_STDIO",
        "SECUREMCP_POLICY_HOT_SWAP",
        "FASTMCP_SECURITY_ENABLED",
        "FASTMCP_SECURITY_POLICY_FAIL_CLOSED",
        "FASTMCP_SECURITY_POLICY_BYPASS_STDIO",
        "FASTMCP_SECURITY_POLICY_HOT_SWAP",
    ]:
        monkeypatch.delenv(name, raising=False)


class TestSecuritySettings:
    def test_core_settings_no_longer_expose_security(self):
        settings = Settings()
        assert not hasattr(settings, "security")

    def test_get_security_context_ignores_legacy_server_field(self):
        server = FastMCP("test")
        setattr(server, "_security_context", SecurityContext(config=SecurityConfig()))

        assert get_security_context(server) is None

    def test_reads_securemcp_prefix(self, monkeypatch):
        _clear_security_env(monkeypatch)
        monkeypatch.setenv("SECUREMCP_ENABLED", "false")
        monkeypatch.setenv("SECUREMCP_POLICY_BYPASS_STDIO", "false")

        settings = SecuritySettings()

        assert settings.enabled is False
        assert settings.policy_bypass_stdio is False

    def test_reads_legacy_fastmcp_prefix(self, monkeypatch):
        _clear_security_env(monkeypatch)
        monkeypatch.setenv("FASTMCP_SECURITY_ENABLED", "false")
        monkeypatch.setenv("FASTMCP_SECURITY_POLICY_HOT_SWAP", "false")

        settings = get_security_settings()

        assert settings.enabled is False
        assert settings.policy_hot_swap is False

    def test_canonical_prefix_wins_over_legacy_prefix(self, monkeypatch):
        _clear_security_env(monkeypatch)
        monkeypatch.setenv("SECUREMCP_POLICY_BYPASS_STDIO", "false")
        monkeypatch.setenv("FASTMCP_SECURITY_POLICY_BYPASS_STDIO", "true")

        settings = SecuritySettings()

        assert settings.policy_bypass_stdio is False


class TestAttachSecuritySettings:
    def test_fastmcp_security_config_kwarg_is_removed(self):
        with pytest.raises(TypeError, match="attach_security"):
            FastMCP("test", security_config=SecurityConfig())

    def test_attach_security_uses_settings_bypass_stdio(self, monkeypatch):
        _clear_security_env(monkeypatch)
        monkeypatch.setenv("SECUREMCP_POLICY_BYPASS_STDIO", "false")

        server = FastMCP("test")
        attach_security(
            server,
            SecurityConfig(policy=PolicyConfig(providers=[AllowAllPolicy()])),
        )

        policy_mw = next(
            m for m in server.middleware if isinstance(m, PolicyEnforcementMiddleware)
        )
        assert policy_mw.bypass_stdio is False

    def test_attach_security_override_beats_settings(self, monkeypatch):
        _clear_security_env(monkeypatch)
        monkeypatch.setenv("SECUREMCP_POLICY_BYPASS_STDIO", "false")

        server = FastMCP("test")
        attach_security(
            server,
            SecurityConfig(policy=PolicyConfig(providers=[AllowAllPolicy()])),
            bypass_stdio=True,
        )

        policy_mw = next(
            m for m in server.middleware if isinstance(m, PolicyEnforcementMiddleware)
        )
        assert policy_mw.bypass_stdio is True

    def test_attach_security_respects_disabled_setting(self, monkeypatch):
        _clear_security_env(monkeypatch)
        monkeypatch.setenv("SECUREMCP_ENABLED", "false")

        server = FastMCP("test")
        ctx = attach_security(
            server,
            SecurityConfig(policy=PolicyConfig(providers=[AllowAllPolicy()])),
        )

        assert get_security_context(server) is ctx
        assert ctx.policy_engine is None
        assert ctx.middleware == []
        assert not any(
            isinstance(m, PolicyEnforcementMiddleware) for m in server.middleware
        )
