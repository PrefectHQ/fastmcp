"""Tests for Namespace and ToolTransform."""

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.providers import FastMCPProvider
from fastmcp.server.transforms import Namespace, ToolTransform
from fastmcp.tools.tool_transform import ToolTransformConfig


class TestNamespaceTransform:
    """Test Namespace transform transformations."""

    async def test_namespace_prefixes_tool_names(self):
        """Test that namespace is applied as prefix to tool names."""
        server = FastMCP("Test")

        @server.tool
        def my_tool() -> str:
            return "result"

        provider = FastMCPProvider(server)
        layer = Namespace("ns")

        # Get tools and pass directly to transform
        tools = await provider.list_tools()
        transformed_tools = await layer.list_tools(tools)

        assert len(transformed_tools) == 1
        assert transformed_tools[0].name == "ns_my_tool"

    async def test_namespace_prefixes_prompt_names(self):
        """Test that namespace is applied as prefix to prompt names."""
        server = FastMCP("Test")

        @server.prompt
        def my_prompt() -> str:
            return "prompt content"

        provider = FastMCPProvider(server)
        layer = Namespace("ns")

        prompts = await provider.list_prompts()
        transformed_prompts = await layer.list_prompts(prompts)

        assert len(transformed_prompts) == 1
        assert transformed_prompts[0].name == "ns_my_prompt"

    async def test_namespace_prefixes_resource_uris(self):
        """Test that namespace is inserted into resource URIs."""
        server = FastMCP("Test")

        @server.resource("resource://data")
        def my_resource() -> str:
            return "content"

        provider = FastMCPProvider(server)
        layer = Namespace("ns")

        resources = await provider.list_resources()
        transformed_resources = await layer.list_resources(resources)

        assert len(transformed_resources) == 1
        assert str(transformed_resources[0].uri) == "resource://ns/data"

    async def test_namespace_prefixes_template_uris(self):
        """Test that namespace is inserted into resource template URIs."""
        server = FastMCP("Test")

        @server.resource("resource://{name}/data")
        def my_template(name: str) -> str:
            return f"content for {name}"

        provider = FastMCPProvider(server)
        layer = Namespace("ns")

        templates = await provider.list_resource_templates()
        transformed_templates = await layer.list_resource_templates(templates)

        assert len(transformed_templates) == 1
        assert transformed_templates[0].uri_template == "resource://ns/{name}/data"


class TestToolTransformRenames:
    """Test ToolTransform renaming functionality."""

    async def test_tool_rename(self):
        """Test tool renaming with ToolTransform."""
        server = FastMCP("Test")

        @server.tool
        def verbose_tool_name() -> str:
            return "result"

        provider = FastMCPProvider(server)
        layer = ToolTransform({"verbose_tool_name": ToolTransformConfig(name="short")})

        tools = await provider.list_tools()
        transformed_tools = await layer.list_tools(tools)

        assert len(transformed_tools) == 1
        assert transformed_tools[0].name == "short"

    async def test_renamed_tool_is_callable_via_mount(self):
        """Test that renamed tools can be called by new name via mount."""
        sub = FastMCP("Sub")

        @sub.tool
        def original() -> str:
            return "success"

        main = FastMCP("Main")
        # Add provider with transform layer
        provider = FastMCPProvider(sub)
        provider.add_transform(
            ToolTransform({"original": ToolTransformConfig(name="renamed")})
        )
        main.add_provider(provider)

        async with Client(main) as client:
            result = await client.call_tool("renamed", {})
            assert result.data == "success"

    def test_duplicate_rename_targets_raises_error(self):
        """Test that duplicate target names in ToolTransform raises ValueError."""
        with pytest.raises(ValueError, match="duplicate target name"):
            ToolTransform(
                {
                    "tool_a": ToolTransformConfig(name="same"),
                    "tool_b": ToolTransformConfig(name="same"),
                }
            )


class TestTransformReverseLookup:
    """Test reverse lookups for routing."""

    async def test_namespace_get_tool(self):
        """Test that tools can be looked up by transformed name."""
        server = FastMCP("Test")

        @server.tool
        def my_tool() -> str:
            return "result"

        provider = FastMCPProvider(server)
        layer = Namespace("ns")

        # Create call_next that delegates to provider
        async def get_tool(name: str, version=None):
            return await provider._get_tool(name, version)

        tool = await layer.get_tool("ns_my_tool", get_tool)

        assert tool is not None
        assert tool.name == "ns_my_tool"

    async def test_transform_layer_get_tool(self):
        """Test that renamed tools can be looked up by new name."""
        server = FastMCP("Test")

        @server.tool
        def original() -> str:
            return "result"

        provider = FastMCPProvider(server)
        layer = ToolTransform({"original": ToolTransformConfig(name="renamed")})

        async def get_tool(name: str, version=None):
            return await provider._get_tool(name, version)

        tool = await layer.get_tool("renamed", get_tool)

        assert tool is not None
        assert tool.name == "renamed"

    async def test_namespace_get_resource(self):
        """Test that resources can be looked up by transformed URI."""
        server = FastMCP("Test")

        @server.resource("resource://data")
        def my_resource() -> str:
            return "content"

        provider = FastMCPProvider(server)
        layer = Namespace("ns")

        async def get_resource(uri: str, version=None):
            return await provider._get_resource(uri, version)

        resource = await layer.get_resource("resource://ns/data", get_resource)

        assert resource is not None
        assert str(resource.uri) == "resource://ns/data"

    async def test_nonmatching_namespace_returns_none(self):
        """Test that lookups with wrong namespace return None."""
        server = FastMCP("Test")

        @server.tool
        def my_tool() -> str:
            return "result"

        provider = FastMCPProvider(server)
        layer = Namespace("ns")

        async def get_tool(name: str, version=None):
            return await provider._get_tool(name, version)

        # Wrong namespace prefix
        assert await layer.get_tool("wrong_my_tool", get_tool) is None
        # No prefix at all
        assert await layer.get_tool("my_tool", get_tool) is None


class TestTransformStacking:
    """Test stacking multiple transforms via provider add_transform."""

    async def test_stacked_namespaces_compose(self):
        """Test that stacked namespaces are applied in order."""
        server = FastMCP("Test")

        @server.tool
        def my_tool() -> str:
            return "result"

        provider = FastMCPProvider(server)
        inner_layer = Namespace("inner")
        outer_layer = Namespace("outer")

        # Apply transforms sequentially: base -> inner -> outer
        tools = await provider.list_tools()
        tools = await inner_layer.list_tools(tools)
        tools = await outer_layer.list_tools(tools)

        assert len(tools) == 1
        assert tools[0].name == "outer_inner_my_tool"

    async def test_stacked_transforms_are_callable(self):
        """Test that stacked transforms still allow tool calls."""
        sub = FastMCP("Sub")

        @sub.tool
        def my_tool() -> str:
            return "success"

        main = FastMCP("Main")
        provider = FastMCPProvider(sub)
        # Add namespace layer then rename layer
        provider.add_transform(Namespace("ns"))
        provider.add_transform(
            ToolTransform({"ns_my_tool": ToolTransformConfig(name="short")})
        )
        main.add_provider(provider)

        async with Client(main) as client:
            result = await client.call_tool("short", {})
            assert result.data == "success"


class TestNoTransformation:
    """Test behavior when no transformations are applied."""

    async def test_transform_passthrough(self):
        """Test that base Transform passes through unchanged."""
        from fastmcp.server.transforms import Transform

        server = FastMCP("Test")

        @server.tool
        def my_tool() -> str:
            return "result"

        provider = FastMCPProvider(server)
        transform = Transform()

        tools = await provider.list_tools()
        transformed_tools = await transform.list_tools(tools)

        assert len(transformed_tools) == 1
        assert transformed_tools[0].name == "my_tool"

    async def test_empty_transform_layer_passthrough(self):
        """Test that empty ToolTransform has no effect."""
        server = FastMCP("Test")

        @server.tool
        def my_tool() -> str:
            return "result"

        provider = FastMCPProvider(server)
        layer = ToolTransform({})

        tools = await provider.list_tools()
        transformed_tools = await layer.list_tools(tools)

        assert len(transformed_tools) == 1
        assert transformed_tools[0].name == "my_tool"
