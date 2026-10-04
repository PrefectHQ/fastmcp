"""Tests for tool injection middleware."""

import math
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import pytest
from inline_snapshot import snapshot
from mcp.types import Tool as SDKTool

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.client import CallToolResult
from fastmcp.client.transports import FastMCPTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import require_scopes
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.server.middleware import AuthMiddleware, Middleware
from fastmcp.server.middleware.tool_injection import (
    ToolInjectionMiddleware,
)
from fastmcp.tools.base import Tool
from fastmcp.tools.function_tool import FunctionTool
from fastmcp.utilities.tests import run_server_async


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


TOKENS = {
    "read-token": {"client_id": "reader", "scopes": ["read"]},
    "admin-token": {"client_id": "administrator", "scopes": ["read", "admin"]},
}


def admin_report() -> str:
    """Return a marker only an admin should see."""
    return "ADMIN_REPORT_MARKER"


def public_report() -> str:
    """Return a marker anyone may see."""
    return "PUBLIC_REPORT_MARKER"


def make_auth_server(*middleware: Middleware) -> FastMCP:
    return FastMCP(
        "AuthServer",
        auth=StaticTokenVerifier(TOKENS),
        middleware=list(middleware),
    )


@asynccontextmanager
async def http_client(server: FastMCP, token: str) -> AsyncIterator[Client]:
    async with run_server_async(server) as url:
        async with Client(url, auth=token) as client:
            yield client


def scoped_admin_tool() -> Tool:
    return Tool.from_function(
        admin_report,
        name="admin_report",
        auth=require_scopes("admin"),
    )


def unscoped_tool(fn: Callable[[], str], name: str) -> Tool:
    return Tool.from_function(fn, name=name)


class TestInjectedToolAuthorization:
    """Injected tools follow the same authorization as registered tools."""

    async def test_tool_auth_hides_injected_tool_without_scope(self):
        server = make_auth_server(ToolInjectionMiddleware([scoped_admin_tool()]))

        async with http_client(server, "read-token") as client:
            tools = await client.list_tools()

        assert "admin_report" not in [tool.name for tool in tools]

    async def test_tool_auth_denies_injected_tool_call_without_scope(self):
        server = make_auth_server(ToolInjectionMiddleware([scoped_admin_tool()]))

        async with http_client(server, "read-token") as client:
            with pytest.raises(ToolError, match="Unknown tool"):
                await client.call_tool("admin_report", {})

    async def test_tool_auth_allows_injected_tool_with_scope(self):
        server = make_auth_server(ToolInjectionMiddleware([scoped_admin_tool()]))

        async with http_client(server, "admin-token") as client:
            tools = await client.list_tools()
            result = await client.call_tool("admin_report", {})

        assert "admin_report" in [tool.name for tool in tools]
        assert result.data == "ADMIN_REPORT_MARKER"

    async def test_tool_auth_on_injected_tool_matches_registered_tool(self):
        """An injected tool and a registered tool with the same auth agree."""
        registered = make_auth_server()
        registered.add_tool(scoped_admin_tool())
        injected = make_auth_server(ToolInjectionMiddleware([scoped_admin_tool()]))

        outcomes: list[tuple[list[str], bool]] = []
        for server in (registered, injected):
            async with http_client(server, "read-token") as client:
                names = [tool.name for tool in await client.list_tools()]
                result = await client.call_tool(
                    "admin_report", {}, raise_on_error=False
                )
            outcomes.append((names, result.is_error))

        assert outcomes[0] == outcomes[1] == ([], True)

    async def test_injected_tool_without_auth_is_available_to_any_token(self):
        server = make_auth_server(
            ToolInjectionMiddleware([unscoped_tool(public_report, "public_report")])
        )

        async with http_client(server, "read-token") as client:
            tools = await client.list_tools()
            result = await client.call_tool("public_report", {})

        assert [tool.name for tool in tools] == ["public_report"]
        assert result.data == "PUBLIC_REPORT_MARKER"

    @pytest.mark.parametrize("injection_first", [True, False])
    async def test_auth_middleware_denies_injected_tool(self, injection_first: bool):
        injection = ToolInjectionMiddleware(
            [unscoped_tool(admin_report, "admin_report")]
        )
        auth = AuthMiddleware(auth=require_scopes("admin"))
        order = [injection, auth] if injection_first else [auth, injection]
        server = make_auth_server(*order)

        async with http_client(server, "read-token") as client:
            tools = await client.list_tools()
            result = await client.call_tool("admin_report", {}, raise_on_error=False)

        assert tools == []
        assert result.is_error
        assert "ADMIN_REPORT_MARKER" not in str(result.content)

    @pytest.mark.parametrize("injection_first", [True, False])
    async def test_auth_middleware_allows_injected_tool(self, injection_first: bool):
        injection = ToolInjectionMiddleware(
            [unscoped_tool(admin_report, "admin_report")]
        )
        auth = AuthMiddleware(auth=require_scopes("admin"))
        order = [injection, auth] if injection_first else [auth, injection]
        server = make_auth_server(*order)

        async with http_client(server, "admin-token") as client:
            tools = await client.list_tools()
            result = await client.call_tool("admin_report", {})

        assert [tool.name for tool in tools] == ["admin_report"]
        assert result.data == "ADMIN_REPORT_MARKER"

    async def test_injected_tool_on_mounted_server_follows_tool_auth(self):
        child = FastMCP(
            "Child",
            middleware=[ToolInjectionMiddleware([scoped_admin_tool()])],
        )
        parent = make_auth_server()
        parent.mount(child, namespace="child")

        async with run_server_async(parent) as url:
            async with Client(url, auth="read-token") as client:
                read_tools = await client.list_tools()
                read_result = await client.call_tool(
                    "child_admin_report", {}, raise_on_error=False
                )
            async with Client(url, auth="admin-token") as client:
                admin_tools = await client.list_tools()
                admin_result = await client.call_tool("child_admin_report", {})

        assert read_tools == []
        assert read_result.is_error
        assert [tool.name for tool in admin_tools] == ["child_admin_report"]
        assert admin_result.data == "ADMIN_REPORT_MARKER"
