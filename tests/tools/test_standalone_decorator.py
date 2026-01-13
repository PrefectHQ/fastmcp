"""Tests for the standalone @tool decorator.

The @tool decorator attaches metadata to functions without registering them
to a server. Functions can be added explicitly via server.add_tool() or
discovered by FileSystemProvider.
"""

from typing import cast

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.tools import tool
from fastmcp.tools.function_tool import DecoratedTool, ToolMeta


class TestToolDecorator:
    """Tests for the @tool decorator."""

    def test_tool_without_parens(self):
        """@tool without parentheses should attach metadata."""

        @tool
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        decorated = cast(DecoratedTool, greet)
        assert callable(greet)
        assert hasattr(greet, "__fastmcp__")
        assert isinstance(decorated.__fastmcp__, ToolMeta)
        assert decorated.__fastmcp__.name is None  # Uses function name by default

    def test_tool_with_empty_parens(self):
        """@tool() with empty parentheses should attach metadata."""

        @tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        decorated = cast(DecoratedTool, greet)
        assert callable(greet)
        assert hasattr(greet, "__fastmcp__")
        assert isinstance(decorated.__fastmcp__, ToolMeta)

    def test_tool_with_name_arg(self):
        """@tool("name") with name as first arg should work."""

        @tool("custom-greet")
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        decorated = cast(DecoratedTool, greet)
        assert callable(greet)
        assert hasattr(greet, "__fastmcp__")
        assert decorated.__fastmcp__.name == "custom-greet"

    def test_tool_with_name_kwarg(self):
        """@tool(name="name") with keyword arg should work."""

        @tool(name="custom-greet")
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        decorated = cast(DecoratedTool, greet)
        assert callable(greet)
        assert hasattr(greet, "__fastmcp__")
        assert decorated.__fastmcp__.name == "custom-greet"

    def test_tool_with_all_metadata(self):
        """@tool with all metadata should store it all."""

        @tool(
            name="custom-greet",
            title="Greeting Tool",
            description="Greets people",
            tags={"greeting", "demo"},
            meta={"custom": "value"},
        )
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        decorated = cast(DecoratedTool, greet)
        assert callable(greet)
        assert hasattr(greet, "__fastmcp__")
        assert decorated.__fastmcp__.name == "custom-greet"
        assert decorated.__fastmcp__.title == "Greeting Tool"
        assert decorated.__fastmcp__.description == "Greets people"
        assert decorated.__fastmcp__.tags == {"greeting", "demo"}
        assert decorated.__fastmcp__.meta == {"custom": "value"}

    async def test_tool_function_still_callable(self):
        """Decorated function should still be directly callable."""

        @tool
        def greet(name: str) -> str:
            """Greet someone."""
            return f"Hello, {name}!"

        # The function is still callable even though it has metadata
        result = cast(DecoratedTool, greet)("World")
        assert result == "Hello, World!"

    def test_tool_rejects_classmethod_decorator(self):
        """@tool should reject classmethod-decorated functions."""
        with pytest.raises(TypeError, match="classmethod"):

            class MyClass:
                @tool
                @classmethod
                def my_method(cls) -> str:
                    return "hello"

    def test_tool_with_both_name_args_raises(self):
        """@tool should raise if both positional and keyword name are given."""
        with pytest.raises(TypeError, match="Cannot specify.*both.*argument.*keyword"):

            @tool("name1", name="name2")  # type: ignore[call-overload]
            def my_tool() -> str:
                return "hello"

    async def test_tool_added_to_server(self):
        """Tool created by @tool should work when added to a server."""

        @tool
        def greet(name: str) -> str:
            """Greet someone."""
            return f"Hello, {name}!"

        mcp = FastMCP("Test")
        mcp.add_tool(greet)

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert any(t.name == "greet" for t in tools)

            result = await client.call_tool("greet", {"name": "World"})
            assert result.data == "Hello, World!"
