"""Tests for functools.partial support as tools.

See https://github.com/PrefectHQ/fastmcp/issues/3266
"""

import functools

from mcp.types import TextContent

from fastmcp import FastMCP
from fastmcp.tools.tool import Tool


class TestPartialTool:
    """Test tools created from functools.partial objects."""

    async def test_partial_sync(self):
        """Test that a sync functools.partial works as a tool."""

        def add(x: int, y: int) -> int:
            return x + y

        partial_add = functools.partial(add, y=10)
        functools.update_wrapper(partial_add, add)

        tool = Tool.from_function(partial_add)
        result = await tool.run({"x": 5})
        assert result.content == [TextContent(type="text", text="15")]

    async def test_partial_async(self):
        """Test that an async functools.partial works as a tool."""

        async def multiply(x: int, factor: int) -> int:
            return x * factor

        partial_mul = functools.partial(multiply, factor=3)
        functools.update_wrapper(partial_mul, multiply)

        tool = Tool.from_function(partial_mul)
        result = await tool.run({"x": 7})
        assert result.content == [TextContent(type="text", text="21")]

    async def test_partial_preserves_name(self):
        """Test that the tool name comes from the wrapped function."""

        def greet(name: str, greeting: str = "Hello") -> str:
            """Greet someone."""
            return f"{greeting}, {name}!"

        partial_greet = functools.partial(greet, greeting="Hi")
        functools.update_wrapper(partial_greet, greet)

        tool = Tool.from_function(partial_greet)
        assert tool.name == "greet"
        assert tool.description == "Greet someone."

    async def test_partial_custom_name(self):
        """Test that a custom name overrides the partial's wrapped name."""

        def compute(x: int, op: str) -> str:
            return f"{op}({x})"

        partial_fn = functools.partial(compute, op="square")
        functools.update_wrapper(partial_fn, compute)

        tool = Tool.from_function(partial_fn, name="square")
        assert tool.name == "square"

    async def test_partial_schema_shows_bound_args_as_optional(self):
        """Test that bound arguments appear as optional with default values."""

        def process(a: int, b: str, c: float = 1.0) -> str:
            return f"{a}-{b}-{c}"

        partial_fn = functools.partial(process, b="fixed")
        functools.update_wrapper(partial_fn, process)

        tool = Tool.from_function(partial_fn)
        props = tool.parameters.get("properties", {})
        required = tool.parameters.get("required", [])
        assert "a" in props
        assert "c" in props
        # b is bound by the partial so it appears as optional with its
        # bound value as the default
        assert "b" in props
        assert props["b"]["default"] == "fixed"
        assert "b" not in required

    async def test_partial_without_update_wrapper(self):
        """Test that functools.partial works without update_wrapper."""

        def add(x: int, y: int) -> int:
            return x + y

        partial_add = functools.partial(add, y=10)
        # No update_wrapper call — name comes from the partial class

        tool = Tool.from_function(partial_add, name="add_ten")
        result = await tool.run({"x": 5})
        assert result.content == [TextContent(type="text", text="15")]

    async def test_partial_with_add_tool(self):
        """Test registering a functools.partial via mcp.add_tool()."""
        mcp = FastMCP("test")

        def greet(name: str, greeting: str = "Hello") -> str:
            return f"{greeting}, {name}!"

        partial_greet = functools.partial(greet, greeting="Hey")
        functools.update_wrapper(partial_greet, greet)

        mcp.add_tool(partial_greet)

        result = await mcp.call_tool("greet", {"name": "World"})
        assert result.content == [TextContent(type="text", text="Hey, World!")]

    async def test_partial_with_server_tool_decorator(self):
        """Test registering a functools.partial via mcp.tool()."""
        mcp = FastMCP("test")

        def add(x: int, y: int) -> int:
            return x + y

        partial_add = functools.partial(add, y=100)
        functools.update_wrapper(partial_add, add)

        mcp.tool(partial_add)

        result = await mcp.call_tool("add", {"x": 5})
        assert result.content == [TextContent(type="text", text="105")]
