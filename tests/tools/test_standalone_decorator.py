"""Tests for the standalone @tool decorator.

The @tool decorator creates FunctionTool objects without registering them
to a server. Objects can be added explicitly via server.add_tool() or
discovered by FileSystemProvider.
"""

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.tools import FunctionTool, tool


class TestToolDecorator:
    """Tests for the @tool decorator."""

    def test_tool_without_parens(self):
        """@tool without parentheses should create a FunctionTool."""

        @tool
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        assert isinstance(greet, FunctionTool)
        assert greet.name == "greet"

    def test_tool_with_empty_parens(self):
        """@tool() with empty parentheses should create a FunctionTool."""

        @tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        assert isinstance(greet, FunctionTool)
        assert greet.name == "greet"

    def test_tool_with_name_arg(self):
        """@tool("name") with name as first arg should work."""

        @tool("custom-greet")
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        assert isinstance(greet, FunctionTool)
        assert greet.name == "custom-greet"

    def test_tool_with_name_kwarg(self):
        """@tool(name="name") with keyword arg should work."""

        @tool(name="custom-greet")
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        assert isinstance(greet, FunctionTool)
        assert greet.name == "custom-greet"

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

        assert isinstance(greet, FunctionTool)
        assert greet.name == "custom-greet"
        assert greet.title == "Greeting Tool"
        assert greet.description == "Greets people"
        assert greet.tags == {"greeting", "demo"}
        assert greet.meta == {"custom": "value"}

    async def test_tool_can_be_run(self):
        """Tool created by @tool should be runnable."""

        @tool
        def greet(name: str) -> str:
            """Greet someone."""
            return f"Hello, {name}!"

        result = await greet.run({"name": "World"})
        assert result.content[0].text == "Hello, World!"  # type: ignore[union-attr]

    def test_tool_rejects_classmethod_decorator(self):
        """@tool should reject classmethod-decorated functions."""
        with pytest.raises(TypeError, match="classmethod"):

            class MyClass:
                @tool  # type: ignore[arg-type]
                @classmethod
                def my_method(cls) -> str:
                    return "hello"

    def test_tool_with_both_name_args_raises(self):
        """@tool should raise if both positional and keyword name are given."""
        with pytest.raises(TypeError, match="Cannot specify both"):

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
