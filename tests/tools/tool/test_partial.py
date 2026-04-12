"""Tests for functools.partial support as tools, prompts, and resources.

See https://github.com/PrefectHQ/fastmcp/issues/3266
"""

import functools

from mcp.types import TextContent

from fastmcp import Client, FastMCP
from fastmcp.tools.function_tool import FunctionTool as Tool


class TestPartialTool:
    """Test tools created from functools.partial objects."""

    async def test_partial_sync(self):
        def add(x: int, y: int) -> int:
            return x + y

        partial_add = functools.partial(add, y=10)
        functools.update_wrapper(partial_add, add)

        tool = Tool.from_function(partial_add)
        result = await tool.run({"x": 5})
        assert result.content == [TextContent(type="text", text="15")]

    async def test_partial_async(self):
        async def multiply(x: int, factor: int) -> int:
            return x * factor

        partial_mul = functools.partial(multiply, factor=3)
        functools.update_wrapper(partial_mul, multiply)

        tool = Tool.from_function(partial_mul)
        result = await tool.run({"x": 7})
        assert result.content == [TextContent(type="text", text="21")]

    async def test_partial_preserves_name(self):
        def greet(name: str, greeting: str = "Hello") -> str:
            """Greet someone."""
            return f"{greeting}, {name}!"

        partial_greet = functools.partial(greet, greeting="Hi")
        functools.update_wrapper(partial_greet, greet)

        tool = Tool.from_function(partial_greet)
        assert tool.name == "greet"
        assert tool.description == "Greet someone."

    async def test_partial_without_update_wrapper(self):
        def add(x: int, y: int) -> int:
            return x + y

        partial_add = functools.partial(add, y=10)

        tool = Tool.from_function(partial_add, name="add_ten")
        result = await tool.run({"x": 5})
        assert result.content == [TextContent(type="text", text="15")]

    async def test_partial_with_add_tool(self):
        mcp = FastMCP("test")

        def greet(name: str, greeting: str = "Hello") -> str:
            return f"{greeting}, {name}!"

        partial_greet = functools.partial(greet, greeting="Hey")
        functools.update_wrapper(partial_greet, greet)

        mcp.add_tool(partial_greet)

        result = await mcp.call_tool("greet", {"name": "World"})
        assert result.content == [TextContent(type="text", text="Hey, World!")]

    async def test_partial_with_server_tool_decorator(self):
        mcp = FastMCP("test")

        def add(x: int, y: int) -> int:
            return x + y

        partial_add = functools.partial(add, y=100)
        functools.update_wrapper(partial_add, add)

        mcp.tool(partial_add)

        result = await mcp.call_tool("add", {"x": 5})
        assert result.content == [TextContent(type="text", text="105")]


class TestPartialPrompt:
    """Test prompts created from functools.partial objects."""

    async def test_partial_prompt_with_decorator(self):
        """Partial can be registered via @mcp.prompt() decorator."""
        mcp = FastMCP("test")

        def greet_prompt(name: str, lang: str) -> str:
            return f"Say hello to {name} in {lang}."

        partial_greet = functools.partial(greet_prompt, lang="French")
        functools.update_wrapper(partial_greet, greet_prompt)

        mcp.prompt(partial_greet)

        async with Client(mcp) as client:
            result = await client.get_prompt("greet_prompt", {"name": "Alice"})
            assert "Alice" in str(result.messages[0])
            assert "French" in str(result.messages[0])


class TestPartialResource:
    """Test resources created from functools.partial objects."""

    async def test_partial_resource_with_decorator(self):
        """Partial can be registered via @mcp.resource() decorator."""
        mcp = FastMCP("test")

        def get_data(key: str, fmt: str = "text") -> str:
            return f"{key} in {fmt} format"

        partial_data = functools.partial(get_data, fmt="json")
        functools.update_wrapper(partial_data, get_data)

        mcp.resource("data://{key}")(partial_data)

        async with Client(mcp) as client:
            content = await client.read_resource("data://users")
            assert "users" in str(content)
            assert "json" in str(content)
