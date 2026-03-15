"""Tests for ResourcesAsTools transform."""

import base64
import json
from collections.abc import Sequence

import pytest
from pydantic import AnyUrl

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.exceptions import ToolError
from fastmcp.resources.function_resource import FunctionResource
from fastmcp.resources.resource import Resource
from fastmcp.resources.template import FunctionResourceTemplate, ResourceTemplate
from fastmcp.server.auth import AuthContext
from fastmcp.server.providers.base import Provider
from fastmcp.server.transforms import ResourcesAsTools


class TestResourcesAsToolsBasic:
    """Test basic ResourcesAsTools functionality."""

    async def test_adds_list_resources_tool(self):
        """Transform adds list_resources tool."""
        mcp = FastMCP("Test")
        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = {t.name for t in tools}
            assert "list_resources" in tool_names

    async def test_adds_read_resource_tool(self):
        """Transform adds read_resource tool."""
        mcp = FastMCP("Test")
        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = {t.name for t in tools}
            assert "read_resource" in tool_names

    async def test_preserves_existing_tools(self):
        """Transform preserves existing tools."""
        mcp = FastMCP("Test")

        @mcp.tool
        def my_tool() -> str:
            return "result"

        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = {t.name for t in tools}
            assert "my_tool" in tool_names
            assert "list_resources" in tool_names
            assert "read_resource" in tool_names


class TestListResourcesTool:
    """Test the list_resources tool."""

    async def test_lists_static_resources(self):
        """list_resources returns static resources with uri."""
        mcp = FastMCP("Test")

        @mcp.resource("config://app")
        def app_config() -> str:
            return "config data"

        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool("list_resources", {})
            resources = json.loads(result.data)

            assert len(resources) == 1
            assert resources[0]["uri"] == "config://app"
            assert resources[0]["name"] == "app_config"

    async def test_lists_resource_templates(self):
        """list_resources returns templates with uri_template."""
        mcp = FastMCP("Test")

        @mcp.resource("file://{path}")
        def read_file(path: str) -> str:
            return f"content of {path}"

        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool("list_resources", {})
            resources = json.loads(result.data)

            assert len(resources) == 1
            assert resources[0]["uri_template"] == "file://{path}"
            assert "uri" not in resources[0]

    async def test_lists_both_resources_and_templates(self):
        """list_resources returns both static and templated resources."""
        mcp = FastMCP("Test")

        @mcp.resource("config://app")
        def app_config() -> str:
            return "config"

        @mcp.resource("file://{path}")
        def read_file(path: str) -> str:
            return f"content of {path}"

        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool("list_resources", {})
            resources = json.loads(result.data)

            assert len(resources) == 2
            # One has uri, one has uri_template
            uris = [r.get("uri") for r in resources if r.get("uri")]
            templates = [
                r.get("uri_template") for r in resources if r.get("uri_template")
            ]
            assert uris == ["config://app"]
            assert templates == ["file://{path}"]

    async def test_empty_when_no_resources(self):
        """list_resources returns empty list when no resources exist."""
        mcp = FastMCP("Test")
        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool("list_resources", {})
            assert json.loads(result.data) == []


class TestReadResourceTool:
    """Test the read_resource tool."""

    async def test_reads_static_resource(self):
        """read_resource reads a static resource by URI."""
        mcp = FastMCP("Test")

        @mcp.resource("config://app")
        def app_config() -> str:
            return "my config data"

        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool("read_resource", {"uri": "config://app"})
            assert result.data == "my config data"

    async def test_reads_templated_resource(self):
        """read_resource reads a templated resource with parameters."""
        mcp = FastMCP("Test")

        @mcp.resource("user://{user_id}/profile")
        def user_profile(user_id: str) -> str:
            return f"Profile for user {user_id}"

        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool(
                "read_resource", {"uri": "user://123/profile"}
            )
            assert result.data == "Profile for user 123"

    async def test_error_on_unknown_resource(self):
        """read_resource raises error for unknown URI."""
        from fastmcp.exceptions import ToolError

        mcp = FastMCP("Test")
        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="Unknown resource"):
                await client.call_tool("read_resource", {"uri": "unknown://resource"})

    async def test_reads_binary_as_base64(self):
        """read_resource returns binary content as base64."""
        mcp = FastMCP("Test")

        @mcp.resource("data://binary", mime_type="application/octet-stream")
        def binary_data() -> bytes:
            return b"\x00\x01\x02\x03"

        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool("read_resource", {"uri": "data://binary"})
            # Should be base64 encoded
            decoded = base64.b64decode(result.data)
            assert decoded == b"\x00\x01\x02\x03"


class TestResourcesAsToolsWithNamespace:
    """Test ResourcesAsTools combined with other transforms."""

    async def test_works_with_namespace_on_provider(self):
        """ResourcesAsTools works when provider has Namespace transform."""
        from fastmcp.server.providers import FastMCPProvider
        from fastmcp.server.transforms import Namespace

        sub = FastMCP("Sub")

        @sub.resource("config://app")
        def app_config() -> str:
            return "sub config"

        main = FastMCP("Main")
        provider = FastMCPProvider(sub)
        provider.add_transform(Namespace("sub"))
        main.add_provider(provider)
        main.add_transform(ResourcesAsTools(main))

        async with Client(main) as client:
            result = await client.call_tool("list_resources", {})
            resources = json.loads(result.data)

            # Resource should have namespaced URI
            assert len(resources) == 1
            assert resources[0]["uri"] == "config://sub/app"


class TestResourcesAsToolsRepr:
    """Test ResourcesAsTools repr."""

    def test_repr(self):
        """Transform has useful repr."""
        mcp = FastMCP("Test")
        transform = ResourcesAsTools(mcp)
        assert "ResourcesAsTools" in repr(transform)


class TestResourcesAsToolsAnnotations:
    """Test ToolAnnotations on generated resource tools."""

    async def test_list_resources_is_read_only(self):
        """list_resources is annotated as read-only by default."""
        mcp = FastMCP("Test")
        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "list_resources")
            assert tool.annotations is not None
            assert tool.annotations.readOnlyHint is True

    async def test_read_resource_is_read_only(self):
        """read_resource is annotated as read-only by default."""
        mcp = FastMCP("Test")
        mcp.add_transform(ResourcesAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "read_resource")
            assert tool.annotations is not None
            assert tool.annotations.readOnlyHint is True


def _deny_all(ctx: AuthContext) -> bool:
    """Auth check that always denies access."""
    return False


class _ResourceProviderWithVisibility(Provider):
    """A non-FastMCP provider with a public and a disabled resource."""

    async def _list_resources(self) -> Sequence[Resource]:
        return [
            FunctionResource(
                uri=AnyUrl("test://public"),
                name="public_data",
                fn=lambda: "public content",
            ),
            FunctionResource(
                uri=AnyUrl("test://secret"),
                name="secret_data",
                fn=lambda: "secret content",
            ),
        ]

    async def _list_resource_templates(self) -> Sequence[ResourceTemplate]:
        return [
            FunctionResourceTemplate.from_function(
                fn=lambda name: f"public {name}",
                uri_template="public://{name}",
                name="public_template",
            ),
            FunctionResourceTemplate.from_function(
                fn=lambda name: f"secret {name}",
                uri_template="secret://{name}",
                name="secret_template",
            ),
        ]


class _ResourceProviderWithAuth(Provider):
    """A non-FastMCP provider with auth-protected resources."""

    async def _list_resources(self) -> Sequence[Resource]:
        return [
            FunctionResource(
                uri=AnyUrl("test://open"),
                name="open_data",
                fn=lambda: "open content",
            ),
            FunctionResource(
                uri=AnyUrl("test://protected"),
                name="protected_data",
                fn=lambda: "protected content",
                auth=_deny_all,
            ),
        ]

    async def _list_resource_templates(self) -> Sequence[ResourceTemplate]:
        template = FunctionResourceTemplate.from_function(
            fn=lambda name: f"protected {name}",
            uri_template="protected://{name}",
            name="protected_template",
            auth=_deny_all,
        )
        return [template]


class TestResourcesAsToolsVisibilityOnProvider:
    """Visibility transforms on non-FastMCP providers are respected."""

    async def test_disabled_resources_hidden_from_list(self):
        """Disabled resources are not listed via list_resources tool."""
        provider = _ResourceProviderWithVisibility()
        provider.disable(names={"test://secret"})

        mcp = FastMCP("Test")
        mcp.add_provider(provider)
        mcp.add_transform(ResourcesAsTools(provider))

        async with Client(mcp) as client:
            result = await client.call_tool("list_resources", {})
            items = json.loads(result.data)
            uris = [r.get("uri") for r in items if r.get("uri")]
            assert "test://public" in uris
            assert "test://secret" not in uris

    async def test_disabled_templates_hidden_from_list(self):
        """Disabled templates are not listed via list_resources tool."""
        provider = _ResourceProviderWithVisibility()
        provider.disable(names={"secret_template"})

        mcp = FastMCP("Test")
        mcp.add_provider(provider)
        mcp.add_transform(ResourcesAsTools(provider))

        async with Client(mcp) as client:
            result = await client.call_tool("list_resources", {})
            items = json.loads(result.data)
            templates = [r.get("uri_template") for r in items if r.get("uri_template")]
            assert "public://{name}" in templates
            assert "secret://{name}" not in templates

    async def test_disabled_resource_cannot_be_read(self):
        """Disabled resources cannot be read via read_resource tool."""
        provider = _ResourceProviderWithVisibility()
        provider.disable(names={"test://secret"})

        mcp = FastMCP("Test")
        mcp.add_provider(provider)
        mcp.add_transform(ResourcesAsTools(provider))

        async with Client(mcp) as client:
            with pytest.raises(ToolError):
                await client.call_tool("read_resource", {"uri": "test://secret"})

    async def test_disabled_template_cannot_be_read(self):
        """Disabled templates cannot be read via read_resource tool."""
        provider = _ResourceProviderWithVisibility()
        provider.disable(names={"secret_template"})

        mcp = FastMCP("Test")
        mcp.add_provider(provider)
        mcp.add_transform(ResourcesAsTools(provider))

        async with Client(mcp) as client:
            with pytest.raises(ToolError):
                await client.call_tool("read_resource", {"uri": "secret://foo"})

    async def test_enabled_resource_still_accessible(self):
        """Enabled resources can still be read via read_resource tool."""
        provider = _ResourceProviderWithVisibility()
        provider.disable(names={"test://secret"})

        mcp = FastMCP("Test")
        mcp.add_provider(provider)
        mcp.add_transform(ResourcesAsTools(provider))

        async with Client(mcp) as client:
            result = await client.call_tool("read_resource", {"uri": "test://public"})
            assert result.data == "public content"


class TestResourcesAsToolsAuthOnProvider:
    """Auth checks on non-FastMCP providers are respected."""

    async def test_auth_protected_resources_hidden_from_list(self):
        """Auth-protected resources are filtered from list_resources tool."""
        provider = _ResourceProviderWithAuth()

        mcp = FastMCP("Test")
        mcp.add_provider(provider)
        mcp.add_transform(ResourcesAsTools(provider))

        async with Client(mcp) as client:
            result = await client.call_tool("list_resources", {})
            items = json.loads(result.data)
            uris = [r.get("uri") for r in items if r.get("uri")]
            assert "test://open" in uris
            assert "test://protected" not in uris

    async def test_auth_protected_templates_hidden_from_list(self):
        """Auth-protected templates are filtered from list_resources tool."""
        provider = _ResourceProviderWithAuth()

        mcp = FastMCP("Test")
        mcp.add_provider(provider)
        mcp.add_transform(ResourcesAsTools(provider))

        async with Client(mcp) as client:
            result = await client.call_tool("list_resources", {})
            items = json.loads(result.data)
            templates = [r.get("uri_template") for r in items if r.get("uri_template")]
            assert "protected://{name}" not in templates

    async def test_auth_protected_resource_cannot_be_read(self):
        """Auth-protected resources cannot be read via read_resource tool."""
        provider = _ResourceProviderWithAuth()

        mcp = FastMCP("Test")
        mcp.add_provider(provider)
        mcp.add_transform(ResourcesAsTools(provider))

        async with Client(mcp) as client:
            with pytest.raises(ToolError):
                await client.call_tool("read_resource", {"uri": "test://protected"})

    async def test_auth_protected_template_cannot_be_read(self):
        """Auth-protected templates cannot be read via read_resource tool."""
        provider = _ResourceProviderWithAuth()

        mcp = FastMCP("Test")
        mcp.add_provider(provider)
        mcp.add_transform(ResourcesAsTools(provider))

        async with Client(mcp) as client:
            with pytest.raises(ToolError):
                await client.call_tool("read_resource", {"uri": "protected://foo"})

    async def test_open_resource_still_accessible(self):
        """Non-auth-protected resources can still be read."""
        provider = _ResourceProviderWithAuth()

        mcp = FastMCP("Test")
        mcp.add_provider(provider)
        mcp.add_transform(ResourcesAsTools(provider))

        async with Client(mcp) as client:
            result = await client.call_tool("read_resource", {"uri": "test://open"})
            assert result.data == "open content"


class TestResourcesAsToolsScopedToSubServer:
    """Scope matching works when wrapping a FastMCP sub-server."""

    async def test_scoped_to_sub_server(self):
        """ResourcesAsTools(sub) only lists sub's resources, not main's."""
        sub = FastMCP("Sub")

        @sub.resource("sub://data")
        def sub_resource() -> str:
            return "sub content"

        main = FastMCP("Main")

        @main.resource("main://data")
        def main_resource() -> str:
            return "main content"

        main.add_provider(sub)
        main.add_transform(ResourcesAsTools(sub))

        async with Client(main) as client:
            result = await client.call_tool("list_resources", {})
            items = json.loads(result.data)
            uris = [r.get("uri") for r in items if r.get("uri")]
            assert "sub://data" in uris
            assert "main://data" not in uris

    async def test_scoped_sub_server_read(self):
        """Resources from a scoped sub-server can be read."""
        sub = FastMCP("Sub")

        @sub.resource("sub://data")
        def sub_resource() -> str:
            return "sub content"

        main = FastMCP("Main")
        main.add_provider(sub)
        main.add_transform(ResourcesAsTools(sub))

        async with Client(main) as client:
            result = await client.call_tool("read_resource", {"uri": "sub://data"})
            assert result.data == "sub content"
