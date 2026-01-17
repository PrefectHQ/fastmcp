"""Tests for tool injection middleware."""

import math

import pytest
from inline_snapshot import snapshot
from mcp.types import TextContent
from mcp.types import Tool as SDKTool

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.client import CallToolResult
from fastmcp.client.transports import FastMCPTransport
from fastmcp.server.middleware.tool_injection import (
    PromptToolMiddleware,
    ResourceToolMiddleware,
    ToolInjectionMiddleware,
)
from fastmcp.tools.function_tool import FunctionTool
from fastmcp.tools.tool import Tool


def multiply_fn(a: int, b: int) -> int:
    """Multiply two numbers."""
    return a * b


def divide_fn(a: int, b: int) -> float:
    """Divide two numbers."""
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b


multiply_tool = Tool.from_function(fn=multiply_fn, name="multiply", tags={"math"})
divide_tool = Tool.from_function(fn=divide_fn, name="divide", tags={"math"})


class TestToolInjectionMiddleware:
    """Tests with real FastMCP server."""

    @pytest.fixture
    def base_server(self):
        """Create a base FastMCP server."""
        mcp = FastMCP("BaseServer")

        @mcp.tool
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        @mcp.tool
        def subtract(a: int, b: int) -> int:
            """Subtract two numbers."""
            return a - b

        return mcp

    async def test_list_tools_includes_injected_tools(self, base_server: FastMCP):
        """Test that list_tools returns both base and injected tools."""

        injected_tools: list[FunctionTool] = [
            multiply_tool,
            divide_tool,
        ]
        middleware: ToolInjectionMiddleware = ToolInjectionMiddleware(
            tools=injected_tools
        )
        base_server.add_middleware(middleware)

        async with Client[FastMCPTransport](base_server) as client:
            tools: list[SDKTool] = await client.list_tools()

        # Should have all tools: multiply, divide, add, subtract
        assert len(tools) == 4
        tool_names: list[str] = [tool.name for tool in tools]
        assert "multiply" in tool_names
        assert "divide" in tool_names
        assert "add" in tool_names
        assert "subtract" in tool_names

    async def test_call_injected_tool(self, base_server: FastMCP):
        """Test that injected tools can be called successfully."""

        injected_tools: list[FunctionTool] = [multiply_tool]
        middleware: ToolInjectionMiddleware = ToolInjectionMiddleware(
            tools=injected_tools
        )
        base_server.add_middleware(middleware)

        async with Client[FastMCPTransport](base_server) as client:
            result: CallToolResult = await client.call_tool(
                name="multiply", arguments={"a": 7, "b": 6}
            )

        assert result.structured_content is not None
        assert isinstance(result.structured_content, dict)
        assert result.structured_content["result"] == 42

    async def test_call_base_tool_still_works(self, base_server: FastMCP):
        """Test that base server tools still work after injecting tools."""

        injected_tools: list[FunctionTool] = [multiply_tool]
        middleware: ToolInjectionMiddleware = ToolInjectionMiddleware(
            tools=injected_tools
        )
        base_server.add_middleware(middleware)

        async with Client[FastMCPTransport](base_server) as client:
            result: CallToolResult = await client.call_tool(
                name="add", arguments={"a": 10, "b": 5}
            )

        assert result.structured_content is not None
        assert isinstance(result.structured_content, dict)
        assert result.structured_content["result"] == 15

    async def test_injected_tool_error_handling(self, base_server: FastMCP):
        """Test that errors in injected tools are properly handled."""

        injected_tools: list[FunctionTool] = [divide_tool]
        middleware: ToolInjectionMiddleware = ToolInjectionMiddleware(
            tools=injected_tools
        )
        base_server.add_middleware(middleware)

        async with Client[FastMCPTransport](base_server) as client:
            with pytest.raises(Exception, match="Cannot divide by zero"):
                _ = await client.call_tool(name="divide", arguments={"a": 10, "b": 0})

    async def test_multiple_tool_injections(self, base_server: FastMCP):
        """Test multiple tool injection middlewares can be stacked."""

        def power(a: int, b: int) -> int:
            """Raise a to the power of b."""
            return int(math.pow(float(a), float(b)))

        def modulo(a: int, b: int) -> int:
            """Calculate a modulo b."""
            return a % b

        middleware1 = ToolInjectionMiddleware(
            tools=[Tool.from_function(fn=power, name="power")]
        )
        middleware2 = ToolInjectionMiddleware(
            tools=[Tool.from_function(fn=modulo, name="modulo")]
        )

        base_server.add_middleware(middleware1)
        base_server.add_middleware(middleware2)

        async with Client(base_server) as client:
            tools = await client.list_tools()

        # Should have all tools
        assert len(tools) == 4
        tool_names = [tool.name for tool in tools]
        assert "power" in tool_names
        assert "modulo" in tool_names
        assert "add" in tool_names
        assert "subtract" in tool_names

        # Test that both injected tools work
        async with Client(base_server) as client:
            power_result = await client.call_tool("power", {"a": 2, "b": 3})
            assert power_result.structured_content is not None
            assert isinstance(power_result.structured_content, dict)
            assert power_result.structured_content["result"] == 8

            modulo_result = await client.call_tool("modulo", {"a": 10, "b": 3})
            assert modulo_result.structured_content is not None
            assert isinstance(modulo_result.structured_content, dict)
            assert modulo_result.structured_content["result"] == 1

    async def test_injected_tool_with_complex_return_type(self, base_server: FastMCP):
        """Test injected tools with complex return types."""

        def calculate_stats(numbers: list[int]) -> dict[str, int | float]:
            """Calculate statistics for a list of numbers."""
            return {
                "sum": sum(numbers),
                "average": sum(numbers) / len(numbers),
                "min": min(numbers),
                "max": max(numbers),
                "count": len(numbers),
            }

        middleware = ToolInjectionMiddleware(
            tools=[Tool.from_function(fn=calculate_stats, name="calculate_stats")]
        )
        base_server.add_middleware(middleware)

        async with Client(base_server) as client:
            result = await client.call_tool(
                "calculate_stats", {"numbers": [1, 2, 3, 4, 5]}
            )

        assert result.structured_content is not None

        assert isinstance(result.structured_content, dict)

        assert result.structured_content == snapshot(
            {"sum": 15, "average": 3.0, "min": 1, "max": 5, "count": 5}
        )

    async def test_injected_tool_metadata_preserved(self, base_server: FastMCP):
        """Test that injected tool metadata is preserved."""

        def multiply(a: int, b: int) -> int:
            """Multiply two numbers."""
            return a * b

        injected_tools = [Tool.from_function(fn=multiply, name="multiply")]
        middleware = ToolInjectionMiddleware(tools=injected_tools)
        base_server.add_middleware(middleware)

        async with Client(base_server) as client:
            tools = await client.list_tools()

        multiply_tool = next(t for t in tools if t.name == "multiply")
        assert multiply_tool.description == "Multiply two numbers."
        assert "a" in multiply_tool.inputSchema["properties"]
        assert "b" in multiply_tool.inputSchema["properties"]

    async def test_injected_tool_does_not_conflict_with_base_tool(
        self, base_server: FastMCP
    ):
        """Test that injected tools with same name as base tools are called correctly."""

        def add(a: int, b: int) -> int:
            """Injected add that multiplies instead."""
            return a * b

        middleware: ToolInjectionMiddleware = ToolInjectionMiddleware(
            tools=[Tool.from_function(fn=add, name="add")]
        )
        base_server.add_middleware(middleware)

        async with Client[FastMCPTransport](base_server) as client:
            result: CallToolResult = await client.call_tool(
                name="add", arguments={"a": 5, "b": 3}
            )

        # Should use the injected tool (multiply behavior)
        assert result.structured_content is not None
        assert result.structured_content["result"] == 15

    async def test_injected_tool_bypass_filtering(self, base_server: FastMCP):
        """Test that injected tools bypass filtering."""
        middleware: ToolInjectionMiddleware = ToolInjectionMiddleware(
            tools=[multiply_tool]
        )
        base_server.add_middleware(middleware)
        base_server.disable(tags={"math"})

        async with Client[FastMCPTransport](base_server) as client:
            tools: list[SDKTool] = await client.list_tools()
            tool_names: list[str] = [tool.name for tool in tools]
            assert "multiply" in tool_names

    async def test_empty_tool_injection(self, base_server: FastMCP):
        """Test that middleware with no tools doesn't affect behavior."""
        middleware: ToolInjectionMiddleware = ToolInjectionMiddleware(tools=[])
        base_server.add_middleware(middleware)

        async with Client[FastMCPTransport](base_server) as client:
            tools: list[SDKTool] = await client.list_tools()
            result: CallToolResult = await client.call_tool(
                name="add", arguments={"a": 3, "b": 4}
            )

        # Should only have the base tools
        assert len(tools) == 2
        tool_names: list[str] = [tool.name for tool in tools]
        assert "add" in tool_names
        assert "subtract" in tool_names
        assert result.structured_content is not None
        assert isinstance(result.structured_content, dict)
        assert result.structured_content["result"] == 7


class TestPromptToolMiddleware:
    """Tests for PromptToolMiddleware."""

    @pytest.fixture
    def server_with_prompts(self):
        """Create a FastMCP server with prompts."""
        mcp = FastMCP("PromptServer")

        @mcp.tool
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        @mcp.prompt
        def greeting(name: str) -> str:
            """Generate a greeting message."""
            return f"Hello, {name}!"

        @mcp.prompt
        def farewell(name: str) -> str:
            """Generate a farewell message."""
            return f"Goodbye, {name}!"

        return mcp

    async def test_prompt_tools_added_to_list(self, server_with_prompts: FastMCP):
        """Test that prompt tools are added to the tool list."""
        middleware = PromptToolMiddleware()
        server_with_prompts.add_middleware(middleware)

        async with Client[FastMCPTransport](server_with_prompts) as client:
            tools: list[SDKTool] = await client.list_tools()

        tool_names: list[str] = [tool.name for tool in tools]
        # Should have: add, list_prompts, get_prompt
        assert len(tools) == 3
        assert "add" in tool_names
        assert "list_prompts" in tool_names
        assert "get_prompt" in tool_names

    async def test_list_prompts_tool_works(self, server_with_prompts: FastMCP):
        """Test that the list_prompts tool can be called."""
        middleware = PromptToolMiddleware()
        server_with_prompts.add_middleware(middleware)

        async with Client[FastMCPTransport](server_with_prompts) as client:
            result: CallToolResult = await client.call_tool(
                name="list_prompts", arguments={}
            )

        assert result.content == snapshot(
            [
                TextContent(
                    type="text",
                    text='[{"name":"greeting","title":null,"description":"Generate a greeting message.","arguments":[{"name":"name","description":null,"required":true}],"icons":null,"_meta":{"fastmcp":{"tags":[]}}},{"name":"farewell","title":null,"description":"Generate a farewell message.","arguments":[{"name":"name","description":null,"required":true}],"icons":null,"_meta":{"fastmcp":{"tags":[]}}}]',
                )
            ]
        )
        assert result.structured_content is not None
        assert result.structured_content["result"] == snapshot(
            [
                {
                    "name": "greeting",
                    "title": None,
                    "description": "Generate a greeting message.",
                    "arguments": [
                        {"name": "name", "description": None, "required": True}
                    ],
                    "icons": None,
                    "_meta": {"fastmcp": {"tags": []}},
                },
                {
                    "name": "farewell",
                    "title": None,
                    "description": "Generate a farewell message.",
                    "arguments": [
                        {"name": "name", "description": None, "required": True}
                    ],
                    "icons": None,
                    "_meta": {"fastmcp": {"tags": []}},
                },
            ]
        )

    async def test_get_prompt_tool_works(self, server_with_prompts: FastMCP):
        """Test that the get_prompt tool can be called."""
        middleware = PromptToolMiddleware()
        server_with_prompts.add_middleware(middleware)

        async with Client[FastMCPTransport](server_with_prompts) as client:
            result: CallToolResult = await client.call_tool(
                name="get_prompt",
                arguments={"name": "greeting", "arguments": {"name": "World"}},
            )

        # The tool returns the prompt result with structured_content
        assert result.content == snapshot(
            [
                TextContent(
                    type="text",
                    text='{"_meta":null,"description":"Generate a greeting message.","messages":[{"role":"user","content":{"type":"text","text":"Hello, World!","annotations":null,"_meta":null}}]}',
                )
            ]
        )
        assert result.structured_content is not None
        assert result.structured_content == snapshot(
            {
                "_meta": None,
                "description": "Generate a greeting message.",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": "Hello, World!",
                            "annotations": None,
                            "_meta": None,
                        },
                    }
                ],
            }
        )


class TestResourceToolMiddleware:
    """Tests for ResourceToolMiddleware."""

    @pytest.fixture
    def server_with_resources(self):
        """Create a FastMCP server with resources."""
        mcp = FastMCP("ResourceServer")

        @mcp.tool
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        @mcp.resource("file://config.txt")
        def config_resource() -> str:
            """Get configuration."""
            return "debug=true"

        @mcp.resource("file://data.json")
        def data_resource() -> str:
            """Get data."""
            return '{"count": 42}'

        return mcp

    async def test_resource_tools_added_to_list(self, server_with_resources: FastMCP):
        """Test that resource tools are added to the tool list."""
        middleware = ResourceToolMiddleware()
        server_with_resources.add_middleware(middleware)

        async with Client[FastMCPTransport](server_with_resources) as client:
            tools: list[SDKTool] = await client.list_tools()

        tool_names: list[str] = [tool.name for tool in tools]
        # Should have: add, list_resources, read_resource
        assert len(tools) == 3
        assert "add" in tool_names
        assert "list_resources" in tool_names
        assert "read_resource" in tool_names

    async def test_list_resources_tool_works(self, server_with_resources: FastMCP):
        """Test that the list_resources tool can be called."""
        middleware = ResourceToolMiddleware()
        server_with_resources.add_middleware(middleware)

        async with Client[FastMCPTransport](server_with_resources) as client:
            result: CallToolResult = await client.call_tool(
                name="list_resources", arguments={}
            )

        assert result.structured_content is not None
        assert result.structured_content["result"] == snapshot(
            [
                {
                    "name": "config_resource",
                    "title": None,
                    "uri": "file://config.txt/",
                    "description": "Get configuration.",
                    "mimeType": "text/plain",
                    "size": None,
                    "icons": None,
                    "annotations": None,
                    "_meta": {"fastmcp": {"tags": []}},
                },
                {
                    "name": "data_resource",
                    "title": None,
                    "uri": "file://data.json/",
                    "description": "Get data.",
                    "mimeType": "text/plain",
                    "size": None,
                    "icons": None,
                    "annotations": None,
                    "_meta": {"fastmcp": {"tags": []}},
                },
            ]
        )

    async def test_read_resource_tool_works(self, server_with_resources: FastMCP):
        """Test that the read_resource tool can be called."""
        middleware = ResourceToolMiddleware()
        server_with_resources.add_middleware(middleware)

        async with Client[FastMCPTransport](server_with_resources) as client:
            result: CallToolResult = await client.call_tool(
                name="read_resource", arguments={"uri": "file://config.txt"}
            )

        assert result.content == snapshot(
            [
                TextContent(
                    type="text",
                    text='{"contents":[{"content":"debug=true","mime_type":"text/plain","meta":null}],"meta":null}',
                )
            ]
        )
        assert result.structured_content == snapshot(
            {
                "contents": [
                    {"content": "debug=true", "mime_type": "text/plain", "meta": None}
                ],
                "meta": None,
            }
        )
