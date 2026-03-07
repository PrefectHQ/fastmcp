"""Tests for version fallback when the highest version is disabled via visibility.

Regression tests for https://github.com/jlowin/fastmcp/issues/3421:
When the latest version of a component is disabled, get_* methods should
fall back to the next-highest enabled version instead of returning None.
"""
# ruff: noqa: F811  # Intentional function redefinition for version testing

from __future__ import annotations

from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.types import TextContent

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


class TestToolVersionFallback:
    """Test that disabling the latest tool version falls back correctly."""

    async def test_list_tools_shows_v1_when_v2_disabled(self):
        """list_tools should show v1 when v2 is disabled."""
        mcp = FastMCP()

        @mcp.tool(version="1.0")
        def calc() -> int:
            return 1

        @mcp.tool(version="2.0")
        def calc() -> int:
            return 2

        mcp.disable(version=VersionSpec(eq="2.0"))

        tools = await mcp.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "calc"
        assert tools[0].version == "1.0"

    async def test_get_tool_returns_v1_when_v2_disabled(self):
        """get_tool should return v1 when v2 is disabled (core bug)."""
        mcp = FastMCP()

        @mcp.tool(version="1.0")
        def calc() -> int:
            return 1

        @mcp.tool(version="2.0")
        def calc() -> int:
            return 2

        mcp.disable(version=VersionSpec(eq="2.0"))

        tool = await mcp.get_tool("calc")
        assert tool is not None
        assert tool.version == "1.0"

    async def test_call_tool_uses_v1_when_v2_disabled(self):
        """call_tool should invoke v1 when v2 is disabled."""
        mcp = FastMCP()

        @mcp.tool(version="1.0")
        def calc() -> int:
            return 1

        @mcp.tool(version="2.0")
        def calc() -> int:
            return 2

        mcp.disable(version=VersionSpec(eq="2.0"))

        result = await mcp.call_tool("calc", {})
        first = result.content[0]
        assert isinstance(first, TextContent)
        assert first.text == "1"

    async def test_get_tool_explicit_disabled_version_returns_none(self):
        """Requesting a specific disabled version should return None."""
        mcp = FastMCP()

        @mcp.tool(version="1.0")
        def calc() -> int:
            return 1

        @mcp.tool(version="2.0")
        def calc() -> int:
            return 2

        mcp.disable(version=VersionSpec(eq="2.0"))

        tool = await mcp.get_tool("calc", VersionSpec(eq="2.0"))
        assert tool is None

    async def test_get_tool_all_versions_disabled_returns_none(self):
        """When all versions are disabled, get_tool returns None."""
        mcp = FastMCP()

        @mcp.tool(version="1.0")
        def calc() -> int:
            return 1

        @mcp.tool(version="2.0")
        def calc() -> int:
            return 2

        mcp.disable(names={"calc"})

        tool = await mcp.get_tool("calc")
        assert tool is None

    async def test_get_tool_middle_version_fallback(self):
        """Disabling v3 should fall back to v2, not v1."""
        mcp = FastMCP()

        @mcp.tool(version="1.0")
        def calc() -> int:
            return 1

        @mcp.tool(version="2.0")
        def calc() -> int:
            return 2

        @mcp.tool(version="3.0")
        def calc() -> int:
            return 3

        mcp.disable(version=VersionSpec(eq="3.0"))

        tool = await mcp.get_tool("calc")
        assert tool is not None
        assert tool.version == "2.0"


class TestResourceVersionFallback:
    """Test that disabling the latest resource version falls back correctly."""

    async def test_get_resource_returns_v1_when_v2_disabled(self):
        """get_resource should return v1 when v2 is disabled."""
        mcp = FastMCP()

        @mcp.resource("data://info", version="1.0")
        def info() -> str:
            return "v1"

        @mcp.resource("data://info", version="2.0")
        def info() -> str:
            return "v2"

        mcp.disable(version=VersionSpec(eq="2.0"))

        resource = await mcp.get_resource("data://info")
        assert resource is not None
        assert resource.version == "1.0"

    async def test_get_resource_explicit_disabled_version_returns_none(self):
        """Requesting a specific disabled resource version should return None."""
        mcp = FastMCP()

        @mcp.resource("data://info", version="1.0")
        def info() -> str:
            return "v1"

        @mcp.resource("data://info", version="2.0")
        def info() -> str:
            return "v2"

        mcp.disable(version=VersionSpec(eq="2.0"))

        resource = await mcp.get_resource("data://info", VersionSpec(eq="2.0"))
        assert resource is None


class TestResourceTemplateVersionFallback:
    """Test that disabling the latest template version falls back correctly."""

    async def test_get_resource_template_returns_v1_when_v2_disabled(self):
        """get_resource_template should return v1 when v2 is disabled."""
        mcp = FastMCP()

        @mcp.resource("data://items/{id}", version="1.0")
        def item(id: str) -> str:
            return f"v1-{id}"

        @mcp.resource("data://items/{id}", version="2.0")
        def item(id: str) -> str:
            return f"v2-{id}"

        mcp.disable(version=VersionSpec(eq="2.0"))

        template = await mcp.get_resource_template("data://items/{id}")
        assert template is not None
        assert template.version == "1.0"


class TestPromptVersionFallback:
    """Test that disabling the latest prompt version falls back correctly."""

    async def test_get_prompt_returns_v1_when_v2_disabled(self):
        """get_prompt should return v1 when v2 is disabled."""
        mcp = FastMCP()

        @mcp.prompt(version="1.0")
        def greet() -> str:
            return "hello v1"

        @mcp.prompt(version="2.0")
        def greet() -> str:
            return "hello v2"

        mcp.disable(version=VersionSpec(eq="2.0"))

        prompt = await mcp.get_prompt("greet")
        assert prompt is not None
        assert prompt.version == "1.0"

    async def test_get_prompt_explicit_disabled_version_returns_none(self):
        """Requesting a specific disabled prompt version should return None."""
        mcp = FastMCP()

        @mcp.prompt(version="1.0")
        def greet() -> str:
            return "hello v1"

        @mcp.prompt(version="2.0")
        def greet() -> str:
            return "hello v2"

        mcp.disable(version=VersionSpec(eq="2.0"))

        prompt = await mcp.get_prompt("greet", VersionSpec(eq="2.0"))
        assert prompt is None


class TestFallbackRespectsAuth:
    """Fallback to older versions must enforce auth checks.

    When the highest version is disabled and the code falls back to older
    versions, those candidates must go through auth filtering. Otherwise
    a protected v1 could be exposed to unauthenticated users when a
    public v2 is disabled.
    """

    async def test_tool_fallback_respects_auth(self):
        """Disabling v2 should not expose auth-protected v1 to unauthorized users."""
        mcp = FastMCP()

        @mcp.tool(version="1.0", auth=require_scopes("admin"))
        def calc() -> int:
            return 1

        @mcp.tool(version="2.0")
        def calc() -> int:
            return 2

        mcp.disable(version=VersionSpec(eq="2.0"))

        # Without an admin token, v1 should NOT be returned
        tool = await mcp.get_tool("calc")
        assert tool is None

    async def test_tool_fallback_allows_authorized_user(self):
        """Fallback should return auth-protected v1 to authorized users."""
        mcp = FastMCP()

        @mcp.tool(version="1.0", auth=require_scopes("admin"))
        def calc() -> int:
            return 1

        @mcp.tool(version="2.0")
        def calc() -> int:
            return 2

        mcp.disable(version=VersionSpec(eq="2.0"))

        token = _make_token(scopes=["admin"])
        tok = _set_token(token)
        try:
            tool = await mcp.get_tool("calc")
            assert tool is not None
            assert tool.version == "1.0"
        finally:
            auth_context_var.reset(tok)

    async def test_resource_fallback_respects_auth(self):
        """Disabling v2 should not expose auth-protected v1 resource."""
        mcp = FastMCP()

        @mcp.resource("data://info", version="1.0", auth=require_scopes("admin"))
        def info() -> str:
            return "v1"

        @mcp.resource("data://info", version="2.0")
        def info() -> str:
            return "v2"

        mcp.disable(version=VersionSpec(eq="2.0"))

        resource = await mcp.get_resource("data://info")
        assert resource is None

    async def test_resource_template_fallback_respects_auth(self):
        """Disabling v2 should not expose auth-protected v1 template."""
        mcp = FastMCP()

        @mcp.resource("data://items/{id}", version="1.0", auth=require_scopes("admin"))
        def item(id: str) -> str:
            return f"v1-{id}"

        @mcp.resource("data://items/{id}", version="2.0")
        def item(id: str) -> str:
            return f"v2-{id}"

        mcp.disable(version=VersionSpec(eq="2.0"))

        template = await mcp.get_resource_template("data://items/{id}")
        assert template is None

    async def test_prompt_fallback_respects_auth(self):
        """Disabling v2 should not expose auth-protected v1 prompt."""
        mcp = FastMCP()

        @mcp.prompt(version="1.0", auth=require_scopes("admin"))
        def greet() -> str:
            return "hello v1"

        @mcp.prompt(version="2.0")
        def greet() -> str:
            return "hello v2"

        mcp.disable(version=VersionSpec(eq="2.0"))

        prompt = await mcp.get_prompt("greet")
        assert prompt is None

    async def test_fallback_skips_unauthorized_picks_next(self):
        """When multiple fallback candidates exist, skip unauthorized ones."""
        mcp = FastMCP()

        @mcp.tool(version="1.0")
        def calc() -> int:
            return 1

        @mcp.tool(version="2.0", auth=require_scopes("admin"))
        def calc() -> int:
            return 2

        @mcp.tool(version="3.0")
        def calc() -> int:
            return 3

        mcp.disable(version=VersionSpec(eq="3.0"))

        # v2 requires admin, so unauthorized user should get v1
        tool = await mcp.get_tool("calc")
        assert tool is not None
        assert tool.version == "1.0"
