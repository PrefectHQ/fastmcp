"""Tests for providers."""

from collections.abc import Sequence
from typing import Any

import pytest
from mcp.types import AnyUrl, PromptMessage, TextContent
from mcp.types import Tool as MCPTool

from fastmcp import FastMCP, Provider
from fastmcp.client import Client
from fastmcp.client.client import CallToolResult
from fastmcp.prompts.prompt import FunctionPrompt, Prompt, PromptResult
from fastmcp.resources.resource import FunctionResource, Resource, ResourceContent
from fastmcp.resources.template import FunctionResourceTemplate, ResourceTemplate
from fastmcp.server.context import Context
from fastmcp.tools.tool import Tool, ToolResult


class SimpleTool(Tool):
    """A simple tool for testing that performs a configured operation."""

    operation: str
    value: int = 0

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        a = arguments.get("a", 0)
        b = arguments.get("b", 0)

        if self.operation == "add":
            result = a + b + self.value
        elif self.operation == "multiply":
            result = a * b + self.value
        else:
            result = a + b

        return ToolResult(
            structured_content={"result": result, "operation": self.operation}
        )


class SimpleToolProvider(Provider):
    """A simple provider that returns a configurable list of tools."""

    def __init__(self, tools: list[Tool] | None = None):
        self._tools = tools or []
        self.list_tools_call_count = 0
        self.get_tool_call_count = 0

    async def list_tools(self, context: Context) -> list[Tool]:
        self.list_tools_call_count += 1
        return self._tools

    async def get_tool(self, context: Context, name: str) -> Tool | None:
        self.get_tool_call_count += 1
        return next((t for t in self._tools if t.name == name), None)


class ListOnlyProvider(Provider):
    """A provider that only implements list_tools (uses default get_tool)."""

    def __init__(self, tools: list[Tool]):
        self._tools = tools
        self.list_tools_call_count = 0

    async def list_tools(self, context: Context) -> list[Tool]:
        self.list_tools_call_count += 1
        return self._tools


class TestProvider:
    """Tests for Provider."""

    @pytest.fixture
    def base_server(self):
        """Create a base FastMCP server with static tools."""
        mcp = FastMCP("BaseServer")

        @mcp.tool
        def static_add(a: int, b: int) -> int:
            """Add two numbers (static tool)."""
            return a + b

        @mcp.tool
        def static_subtract(a: int, b: int) -> int:
            """Subtract two numbers (static tool)."""
            return a - b

        return mcp

    @pytest.fixture
    def dynamic_tools(self) -> list[Tool]:
        """Create dynamic tools for testing."""
        return [
            SimpleTool(
                name="dynamic_multiply",
                description="Multiply two numbers",
                parameters={
                    "type": "object",
                    "properties": {
                        "a": {"type": "integer"},
                        "b": {"type": "integer"},
                    },
                },
                operation="multiply",
            ),
            SimpleTool(
                name="dynamic_add",
                description="Add two numbers with offset",
                parameters={
                    "type": "object",
                    "properties": {
                        "a": {"type": "integer"},
                        "b": {"type": "integer"},
                    },
                },
                operation="add",
                value=100,
            ),
        ]

    async def test_list_tools_includes_dynamic_tools(
        self, base_server: FastMCP, dynamic_tools: list[Tool]
    ):
        """Test that list_tools returns both static and dynamic tools."""
        provider = SimpleToolProvider(tools=dynamic_tools)
        base_server.add_provider(provider)

        async with Client(base_server) as client:
            tools: list[MCPTool] = await client.list_tools()

        # Should have all tools: 2 static + 2 dynamic
        assert len(tools) == 4
        tool_names = [tool.name for tool in tools]
        assert "static_add" in tool_names
        assert "static_subtract" in tool_names
        assert "dynamic_multiply" in tool_names
        assert "dynamic_add" in tool_names

    async def test_list_tools_calls_provider_each_time(
        self, base_server: FastMCP, dynamic_tools: list[Tool]
    ):
        """Test that provider.list_tools() is called on every list_tools request."""
        provider = SimpleToolProvider(tools=dynamic_tools)
        base_server.add_provider(provider)

        async with Client(base_server) as client:
            # Call list_tools multiple times
            await client.list_tools()
            await client.list_tools()
            await client.list_tools()

        # Provider should have been called 3 times
        assert provider.list_tools_call_count == 3

    async def test_call_dynamic_tool(
        self, base_server: FastMCP, dynamic_tools: list[Tool]
    ):
        """Test that dynamic tools can be called successfully."""
        provider = SimpleToolProvider(tools=dynamic_tools)
        base_server.add_provider(provider)

        async with Client(base_server) as client:
            result: CallToolResult = await client.call_tool(
                name="dynamic_multiply", arguments={"a": 7, "b": 6}
            )

        assert result.structured_content is not None
        assert result.structured_content["result"] == 42  # type: ignore[attr-defined]
        assert result.structured_content["operation"] == "multiply"  # type: ignore[attr-defined]

    async def test_call_dynamic_tool_with_config(
        self, base_server: FastMCP, dynamic_tools: list[Tool]
    ):
        """Test that dynamic tool config (like value offset) is used."""
        provider = SimpleToolProvider(tools=dynamic_tools)
        base_server.add_provider(provider)

        async with Client(base_server) as client:
            result: CallToolResult = await client.call_tool(
                name="dynamic_add", arguments={"a": 5, "b": 3}
            )

        assert result.structured_content is not None
        # 5 + 3 + 100 (value offset) = 108
        assert result.structured_content["result"] == 108  # type: ignore[attr-defined]

    async def test_call_static_tool_still_works(
        self, base_server: FastMCP, dynamic_tools: list[Tool]
    ):
        """Test that static tools still work after adding dynamic tools."""
        provider = SimpleToolProvider(tools=dynamic_tools)
        base_server.add_provider(provider)

        async with Client(base_server) as client:
            result: CallToolResult = await client.call_tool(
                name="static_add", arguments={"a": 10, "b": 5}
            )

        assert result.structured_content is not None
        assert result.structured_content["result"] == 15  # type: ignore[attr-defined]

    async def test_call_tool_uses_get_tool_for_efficient_lookup(
        self, base_server: FastMCP, dynamic_tools: list[Tool]
    ):
        """Test that call_tool uses get_tool() for efficient single-tool lookup."""
        provider = SimpleToolProvider(tools=dynamic_tools)
        base_server.add_provider(provider)

        async with Client(base_server) as client:
            await client.call_tool(name="dynamic_multiply", arguments={"a": 2, "b": 3})

        # get_tool is called twice:
        # 1. Server calls get_tool() to check _should_enable_component filter
        # 2. Default call_tool() implementation calls get_tool() internally
        # Key point: list_tools is NOT called during tool execution (efficient lookup)
        assert provider.get_tool_call_count == 2

    async def test_default_get_tool_falls_back_to_list(self, base_server: FastMCP):
        """Test that BaseToolProvider's default get_tool calls list_tools."""
        tools = [
            SimpleTool(
                name="test_tool",
                description="A test tool",
                parameters={"type": "object", "properties": {}},
                operation="add",
            ),
        ]
        provider = ListOnlyProvider(tools=tools)
        base_server.add_provider(provider)

        async with Client(base_server) as client:
            result = await client.call_tool(
                name="test_tool", arguments={"a": 1, "b": 2}
            )

        assert result.structured_content is not None
        # Default get_tool should have called list_tools
        assert provider.list_tools_call_count >= 1

    async def test_dynamic_tools_come_first(
        self, base_server: FastMCP, dynamic_tools: list[Tool]
    ):
        """Test that dynamic tools appear before static tools in list."""
        provider = SimpleToolProvider(tools=dynamic_tools)
        base_server.add_provider(provider)

        async with Client(base_server) as client:
            tools: list[MCPTool] = await client.list_tools()

        tool_names = [tool.name for tool in tools]
        # Dynamic tools should come first
        assert tool_names[:2] == ["dynamic_multiply", "dynamic_add"]

    async def test_empty_provider(self, base_server: FastMCP):
        """Test that empty provider doesn't affect behavior."""
        provider = SimpleToolProvider(tools=[])
        base_server.add_provider(provider)

        async with Client(base_server) as client:
            tools: list[MCPTool] = await client.list_tools()

        # Should only have static tools
        assert len(tools) == 2

    async def test_tool_not_found_falls_through_to_static(
        self, base_server: FastMCP, dynamic_tools: list[Tool]
    ):
        """Test that unknown tool name falls through to static tools."""
        provider = SimpleToolProvider(tools=dynamic_tools)
        base_server.add_provider(provider)

        async with Client(base_server) as client:
            # This tool is static, not in the dynamic provider
            result: CallToolResult = await client.call_tool(
                name="static_subtract", arguments={"a": 10, "b": 3}
            )

        assert result.structured_content is not None
        assert result.structured_content["result"] == 7  # type: ignore[attr-defined]


class TestProviderClass:
    """Tests for the Provider class."""

    async def test_subclass_is_instance(self):
        """Test that subclasses are instances of Provider."""
        provider = SimpleToolProvider(tools=[])
        assert isinstance(provider, Provider)

    async def test_default_get_tool_works(self):
        """Test that the default get_tool implementation works."""
        tool = SimpleTool(
            name="test",
            description="Test",
            parameters={"type": "object", "properties": {}},
            operation="add",
        )
        provider = ListOnlyProvider(tools=[tool])

        # Create a context for direct testing
        mcp = FastMCP("TestServer")
        ctx = Context(mcp)

        # Default get_tool should find by name
        found = await provider.get_tool(ctx, "test")
        assert found is not None
        assert found.name == "test"

        # Should return None for unknown names
        not_found = await provider.get_tool(ctx, "unknown")
        assert not_found is None


class TestDynamicToolUpdates:
    """Tests demonstrating dynamic tool updates without restart."""

    async def test_tools_update_without_restart(self):
        """Test that tools can be updated dynamically."""
        mcp = FastMCP("DynamicServer")

        # Start with one tool
        initial_tools = [
            SimpleTool(
                name="tool_v1",
                description="Version 1",
                parameters={"type": "object", "properties": {}},
                operation="add",
            ),
        ]
        provider = SimpleToolProvider(tools=initial_tools)
        mcp.add_provider(provider)

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert len(tools) == 1
            assert tools[0].name == "tool_v1"

            # Update the provider's tools (simulating DB update)
            provider._tools = [
                SimpleTool(
                    name="tool_v2",
                    description="Version 2",
                    parameters={"type": "object", "properties": {}},
                    operation="multiply",
                ),
                SimpleTool(
                    name="tool_v3",
                    description="Version 3",
                    parameters={"type": "object", "properties": {}},
                    operation="add",
                ),
            ]

            # List tools again - should see new tools
            tools = await client.list_tools()
            assert len(tools) == 2
            tool_names = [t.name for t in tools]
            assert "tool_v1" not in tool_names
            assert "tool_v2" in tool_names
            assert "tool_v3" in tool_names


class TestProviderExecutionMethods:
    """Tests for Provider execution methods (call_tool, read_resource, render_prompt)."""

    async def test_call_tool_default_implementation(self):
        """Test that default call_tool uses get_tool and runs the tool."""
        tool = SimpleTool(
            name="test_tool",
            description="Test",
            parameters={"type": "object", "properties": {"a": {}, "b": {}}},
            operation="add",
        )
        provider = SimpleToolProvider(tools=[tool])
        mcp = FastMCP("TestServer")
        mcp.add_provider(provider)

        async with Client(mcp) as client:
            result = await client.call_tool("test_tool", {"a": 1, "b": 2})

        assert result.structured_content is not None
        assert result.structured_content["result"] == 3  # type: ignore[attr-defined]

    async def test_call_tool_custom_implementation(self):
        """Test that providers can override call_tool for custom behavior."""

        class CustomCallProvider(Provider):
            """Provider that wraps tool execution with custom logic."""

            def __init__(self):
                self.call_count = 0
                self._tool = SimpleTool(
                    name="custom_tool",
                    description="Test",
                    parameters={"type": "object", "properties": {"a": {}, "b": {}}},
                    operation="add",
                )

            async def list_tools(self, context: Context) -> Sequence[Tool]:
                return [self._tool]

            async def get_tool(self, context: Context, name: str) -> Tool | None:
                if name == "custom_tool":
                    return self._tool
                return None

            async def call_tool(
                self, context: Context, name: str, arguments: dict[str, Any]
            ) -> ToolResult | None:
                # Custom behavior: track calls and modify result
                self.call_count += 1
                tool = await self.get_tool(context, name)
                if tool is None:
                    return None
                result = await tool.run(arguments)
                # Add custom metadata to result
                result.structured_content["custom_wrapper"] = True  # type: ignore[index]
                return result

        provider = CustomCallProvider()
        mcp = FastMCP("TestServer")
        mcp.add_provider(provider)

        async with Client(mcp) as client:
            result = await client.call_tool("custom_tool", {"a": 5, "b": 3})

        assert provider.call_count == 1
        assert result.structured_content is not None
        assert result.structured_content["result"] == 8  # type: ignore[attr-defined]
        assert result.structured_content["custom_wrapper"] is True  # type: ignore[attr-defined]

    async def test_read_resource_default_implementation(self):
        """Test that default read_resource uses get_resource and reads it."""

        class ResourceProvider(Provider):
            async def list_resources(self, context: Context) -> Sequence[Resource]:
                return [
                    FunctionResource(
                        uri=AnyUrl("test://data"),
                        name="Test Data",
                        fn=lambda: "hello world",
                    )
                ]

        provider = ResourceProvider()
        mcp = FastMCP("TestServer")
        mcp.add_provider(provider)

        async with Client(mcp) as client:
            result = await client.read_resource("test://data")

        assert len(result) == 1
        assert result[0].text == "hello world"

    async def test_read_resource_custom_implementation(self):
        """Test that providers can override read_resource for custom behavior."""

        class CustomReadProvider(Provider):
            """Provider that transforms resource content."""

            async def list_resources(self, context: Context) -> Sequence[Resource]:
                return [
                    FunctionResource(
                        uri=AnyUrl("test://data"),
                        name="Test Data",
                        fn=lambda: "original",
                    )
                ]

            async def read_resource(
                self, context: Context, uri: str
            ) -> ResourceContent | None:
                if uri == "test://data":
                    # Custom behavior: return transformed content
                    return ResourceContent(content="TRANSFORMED")
                return None

        provider = CustomReadProvider()
        mcp = FastMCP("TestServer")
        mcp.add_provider(provider)

        async with Client(mcp) as client:
            result = await client.read_resource("test://data")

        assert len(result) == 1
        assert result[0].text == "TRANSFORMED"

    async def test_read_resource_template_default(self):
        """Test that read_resource_template handles template-based resources."""

        class TemplateProvider(Provider):
            async def list_resource_templates(
                self, context: Context
            ) -> Sequence[ResourceTemplate]:
                return [
                    FunctionResourceTemplate.from_function(
                        fn=lambda name: f"content of {name}",
                        uri_template="data://files/{name}",
                        name="Data Template",
                    )
                ]

        provider = TemplateProvider()
        mcp = FastMCP("TestServer")
        mcp.add_provider(provider)

        async with Client(mcp) as client:
            result = await client.read_resource("data://files/test.txt")

        assert len(result) == 1
        assert result[0].text == "content of test.txt"

    async def test_render_prompt_default_implementation(self):
        """Test that default render_prompt uses get_prompt and renders it."""

        class PromptProvider(Provider):
            async def list_prompts(self, context: Context) -> Sequence[Prompt]:
                return [
                    FunctionPrompt.from_function(
                        fn=lambda name: f"Hello, {name}!",
                        name="greeting",
                        description="Greet someone",
                    )
                ]

        provider = PromptProvider()
        mcp = FastMCP("TestServer")
        mcp.add_provider(provider)

        async with Client(mcp) as client:
            result = await client.get_prompt("greeting", {"name": "World"})

        assert len(result.messages) == 1
        assert result.messages[0].content.text == "Hello, World!"  # type: ignore[attr-defined]

    async def test_render_prompt_custom_implementation(self):
        """Test that providers can override render_prompt for custom behavior."""

        class CustomRenderProvider(Provider):
            """Provider that adds prefix to all prompts."""

            async def list_prompts(self, context: Context) -> Sequence[Prompt]:
                return [
                    FunctionPrompt.from_function(
                        fn=lambda: "original message",
                        name="test_prompt",
                        description="Test",
                    )
                ]

            async def render_prompt(
                self, context: Context, name: str, arguments: dict[str, Any] | None
            ) -> PromptResult | None:
                if name == "test_prompt":
                    # Custom behavior: add prefix
                    return PromptResult(
                        messages=[
                            PromptMessage(
                                role="user",
                                content=TextContent(
                                    type="text",
                                    text="[CUSTOM PREFIX] original message",
                                ),
                            )
                        ]
                    )
                return None

        provider = CustomRenderProvider()
        mcp = FastMCP("TestServer")
        mcp.add_provider(provider)

        async with Client(mcp) as client:
            result = await client.get_prompt("test_prompt", {})

        assert len(result.messages) == 1
        assert "[CUSTOM PREFIX]" in result.messages[0].content.text  # type: ignore[attr-defined]
