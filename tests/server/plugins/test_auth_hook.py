"""Tests for the `Plugin.auth()` contribution hook (FMCP-24).

Semantic rule: FastMCP's auth slot is singular. `auth=` + every plugin's
`auth()` return are collected; at most one `AuthProvider` may be active.
Multiple sources raise `PluginError` — no automatic `MultiAuth` wrapping.
Users who want multi-source auth build `MultiAuth` explicitly.
"""

from __future__ import annotations

import pytest

from fastmcp import FastMCP
from fastmcp.server.auth.auth import AuthProvider, TokenVerifier
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.server.plugins.base import Plugin, PluginError, PluginMeta


def _verifier(token: str = "t") -> TokenVerifier:
    return StaticTokenVerifier(tokens={token: {"client_id": "c", "scopes": []}})


class _FakeServerAuth(AuthProvider):
    """Minimal non-TokenVerifier AuthProvider — stands in for an OAuth
    server in tests without needing a real issuer URL."""

    def __init__(self, base_url: str = "https://example.com") -> None:
        super().__init__(base_url=base_url)

    async def verify_token(self, token):  # type: ignore[override]
        return None


class TestDefaultHook:
    def test_plugin_auth_defaults_to_none(self):
        class P(Plugin):
            meta = PluginMeta(name="p")

        assert P().auth() is None


class TestSingleSource:
    def test_lone_plugin_contribution_becomes_self_auth(self):
        """One plugin contributing one AuthProvider, no user `auth=` → that
        provider is installed directly as `self.auth`. No wrapping, no
        lifespan round-trip: `self.auth` is set synchronously at
        `add_plugin` time so HTTP/SSE transports see it when they build
        the Starlette app."""
        v = _verifier()

        class P(Plugin):
            meta = PluginMeta(name="p")

            def auth(self) -> AuthProvider | None:
                return v

        mcp = FastMCP("t", plugins=[P()])
        assert mcp.auth is v

    def test_user_declared_alone_untouched(self):
        """No plugin contributing auth → `self.auth` is exactly the user
        value, no processing."""
        user_v = _verifier()
        mcp = FastMCP("t", auth=user_v)
        assert mcp.auth is user_v

    def test_no_sources_leaves_auth_none(self):
        mcp = FastMCP("t")
        assert mcp.auth is None

    def test_add_plugin_installs_auth(self):
        """Plugin added after construction installs its auth synchronously."""
        v = _verifier()
        mcp = FastMCP("t")
        assert mcp.auth is None

        class P(Plugin):
            meta = PluginMeta(name="p")

            def auth(self) -> AuthProvider | None:
                return v

        mcp.add_plugin(P())
        assert mcp.auth is v


class TestMultipleSourcesRejected:
    """FastMCP's auth slot is singular. Multiple contributors raise."""

    def test_two_plugin_verifiers_raises(self):
        v1, v2 = _verifier("one"), _verifier("two")

        class P1(Plugin):
            meta = PluginMeta(name="p1")

            def auth(self) -> AuthProvider | None:
                return v1

        class P2(Plugin):
            meta = PluginMeta(name="p2")

            def auth(self) -> AuthProvider | None:
                return v2

        with pytest.raises(PluginError, match="Multiple auth sources"):
            FastMCP("t", plugins=[P1(), P2()])

    def test_user_plus_plugin_raises(self):
        """User-declared `auth=` + any plugin contribution is ambiguous —
        framework doesn't silently pick a winner."""
        user_v, plugin_v = _verifier("u"), _verifier("p")

        class P(Plugin):
            meta = PluginMeta(name="p")

            def auth(self) -> AuthProvider | None:
                return plugin_v

        with pytest.raises(PluginError, match="Multiple auth sources"):
            FastMCP("t", auth=user_v, plugins=[P()])

    def test_two_server_contributions_raises(self):
        """Also covers the server-server case (historical multiauth reason)."""
        s1 = _FakeServerAuth("https://a.example")
        s2 = _FakeServerAuth("https://b.example")

        class P1(Plugin):
            meta = PluginMeta(name="p1")

            def auth(self) -> AuthProvider | None:
                return s1

        class P2(Plugin):
            meta = PluginMeta(name="p2")

            def auth(self) -> AuthProvider | None:
                return s2

        with pytest.raises(PluginError, match="Multiple auth sources"):
            FastMCP("t", plugins=[P1(), P2()])

    def test_error_names_conflicting_sources(self):
        """Operator needs to know which sources conflict so they can
        disable auth on all but one."""
        v1, v2 = _verifier("a"), _verifier("b")

        class Alpha(Plugin):
            meta = PluginMeta(name="alpha")

            def auth(self) -> AuthProvider | None:
                return v1

        class Beta(Plugin):
            meta = PluginMeta(name="beta")

            def auth(self) -> AuthProvider | None:
                return v2

        with pytest.raises(PluginError) as exc_info:
            FastMCP("t", plugins=[Alpha(), Beta()])

        msg = str(exc_info.value)
        # The "prior" source (alpha) and the rejected plugin (beta) must
        # both appear so operators can act on the conflict without
        # re-running with extra logging.
        assert "'alpha'" in msg
        assert "'beta'" in msg


class TestAddPluginAtomicity:
    def test_rejected_auth_conflict_leaves_no_residue(self):
        """A plugin whose auth contribution would trip the 'Multiple auth
        sources' guard must be fully rolled back: not attached (`_installed_on`
        cleared), not appended to `self.plugins`, no contributions installed."""
        v1 = _verifier("one")

        class P1(Plugin):
            meta = PluginMeta(name="p1")

            def auth(self) -> AuthProvider | None:
                return v1

        class P2(Plugin):
            meta = PluginMeta(name="p2")

            def __init__(self) -> None:
                super().__init__()
                self._v = _verifier("two")

            def auth(self) -> AuthProvider | None:
                return self._v

        p1 = P1()
        mcp = FastMCP("t", plugins=[p1])
        assert mcp.plugins == [p1]
        assert mcp.auth is v1

        p2 = P2()
        with pytest.raises(PluginError, match="Multiple auth sources"):
            mcp.add_plugin(p2)

        # Full rollback: p2 is not in plugins, not attached, auth unchanged.
        assert mcp.plugins == [p1]
        assert mcp.auth is v1
        assert p2._installed_on is None


class TestSingleServerPerInstance:
    def test_same_instance_registered_twice_raises(self):
        """A plugin instance belongs to one server — registering the same
        instance twice (same server or different) raises PluginError."""
        v = _verifier()

        class P(Plugin):
            meta = PluginMeta(name="p")

            def auth(self) -> AuthProvider | None:
                return v

        p = P()
        mcp = FastMCP("t", plugins=[p])
        assert mcp.auth is v

        with pytest.raises(PluginError, match="already installed"):
            mcp.add_plugin(p)

    def test_plugins_kwarg_duplicate_instance_raises(self):
        """Duplicate instance in the `plugins=` kwarg is caught the same way."""

        class P(Plugin):
            meta = PluginMeta(name="p")

        p = P()
        with pytest.raises(PluginError, match="already installed"):
            FastMCP("t", plugins=[p, p])

    def test_instance_on_second_server_raises(self):
        """Sharing a plugin instance across two servers is not supported."""

        class P(Plugin):
            meta = PluginMeta(name="p")

        p = P()
        FastMCP("a", plugins=[p])
        with pytest.raises(PluginError, match="already installed"):
            FastMCP("b", plugins=[p])
