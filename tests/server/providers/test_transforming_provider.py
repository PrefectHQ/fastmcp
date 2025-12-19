"""Tests for TransformingProvider."""

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.providers import FastMCPProvider, TransformingProvider


class TestNamespaceTransformation:
    """Test namespace prefix transformations."""

    async def test_namespace_prefixes_tool_names(self):
        """Test that namespace is applied as prefix to tool names."""
        server = FastMCP("Test")

        @server.tool
        def my_tool() -> str:
            return "result"

        provider = FastMCPProvider(server).with_namespace("ns")
        tools = await provider.list_tools()

        assert len(tools) == 1
        assert tools[0].name == "ns_my_tool"

    async def test_namespace_prefixes_prompt_names(self):
        """Test that namespace is applied as prefix to prompt names."""
        server = FastMCP("Test")

        @server.prompt
        def my_prompt() -> str:
            return "prompt content"

        provider = FastMCPProvider(server).with_namespace("ns")
        prompts = await provider.list_prompts()

        assert len(prompts) == 1
        assert prompts[0].name == "ns_my_prompt"

    async def test_namespace_prefixes_resource_uris(self):
        """Test that namespace is inserted into resource URIs."""
        server = FastMCP("Test")

        @server.resource("resource://data")
        def my_resource() -> str:
            return "content"

        provider = FastMCPProvider(server).with_namespace("ns")
        resources = await provider.list_resources()

        assert len(resources) == 1
        assert str(resources[0].uri) == "resource://ns/data"

    async def test_namespace_prefixes_template_uris(self):
        """Test that namespace is inserted into resource template URIs."""
        server = FastMCP("Test")

        @server.resource("resource://{name}/data")
        def my_template(name: str) -> str:
            return f"content for {name}"

        provider = FastMCPProvider(server).with_namespace("ns")
        templates = await provider.list_resource_templates()

        assert len(templates) == 1
        assert templates[0].uri_template == "resource://ns/{name}/data"


class TestToolRenames:
    """Test tool renaming functionality."""

    async def test_tool_rename_bypasses_namespace(self):
        """Test that explicit renames bypass namespace prefixing."""
        server = FastMCP("Test")

        @server.tool
        def verbose_tool_name() -> str:
            return "result"

        provider = FastMCPProvider(server).with_transforms(
            namespace="ns",
            tool_renames={"verbose_tool_name": "short"},
        )
        tools = await provider.list_tools()

        assert len(tools) == 1
        assert tools[0].name == "short"

    async def test_tool_rename_without_namespace(self):
        """Test tool renaming works without namespace."""
        server = FastMCP("Test")

        @server.tool
        def old_name() -> str:
            return "result"

        provider = FastMCPProvider(server).with_transforms(
            tool_renames={"old_name": "new_name"},
        )
        tools = await provider.list_tools()

        assert len(tools) == 1
        assert tools[0].name == "new_name"

    async def test_renamed_tool_is_callable(self):
        """Test that renamed tools can be called by new name."""
        sub = FastMCP("Sub")

        @sub.tool
        def original() -> str:
            return "success"

        main = FastMCP("Main")
        main._providers.append(
            FastMCPProvider(sub).with_transforms(
                tool_renames={"original": "renamed"},
            )
        )

        async with Client(main) as client:
            result = await client.call_tool("renamed", {})
            assert result.data == "success"

    async def test_duplicate_rename_targets_raises_error(self):
        """Test that duplicate target names in tool_renames raises ValueError."""
        server = FastMCP("Test")
        base_provider = FastMCPProvider(server)

        with pytest.raises(ValueError, match="duplicate target name"):
            base_provider.with_transforms(
                tool_renames={"tool_a": "same", "tool_b": "same"},
            )


class TestReverseTransformation:
    """Test reverse lookups for routing."""

    async def test_reverse_tool_lookup_with_namespace(self):
        """Test that tools can be looked up by transformed name."""
        server = FastMCP("Test")

        @server.tool
        def my_tool() -> str:
            return "result"

        provider = FastMCPProvider(server).with_namespace("ns")
        tool = await provider.get_tool("ns_my_tool")

        assert tool is not None
        assert tool.name == "ns_my_tool"

    async def test_reverse_tool_lookup_with_rename(self):
        """Test that renamed tools can be looked up by new name."""
        server = FastMCP("Test")

        @server.tool
        def original() -> str:
            return "result"

        provider = FastMCPProvider(server).with_transforms(
            tool_renames={"original": "renamed"},
        )
        tool = await provider.get_tool("renamed")

        assert tool is not None
        assert tool.name == "renamed"

    async def test_reverse_resource_lookup_with_namespace(self):
        """Test that resources can be looked up by transformed URI."""
        server = FastMCP("Test")

        @server.resource("resource://data")
        def my_resource() -> str:
            return "content"

        provider = FastMCPProvider(server).with_namespace("ns")
        resource = await provider.get_resource("resource://ns/data")

        assert resource is not None
        assert str(resource.uri) == "resource://ns/data"

    async def test_nonmatching_namespace_returns_none(self):
        """Test that lookups with wrong namespace return None."""
        server = FastMCP("Test")

        @server.tool
        def my_tool() -> str:
            return "result"

        provider = FastMCPProvider(server).with_namespace("ns")

        # Wrong namespace prefix
        assert await provider.get_tool("wrong_my_tool") is None
        # No prefix at all
        assert await provider.get_tool("my_tool") is None


class TestTransformStacking:
    """Test stacking multiple transformations."""

    async def test_stacked_namespaces_compose(self):
        """Test that stacked namespaces are applied in order."""
        server = FastMCP("Test")

        @server.tool
        def my_tool() -> str:
            return "result"

        provider = (
            FastMCPProvider(server).with_namespace("inner").with_namespace("outer")
        )
        tools = await provider.list_tools()

        assert len(tools) == 1
        assert tools[0].name == "outer_inner_my_tool"

    async def test_stacked_rename_after_namespace(self):
        """Test renaming a namespaced tool."""
        server = FastMCP("Test")

        @server.tool
        def my_tool() -> str:
            return "result"

        provider = (
            FastMCPProvider(server)
            .with_namespace("ns")
            .with_transforms(tool_renames={"ns_my_tool": "short"})
        )
        tools = await provider.list_tools()

        assert len(tools) == 1
        assert tools[0].name == "short"

    async def test_stacked_transforms_are_callable(self):
        """Test that stacked transforms still allow tool calls."""
        sub = FastMCP("Sub")

        @sub.tool
        def my_tool() -> str:
            return "success"

        main = FastMCP("Main")
        main._providers.append(
            FastMCPProvider(sub)
            .with_namespace("ns")
            .with_transforms(tool_renames={"ns_my_tool": "short"})
        )

        async with Client(main) as client:
            result = await client.call_tool("short", {})
            assert result.data == "success"


class TestNoTransformation:
    """Test behavior when no transformations are applied."""

    async def test_no_namespace_passthrough(self):
        """Test that tools pass through unchanged without namespace."""
        server = FastMCP("Test")

        @server.tool
        def my_tool() -> str:
            return "result"

        provider = TransformingProvider(FastMCPProvider(server))
        tools = await provider.list_tools()

        assert len(tools) == 1
        assert tools[0].name == "my_tool"

    async def test_empty_tool_renames_passthrough(self):
        """Test that empty tool_renames has no effect."""
        server = FastMCP("Test")

        @server.tool
        def my_tool() -> str:
            return "result"

        provider = FastMCPProvider(server).with_transforms(tool_renames={})
        tools = await provider.list_tools()

        assert len(tools) == 1
        assert tools[0].name == "my_tool"
