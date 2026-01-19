"""Tests for tool output schemas."""

from dataclasses import dataclass
from typing import Any

import pytest
from mcp.types import (
    TextContent,
)
from pydantic import AnyUrl, BaseModel, TypeAdapter
from typing_extensions import TypedDict

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from fastmcp.utilities.json_schema import compress_schema


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


class TestToolOutputSchema:
    @pytest.mark.parametrize("annotation", [str, int, float, bool, list, AnyUrl])
    async def test_simple_output_schema(self, annotation):
        mcp = FastMCP()

        @mcp.tool
        def f() -> annotation:
            return "hello"

        tools = await mcp.list_tools()
        assert len(tools) == 1

        type_schema = TypeAdapter(annotation).json_schema()
        type_schema = compress_schema(type_schema, prune_titles=True)
        assert tools[0].output_schema == {
            "type": "object",
            "properties": {"result": type_schema},
            "required": ["result"],
            "x-fastmcp-wrap-result": True,
        }

    @pytest.mark.parametrize(
        "annotation",
        [dict[str, int | str], PersonTypedDict, PersonModel, PersonDataclass],
    )
    async def test_structured_output_schema(self, annotation):
        mcp = FastMCP()

        @mcp.tool
        def f() -> annotation:
            return {"name": "John", "age": 30}

        tools = await mcp.list_tools()

        type_schema = compress_schema(
            TypeAdapter(annotation).json_schema(), prune_titles=True
        )
        assert len(tools) == 1

        actual_schema = _normalize_anyof_order(tools[0].output_schema)
        expected_schema = _normalize_anyof_order(type_schema)
        assert actual_schema == expected_schema

    async def test_disabled_output_schema_no_structured_content(self):
        mcp = FastMCP()

        @mcp.tool(output_schema=None)
        def f() -> int:
            return 42

        result = await mcp.call_tool("f", {})
        assert isinstance(result.content, list)
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "42"
        assert result.structured_content is None

    async def test_manual_structured_content(self):
        from typing import cast

        from fastmcp.tools.function_tool import DecoratedTool

        mcp = FastMCP()

        @mcp.tool
        def f() -> ToolResult:
            return ToolResult(
                content="Hello, world!", structured_content={"message": "Hello, world!"}
            )

        # In new decorator mode, check metadata instead of attributes
        from fastmcp.utilities.types import NotSet

        decorated = cast(DecoratedTool, f)
        assert hasattr(f, "__fastmcp__")
        assert decorated.__fastmcp__.output_schema is NotSet

        result = await mcp.call_tool("f", {})
        assert isinstance(result.content, list)
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "Hello, world!"
        assert result.structured_content == {"message": "Hello, world!"}

    async def test_output_schema_none(self):
        """Test that output_schema=None works correctly."""
        mcp = FastMCP()

        @mcp.tool(output_schema=None)
        def simple_tool() -> int:
            return 42

        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "simple_tool")
        assert tool.output_schema is None

        result = await mcp.call_tool("simple_tool", {})
        assert result.structured_content is None
        assert isinstance(result.content, list)
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "42"

    async def test_output_schema_explicit_object(self):
        """Test explicit object output schema."""
        mcp = FastMCP()

        @mcp.tool(
            output_schema={
                "type": "object",
                "properties": {
                    "greeting": {"type": "string"},
                    "count": {"type": "integer"},
                },
                "required": ["greeting"],
            }
        )
        def explicit_tool() -> dict[str, Any]:
            return {"greeting": "Hello", "count": 42}

        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "explicit_tool")
        expected_schema = {
            "type": "object",
            "properties": {
                "greeting": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": ["greeting"],
        }
        assert tool.output_schema == expected_schema

        result = await mcp.call_tool("explicit_tool", {})
        assert result.structured_content == {"greeting": "Hello", "count": 42}

    async def test_output_schema_wrapped_primitive(self):
        """Test wrapped primitive output schema."""
        mcp = FastMCP()

        @mcp.tool
        def primitive_tool() -> str:
            return "Hello, primitives!"

        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "primitive_tool")
        expected_schema = {
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result"],
            "x-fastmcp-wrap-result": True,
        }
        assert tool.output_schema == expected_schema

        result = await mcp.call_tool("primitive_tool", {})
        assert result.structured_content == {"result": "Hello, primitives!"}

    async def test_output_schema_complex_type(self):
        """Test complex type output schema."""
        mcp = FastMCP()

        @mcp.tool
        def complex_tool() -> list[dict[str, int]]:
            return [{"a": 1, "b": 2}, {"c": 3, "d": 4}]

        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "complex_tool")
        expected_inner_schema = compress_schema(
            TypeAdapter(list[dict[str, int]]).json_schema(), prune_titles=True
        )
        expected_schema = {
            "type": "object",
            "properties": {"result": expected_inner_schema},
            "required": ["result"],
            "x-fastmcp-wrap-result": True,
        }
        assert tool.output_schema == expected_schema

        result = await mcp.call_tool("complex_tool", {})
        expected_data = [{"a": 1, "b": 2}, {"c": 3, "d": 4}]
        assert result.structured_content == {"result": expected_data}

    async def test_output_schema_dataclass(self):
        """Test dataclass output schema."""
        mcp = FastMCP()

        @dataclass
        class User:
            name: str
            age: int

        @mcp.tool
        def dataclass_tool() -> User:
            return User(name="Alice", age=30)

        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "dataclass_tool")
        expected_schema = compress_schema(
            TypeAdapter(User).json_schema(), prune_titles=True
        )
        assert tool.output_schema == expected_schema
        assert tool.output_schema and "x-fastmcp-wrap-result" not in tool.output_schema

        result = await mcp.call_tool("dataclass_tool", {})
        assert result.structured_content == {"name": "Alice", "age": 30}

    async def test_output_schema_mixed_content_types(self):
        """Test tools with mixed content and output schemas."""
        mcp = FastMCP()

        @mcp.tool
        def mixed_output() -> list[Any]:
            return [
                "text message",
                {"structured": "data"},
                TextContent(type="text", text="direct MCP content"),
            ]

        result = await mcp.call_tool("mixed_output", {})
        assert isinstance(result.content, list)
        assert len(result.content) == 3
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "text message"
        assert isinstance(result.content[1], TextContent)
        assert result.content[1].text == '{"structured":"data"}'
        assert isinstance(result.content[2], TextContent)
        assert result.content[2].text == "direct MCP content"

    async def test_output_schema_serialization_edge_cases(self):
        """Test edge cases in output schema serialization."""
        mcp = FastMCP()

        @mcp.tool
        def edge_case_tool() -> tuple[int, str]:
            return (42, "hello")

        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "edge_case_tool")

        assert tool.output_schema and "x-fastmcp-wrap-result" in tool.output_schema

        result = await mcp.call_tool("edge_case_tool", {})
        assert result.structured_content == {"result": [42, "hello"]}
