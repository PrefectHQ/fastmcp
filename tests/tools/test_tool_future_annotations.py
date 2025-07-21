from __future__ import annotations

from typing import Any, cast

import mcp.types
import pytest

from fastmcp import Context, FastMCP
from fastmcp.client import Client
from fastmcp.tools.tool import ToolResult
from fastmcp.utilities.types import Image

fastmcp_server = FastMCP()


@fastmcp_server.tool
def simple_with_context(ctx: Context) -> str:
    """Simple tool with context parameter."""
    return f"Request ID: {ctx.request_id}"


@fastmcp_server.tool
def complex_types(
    data: dict[str, Any], items: list[int], ctx: Context
) -> dict[str, str | int]:
    """Tool with complex type annotations."""
    return {"count": len(items), "request_id": ctx.request_id}


@fastmcp_server.tool
def optional_context(name: str, ctx: Context | None = None) -> str:
    """Tool with optional context."""
    if ctx:
        return f"Hello {name} from request {ctx.request_id}"
    return f"Hello {name}"


@fastmcp_server.tool
def union_with_context(value: int | str, ctx: Context) -> ToolResult:
    """Tool returning ToolResult with context."""
    return ToolResult(content=f"Value: {value}, Request: {ctx.request_id}")


@fastmcp_server.tool
def returns_image(ctx: Context) -> Image:
    """Tool that returns an Image."""
    # Create a simple 1x1 white pixel PNG
    png_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd4c\x00\x00\x00\x00IEND\xaeB`\x82"
    return Image(data=png_data, format="png")


@fastmcp_server.tool
async def async_with_context(ctx: Context) -> str:
    """Async tool with context."""
    return f"Async request: {ctx.request_id}"


class TestFutureAnnotations:
    async def test_simple_with_context(self):
        async with Client(fastmcp_server) as client:
            result = await client.call_tool("simple_with_context", {})
            assert "Request ID:" in cast(mcp.types.TextContent, result.content[0]).text

    async def test_complex_types(self):
        async with Client(fastmcp_server) as client:
            result = await client.call_tool(
                "complex_types", {"data": {"key": "value"}, "items": [1, 2, 3]}
            )
            # Check the result is valid JSON with expected values
            import json

            data = json.loads(cast(mcp.types.TextContent, result.content[0]).text)
            assert data["count"] == 3
            assert "request_id" in data

    async def test_optional_context(self):
        async with Client(fastmcp_server) as client:
            result = await client.call_tool("optional_context", {"name": "World"})
            assert (
                "Hello World from request"
                in cast(mcp.types.TextContent, result.content[0]).text
            )

    async def test_union_with_context(self):
        async with Client(fastmcp_server) as client:
            result = await client.call_tool("union_with_context", {"value": 42})
            assert (
                "Value: 42, Request:"
                in cast(mcp.types.TextContent, result.content[0]).text
            )

    async def test_returns_image(self):
        async with Client(fastmcp_server) as client:
            result = await client.call_tool("returns_image", {})
            assert result.content[0].type == "image"
            assert result.content[0].mimeType == "image/png"

    async def test_async_with_context(self):
        async with Client(fastmcp_server) as client:
            result = await client.call_tool("async_with_context", {})
            assert (
                "Async request:" in cast(mcp.types.TextContent, result.content[0]).text
            )

    async def test_modern_union_syntax_works(self):
        """Test that modern | union syntax works with future annotations."""
        # This demonstrates that our solution works with | syntax when types
        # are available in module globals

        # Define a tool with modern union syntax
        @fastmcp_server.tool
        def modern_union_tool(value: str | int | None) -> str | None:
            """Tool using modern | union syntax throughout."""
            if value is None:
                return None
            return f"processed: {value}"

        async with Client(fastmcp_server) as client:
            # Test with string
            result = await client.call_tool("modern_union_tool", {"value": "hello"})
            assert (
                "processed: hello"
                in cast(mcp.types.TextContent, result.content[0]).text
            )

            # Test with int
            result = await client.call_tool("modern_union_tool", {"value": 42})
            assert (
                "processed: 42" in cast(mcp.types.TextContent, result.content[0]).text
            )

            # Test with None
            result = await client.call_tool("modern_union_tool", {"value": None})
            # When function returns None, FastMCP returns empty content
            assert (
                len(result.content) == 0
                or cast(mcp.types.TextContent, result.content[0]).text == "null"
            )


@pytest.mark.xfail(
    reason="Closure-scoped types cannot be resolved with 'from __future__ import annotations'. "
    "When using future annotations, all type annotations become strings that need to be evaluated "
    "using eval() in the function's global namespace. Types defined only in closure scope "
    "(like local imports or type aliases) are not available in the function's __globals__ "
    "and therefore cannot be resolved by get_type_hints()."
)
def test_closure_scoped_types_limitation():
    """
    This test demonstrates that closure-scoped types don't work with future annotations.

    The fundamental issue is that 'from __future__ import annotations' converts all
    annotations to strings, and those strings can only be resolved using the function's
    global namespace, not local variables from closures.
    """

    def create_failing_closure():
        # This import is only available in the closure scope

        mcp = FastMCP()

        @mcp.tool
        def closure_tool(value: str | None) -> str:
            """This will fail because Optional can't be resolved from closure import."""
            return str(value)

        return mcp

    # This should raise an error during tool registration
    create_failing_closure()
