"""Tests for tool decorator patterns."""

from dataclasses import dataclass
from typing import Annotated

import pytest
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from fastmcp import FastMCP
from fastmcp.exceptions import NotFoundError
from fastmcp.tools.tool import Tool


def _normalize_anyof_order(schema):
    """Normalize the order of items in anyOf arrays for consistent comparison."""
    if isinstance(schema, dict):
        if "anyOf" in schema:
            schema = schema.copy()
            schema["anyOf"] = sorted(schema["anyOf"], key=str)
        return {k: _normalize_anyof_order(v) for k, v in schema.items()}
    elif isinstance(schema, list):
        return [_normalize_anyof_order(item) for item in schema]
    return schema


class PersonTypedDict(TypedDict):
    name: str
    age: int


class PersonModel(BaseModel):
    name: str
    age: int


@dataclass
class PersonDataclass:
    name: str
    age: int


class TestToolDecorator:
    async def test_no_tools_before_decorator(self):
        mcp = FastMCP()

        with pytest.raises(NotFoundError, match="Unknown tool: 'add'"):
            await mcp.call_tool("add", {"x": 1, "y": 2})

    async def test_tool_decorator(self):
        mcp = FastMCP()

        @mcp.tool
        def add(x: int, y: int) -> int:
            return x + y

        result = await mcp.call_tool("add", {"x": 1, "y": 2})
        assert result.structured_content == {"result": 3}

    async def test_tool_decorator_without_parentheses(self):
        """Test that @tool decorator works without parentheses."""
        mcp = FastMCP()

        @mcp.tool
        def add(x: int, y: int) -> int:
            return x + y

        tools = await mcp.list_tools()
        assert any(t.name == "add" for t in tools)

        result = await mcp.call_tool("add", {"x": 1, "y": 2})
        assert result.structured_content == {"result": 3}

    async def test_tool_decorator_with_name(self):
        mcp = FastMCP()

        @mcp.tool(name="custom-add")
        def add(x: int, y: int) -> int:
            return x + y

        result = await mcp.call_tool("custom-add", {"x": 1, "y": 2})
        assert result.structured_content == {"result": 3}

    async def test_tool_decorator_with_description(self):
        mcp = FastMCP()

        @mcp.tool(description="Add two numbers")
        def add(x: int, y: int) -> int:
            return x + y

        tools = await mcp.list_tools()
        assert len(tools) == 1
        tool = tools[0]
        assert tool.description == "Add two numbers"

    async def test_tool_decorator_instance_method(self):
        mcp = FastMCP()

        class MyClass:
            def __init__(self, x: int):
                self.x = x

            def add(self, y: int) -> int:
                return self.x + y

        obj = MyClass(10)
        mcp.add_tool(Tool.from_function(obj.add))
        result = await mcp.call_tool("add", {"y": 2})
        assert result.structured_content == {"result": 12}

    async def test_tool_decorator_classmethod(self):
        mcp = FastMCP()

        class MyClass:
            x: int = 10

            @classmethod
            def add(cls, y: int) -> int:
                return cls.x + y

        mcp.add_tool(Tool.from_function(MyClass.add))
        result = await mcp.call_tool("add", {"y": 2})
        assert result.structured_content == {"result": 12}

    async def test_tool_decorator_staticmethod(self):
        mcp = FastMCP()

        class MyClass:
            @mcp.tool
            @staticmethod
            def add(x: int, y: int) -> int:
                return x + y

        result = await mcp.call_tool("add", {"x": 1, "y": 2})
        assert result.structured_content == {"result": 3}

    async def test_tool_decorator_async_function(self):
        mcp = FastMCP()

        @mcp.tool
        async def add(x: int, y: int) -> int:
            return x + y

        result = await mcp.call_tool("add", {"x": 1, "y": 2})
        assert result.structured_content == {"result": 3}

    async def test_tool_decorator_classmethod_error(self):
        mcp = FastMCP()

        with pytest.raises(TypeError, match="classmethod"):

            class MyClass:
                @mcp.tool
                @classmethod
                def add(cls, y: int) -> None:
                    pass

    async def test_tool_decorator_classmethod_async_function(self):
        mcp = FastMCP()

        class MyClass:
            x = 10

            @classmethod
            async def add(cls, y: int) -> int:
                return cls.x + y

        mcp.add_tool(Tool.from_function(MyClass.add))
        result = await mcp.call_tool("add", {"y": 2})
        assert result.structured_content == {"result": 12}

    async def test_tool_decorator_staticmethod_async_function(self):
        mcp = FastMCP()

        class MyClass:
            @staticmethod
            async def add(x: int, y: int) -> int:
                return x + y

        mcp.add_tool(Tool.from_function(MyClass.add))
        result = await mcp.call_tool("add", {"x": 1, "y": 2})
        assert result.structured_content == {"result": 3}

    async def test_tool_decorator_staticmethod_order(self):
        """Test that the recommended decorator order works for static methods"""
        mcp = FastMCP()

        class MyClass:
            @mcp.tool
            @staticmethod
            def add_v1(x: int, y: int) -> int:
                return x + y

        result = await mcp.call_tool("add_v1", {"x": 1, "y": 2})
        assert result.structured_content == {"result": 3}

    async def test_tool_decorator_with_tags(self):
        """Test that the tool decorator properly sets tags."""
        mcp = FastMCP()

        @mcp.tool(tags={"example", "test-tag"})
        def sample_tool(x: int) -> int:
            return x * 2

        tools = await mcp.list_tools()
        assert len(tools) == 1
        assert tools[0].tags == {"example", "test-tag"}

    async def test_add_tool_with_custom_name(self):
        """Test adding a tool with a custom name using server.add_tool()."""
        mcp = FastMCP()

        def multiply(a: int, b: int) -> int:
            """Multiply two numbers."""
            return a * b

        mcp.add_tool(Tool.from_function(multiply, name="custom_multiply"))

        tools = await mcp.list_tools()
        assert any(t.name == "custom_multiply" for t in tools)

        result = await mcp.call_tool("custom_multiply", {"a": 5, "b": 3})
        assert result.structured_content == {"result": 15}

        assert not any(t.name == "multiply" for t in tools)

    async def test_tool_with_annotated_arguments(self):
        """Test that tools with annotated arguments work correctly."""
        mcp = FastMCP()

        @mcp.tool
        def add(
            x: Annotated[int, Field(description="x is an int")],
            y: Annotated[str, Field(description="y is not an int")],
        ) -> None:
            pass

        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "add")
        assert tool.parameters["properties"]["x"]["description"] == "x is an int"
        assert tool.parameters["properties"]["y"]["description"] == "y is not an int"

    async def test_tool_with_field_defaults(self):
        """Test that tools with annotated arguments work correctly."""
        mcp = FastMCP()

        @mcp.tool
        def add(
            x: int = Field(description="x is an int"),
            y: str = Field(description="y is not an int"),
        ) -> None:
            pass

        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "add")
        assert tool.parameters["properties"]["x"]["description"] == "x is an int"
        assert tool.parameters["properties"]["y"]["description"] == "y is not an int"

    async def test_tool_direct_function_call(self):
        """Test that tools can be registered via direct function call."""
        from typing import cast

        from fastmcp.tools.function_tool import DecoratedTool

        mcp = FastMCP()

        def standalone_function(x: int, y: int) -> int:
            """A standalone function to be registered."""
            return x + y

        result_fn = mcp.tool(standalone_function, name="direct_call_tool")

        # In new decorator mode, returns the function with metadata
        decorated = cast(DecoratedTool, result_fn)
        assert hasattr(result_fn, "__fastmcp__")
        assert decorated.__fastmcp__.name == "direct_call_tool"
        assert result_fn is standalone_function

        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "direct_call_tool")
        # Tool is registered separately, not same object as decorated function
        assert tool.name == "direct_call_tool"

        result = await mcp.call_tool("direct_call_tool", {"x": 5, "y": 3})
        assert result.structured_content == {"result": 8}

    async def test_tool_decorator_with_string_name(self):
        """Test that @tool("custom_name") syntax works correctly."""
        mcp = FastMCP()

        @mcp.tool("string_named_tool")
        def my_function(x: int) -> str:
            """A function with a string name."""
            return f"Result: {x}"

        tools = await mcp.list_tools()
        assert any(t.name == "string_named_tool" for t in tools)
        assert not any(t.name == "my_function" for t in tools)

        result = await mcp.call_tool("string_named_tool", {"x": 42})
        assert result.structured_content == {"result": "Result: 42"}

    async def test_tool_decorator_conflicting_names_error(self):
        """Test that providing both positional and keyword name raises an error."""
        mcp = FastMCP()

        with pytest.raises(
            TypeError,
            match="Cannot specify both a name as first argument and as keyword argument",
        ):

            @mcp.tool("positional_name", name="keyword_name")
            def my_function(x: int) -> str:
                return f"Result: {x}"

    async def test_tool_decorator_with_output_schema(self):
        mcp = FastMCP()

        with pytest.raises(
            ValueError, match="Output schemas must represent object types"
        ):

            @mcp.tool(output_schema={"type": "integer"})
            def my_function(x: int) -> str:
                return f"Result: {x}"

    async def test_tool_decorator_with_meta(self):
        """Test that meta parameter is passed through the tool decorator."""
        mcp = FastMCP()

        meta_data = {"version": "1.0", "author": "test"}

        @mcp.tool(meta=meta_data)
        def multiply(a: int, b: int) -> int:
            """Multiply two numbers."""
            return a * b

        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "multiply")

        assert tool.meta == meta_data
