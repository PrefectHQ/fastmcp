"""Tests for version fallback when the highest version is unauthorized.

Regression tests for https://github.com/jlowin/fastmcp/issues/4950:
When the highest version of a component fails component-level auth, get_*
methods should fall back to the highest authorized version instead of
returning None, matching what list_* already exposes.
"""
# ruff: noqa: F811  # Intentional function redefinition for version testing

from __future__ import annotations

from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp_types import TextContent

from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, require_scopes
from fastmcp.utilities.versions import VersionSpec


def _make_token(scopes: list[str] | None = None) -> AccessToken:
    """Create a test access token."""
    return AccessToken(
        token="test-token",
        client_id="test-client",
        scopes=scopes or [],
        expires_at=None,
        claims={},
    )


def _set_token(token: AccessToken | None):
    """Set the access token in the auth context var."""
    if token is None:
        return auth_context_var.set(None)
    return auth_context_var.set(AuthenticatedUser(token))


def _server_with_protected_v2() -> FastMCP:
    mcp = FastMCP()

    @mcp.tool(version="1.0")
    def calc() -> int:
        return 1

    @mcp.tool(version="2.0", auth=require_scopes("admin"))
    def calc() -> int:
        return 2

    return mcp


class TestToolAuthVersionFallback:
    """Direct tool lookups must agree with list_tools under auth filtering."""

    async def test_list_tools_shows_v1_when_v2_unauthorized(self):
        mcp = _server_with_protected_v2()

        tools = await mcp.list_tools()
        assert [(t.name, t.version) for t in tools] == [("calc", "1.0")]

    async def test_get_tool_returns_v1_when_v2_unauthorized(self):
        mcp = _server_with_protected_v2()

        tool = await mcp.get_tool("calc")
        assert tool is not None
        assert tool.version == "1.0"

    async def test_call_tool_uses_v1_when_v2_unauthorized(self):
        mcp = _server_with_protected_v2()

        result = await mcp.call_tool("calc", {})
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "1"

    async def test_get_tool_explicit_unauthorized_version_returns_none(self):
        """An explicitly requested version is still authorized on its own."""
        mcp = _server_with_protected_v2()

        tool = await mcp.get_tool("calc", VersionSpec(eq="2.0"))
        assert tool is None

    async def test_get_tool_returns_v2_for_authorized_user(self):
        mcp = _server_with_protected_v2()

        tok = _set_token(_make_token(scopes=["admin"]))
        try:
            tool = await mcp.get_tool("calc")
            assert tool is not None
            assert tool.version == "2.0"
        finally:
            auth_context_var.reset(tok)

    async def test_get_tool_all_versions_unauthorized_returns_none(self):
        mcp = FastMCP()

        @mcp.tool(version="1.0", auth=require_scopes("admin"))
        def calc() -> int:
            return 1

        @mcp.tool(version="2.0", auth=require_scopes("admin"))
        def calc() -> int:
            return 2

        tool = await mcp.get_tool("calc")
        assert tool is None

    async def test_get_tool_skips_disabled_and_unauthorized_versions(self):
        """Fallback picks the highest version that is both enabled and authorized."""
        mcp = FastMCP()

        @mcp.tool(version="1.0")
        def calc() -> int:
            return 1

        @mcp.tool(version="2.0")
        def calc() -> int:
            return 2

        @mcp.tool(version="3.0", auth=require_scopes("admin"))
        def calc() -> int:
            return 3

        mcp.disable(version=VersionSpec(eq="2.0"))

        tool = await mcp.get_tool("calc")
        assert tool is not None
        assert tool.version == "1.0"

    async def test_get_unknown_tool_returns_none(self):
        mcp = _server_with_protected_v2()

        assert await mcp.get_tool("missing") is None


class TestResourceAuthVersionFallback:
    async def test_get_resource_returns_v1_when_v2_unauthorized(self):
        mcp = FastMCP()

        @mcp.resource("data://info", version="1.0")
        def info() -> str:
            return "v1"

        @mcp.resource("data://info", version="2.0", auth=require_scopes("admin"))
        def info() -> str:
            return "v2"

        resource = await mcp.get_resource("data://info")
        assert resource is not None
        assert resource.version == "1.0"

    async def test_get_resource_explicit_unauthorized_version_returns_none(self):
        mcp = FastMCP()

        @mcp.resource("data://info", version="1.0")
        def info() -> str:
            return "v1"

        @mcp.resource("data://info", version="2.0", auth=require_scopes("admin"))
        def info() -> str:
            return "v2"

        resource = await mcp.get_resource("data://info", VersionSpec(eq="2.0"))
        assert resource is None


class TestResourceTemplateAuthVersionFallback:
    async def test_get_resource_template_returns_v1_when_v2_unauthorized(self):
        mcp = FastMCP()

        @mcp.resource("data://items/{id}", version="1.0")
        def item(id: str) -> str:
            return f"v1-{id}"

        @mcp.resource("data://items/{id}", version="2.0", auth=require_scopes("admin"))
        def item(id: str) -> str:
            return f"v2-{id}"

        template = await mcp.get_resource_template("data://items/{id}")
        assert template is not None
        assert template.version == "1.0"


class TestPromptAuthVersionFallback:
    async def test_get_prompt_returns_v1_when_v2_unauthorized(self):
        mcp = FastMCP()

        @mcp.prompt(version="1.0")
        def greet() -> str:
            return "hello v1"

        @mcp.prompt(version="2.0", auth=require_scopes("admin"))
        def greet() -> str:
            return "hello v2"

        prompt = await mcp.get_prompt("greet")
        assert prompt is not None
        assert prompt.version == "1.0"

    async def test_get_prompt_explicit_unauthorized_version_returns_none(self):
        mcp = FastMCP()

        @mcp.prompt(version="1.0")
        def greet() -> str:
            return "hello v1"

        @mcp.prompt(version="2.0", auth=require_scopes("admin"))
        def greet() -> str:
            return "hello v2"

        prompt = await mcp.get_prompt("greet", VersionSpec(eq="2.0"))
        assert prompt is None
