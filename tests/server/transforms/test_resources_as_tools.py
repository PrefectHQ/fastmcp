"""Tests for ResourcesAsTools transform."""

import base64
import json

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
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
