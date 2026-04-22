"""Tests for the `Plugin.auth()` contribution hook (FMCP-24)."""

from __future__ import annotations

import contextlib

import pytest

from fastmcp import FastMCP
from fastmcp.server.auth.auth import AuthProvider, MultiAuth, TokenVerifier
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.server.plugins.base import Plugin, PluginError, PluginMeta


def _verifier(token: str = "t") -> TokenVerifier:
    return StaticTokenVerifier(tokens={token: {"client_id": "c", "scopes": []}})


class _FakeServerAuth(AuthProvider):
    """Minimal non-TokenVerifier AuthProvider — stands in for an OAuth
    server in partition logic tests without needing a real issuer URL."""

    def __init__(self, base_url: str = "https://example.com") -> None:
        super().__init__(base_url=base_url)

    async def verify_token(self, token):  # type: ignore[override]
        return None


async def _enter_lifecycle(mcp: FastMCP) -> None:
    """Run the plugin contribution pipeline manually without launching a server."""
    async with contextlib.AsyncExitStack() as stack:
        await mcp._enter_plugin_contexts(stack)


class TestDefaultHook:
    def test_plugin_auth_defaults_to_empty_list(self):
        class P(Plugin):
            meta = PluginMeta(name="p")

        assert P().auth() == []


class TestSingleContribution:
    async def test_lone_verifier_not_wrapped(self):
        """A single auth contribution (and no user-declared auth) should
        land on `self.auth` directly rather than being wrapped in a
        MultiAuth. MultiAuth is a composition primitive — using it for
        a single source is clutter."""
        v = _verifier()

        class AddVerifier(Plugin):
            meta = PluginMeta(name="verifier")

            def auth(self) -> list[AuthProvider]:
                return [v]

        mcp = FastMCP("t", plugins=[AddVerifier()])
        await _enter_lifecycle(mcp)

        assert mcp.auth is v


class TestMultipleVerifiers:
    async def test_multiple_verifier_plugins_wrap_in_multiauth(self):
        v1, v2 = _verifier("one"), _verifier("two")

        class P1(Plugin):
            meta = PluginMeta(name="p1")

            def auth(self) -> list[AuthProvider]:
                return [v1]

        class P2(Plugin):
            meta = PluginMeta(name="p2")

            def auth(self) -> list[AuthProvider]:
                return [v2]

        mcp = FastMCP("t", plugins=[P1(), P2()])
        await _enter_lifecycle(mcp)

        assert isinstance(mcp.auth, MultiAuth)
        assert mcp.auth.server is None
        assert mcp.auth.verifiers == [v1, v2]


class TestUserDeclaredAuthCombined:
    async def test_user_verifier_plus_plugin_verifier(self):
        user_v, plugin_v = _verifier("u"), _verifier("p")

        class P(Plugin):
            meta = PluginMeta(name="p")

            def auth(self) -> list[AuthProvider]:
                return [plugin_v]

        mcp = FastMCP("t", auth=user_v, plugins=[P()])
        await _enter_lifecycle(mcp)

        assert isinstance(mcp.auth, MultiAuth)
        # User verifier first, then plugin's.
        assert mcp.auth.verifiers == [user_v, plugin_v]
        assert mcp.auth.server is None

    async def test_user_declared_alone_is_not_wrapped(self):
        """No plugins contributing auth → `self.auth` is exactly the user
        value, no composition overhead."""
        user_v = _verifier()
        mcp = FastMCP("t", auth=user_v)
        await _enter_lifecycle(mcp)

        assert mcp.auth is user_v


class TestServerPlusVerifiers:
    async def test_oauth_server_with_plugin_verifier(self):
        """OAuth provider in `auth=` + plugin-contributed TokenVerifier
        compose into MultiAuth(server=oauth, verifiers=[v])."""

        server = _FakeServerAuth()
        v = _verifier()

        class P(Plugin):
            meta = PluginMeta(name="p")

            def auth(self) -> list[AuthProvider]:
                return [v]

        mcp = FastMCP("t", auth=server, plugins=[P()])
        await _enter_lifecycle(mcp)

        assert isinstance(mcp.auth, MultiAuth)
        assert mcp.auth.server is server
        assert mcp.auth.verifiers == [v]


class TestMultipleServersRejected:
    async def test_two_plugin_servers_raises(self):
        s1 = _FakeServerAuth("https://a.example")
        s2 = _FakeServerAuth("https://b.example")

        class P1(Plugin):
            meta = PluginMeta(name="p1")

            def auth(self) -> list[AuthProvider]:
                return [s1]

        class P2(Plugin):
            meta = PluginMeta(name="p2")

            def auth(self) -> list[AuthProvider]:
                return [s2]

        mcp = FastMCP("t", plugins=[P1(), P2()])
        with pytest.raises(PluginError, match="Multiple auth providers"):
            await _enter_lifecycle(mcp)


class TestRebuildOnEphemeralTeardown:
    async def test_ephemeral_plugin_auth_removed_on_teardown(self):
        """Ephemeral (loader-added) plugins' auth contributions must be
        stripped from `self.auth` when the plugin is torn down — the
        resolver rebuilds from scratch to avoid bespoke per-plugin
        accounting in the auth slot."""
        permanent_v = _verifier("perm")
        ephemeral_v = _verifier("temp")

        class Permanent(Plugin):
            meta = PluginMeta(name="perm")

            def auth(self) -> list[AuthProvider]:
                return [permanent_v]

        class Ephemeral(Plugin):
            meta = PluginMeta(name="temp")

            def auth(self) -> list[AuthProvider]:
                return [ephemeral_v]

        mcp = FastMCP("t", plugins=[Permanent()])

        async with contextlib.AsyncExitStack() as stack:
            await mcp._enter_plugin_contexts(stack)
            assert mcp.auth is permanent_v

            # Simulate a loader adding a plugin during lifespan.
            mcp._in_plugin_setup_pass = True
            try:
                mcp.add_plugin(Ephemeral())
            finally:
                mcp._in_plugin_setup_pass = False
            # Loader-added plugins have their contributions collected on
            # subsequent lifespan entries; for the test, re-run
            # contribution collection manually by re-entering contexts.
            await mcp._enter_plugin_contexts(stack)
            assert isinstance(mcp.auth, MultiAuth)
            assert mcp.auth.verifiers == [permanent_v, ephemeral_v]

        # After stack exit ephemeral teardown fires and auth rebuilds;
        # should be back to just the permanent verifier (unwrapped, since
        # single contribution).
        assert mcp.auth is permanent_v
