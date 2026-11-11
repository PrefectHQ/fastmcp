"""Tests for tool output schemas."""

from dataclasses import dataclass
from typing import Any, Literal

import pytest
from mcp_types import (
    TextContent,
)
from pydantic import AnyUrl, BaseModel, TypeAdapter
from typing_extensions import TypeAliasType, TypedDict

from fastmcp import Client, FastMCP
from fastmcp.tools.base import ToolResult
from fastmcp.utilities.json_schema import compress_schema, is_object_schema


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
    @pytest.mark.parametrize("annotation", [str, int, float, bool, AnyUrl])
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

    async def test_wrapped_result_includes_meta_flag(self):
        """Wrapped results include wrap_result in meta."""
        server = FastMCP()

        @server.tool
        def list_tool() -> list[dict]:
            return [{"a": 1}]

        result = await server.call_tool("list_tool", {})
        assert result.structured_content == {"result": [{"a": 1}]}
        assert result.meta == {"fastmcp": {"wrap_result": True}}

    async def test_unwrapped_result_has_no_meta_flag(self):
        """Unwrapped dict results do not include wrap_result in meta."""
        server = FastMCP()

        @server.tool
        def dict_tool() -> dict[str, int]:
            return {"value": 42}

        result = await server.call_tool("dict_tool", {})
        assert result.structured_content == {"value": 42}
        assert result.meta is None

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

    async def test_output_schema_wraps_non_object_ref_schema(self):
        """Root $ref schemas should only skip wrapping when they resolve to objects."""
        mcp = FastMCP()
        AliasType = TypeAliasType("AliasType", Literal["foo", "bar"])

        @mcp.tool
        def alias_tool() -> AliasType:
            return "foo"

        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "alias_tool")

        expected_inner_schema = compress_schema(
            TypeAdapter(AliasType).json_schema(mode="serialization"),
            prune_titles=True,
        )
        assert tool.output_schema == {
            "type": "object",
            "properties": {"result": expected_inner_schema},
            "required": ["result"],
            "x-fastmcp-wrap-result": True,
        }

        result = await mcp.call_tool("alias_tool", {})
        assert result.structured_content == {"result": "foo"}


@pytest.mark.parametrize(
    ("schema_type", "expected"),
    [
        ("object", True),
        (["object", "null"], True),
        (["object", "array"], False),
        ("array", False),
    ],
)
def test_explicit_type_decides_object_schema(schema_type: Any, expected: bool):
    schema = {"type": schema_type, "properties": {"value": {"type": "integer"}}}
    assert is_object_schema(schema) is expected


@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_nullable_object_output_schema_is_registrable(mode: str):
    schema = {
        "type": ["object", "null"],
        "properties": {"value": {"type": "integer"}},
    }
    mcp = FastMCP()

    @mcp.tool(output_schema=schema)
    def maybe_value() -> dict[str, int]:
        return {"value": 1}

    async with Client(mcp, mode=mode) as client:
        advertised = (await client.list_tools())[0].output_schema
        result = await client.call_tool("maybe_value", {})

    expected_type = "object" if mode == "legacy" else ["object", "null"]
    assert advertised is not None
    assert advertised["type"] == expected_type
    assert result.structured_content == {"value": 1}


class TestIsObjectSchemaRefResolution:
    """Tests for $ref resolution in is_object_schema, including JSON Pointer
    escaping and nested $defs paths."""

    def test_simple_ref_to_object(self):
        schema = {
            "$ref": "#/$defs/MyModel",
            "$defs": {
                "MyModel": {"type": "object", "properties": {"x": {"type": "int"}}}
            },
        }
        assert is_object_schema(schema) is True

    def test_simple_ref_to_non_object(self):
        schema = {
            "$ref": "#/$defs/MyEnum",
            "$defs": {"MyEnum": {"enum": ["a", "b"]}},
        }
        assert is_object_schema(schema) is False

    def test_nested_defs_path(self):
        """Refs like #/$defs/Outer/$defs/Inner should walk into nested dicts."""
        schema = {
            "$ref": "#/$defs/Outer/$defs/Inner",
            "$defs": {
                "Outer": {
                    "$defs": {
                        "Inner": {
                            "type": "object",
                            "properties": {"y": {"type": "string"}},
                        },
                    },
                },
            },
        }
        assert is_object_schema(schema) is True

    def test_nested_defs_non_object(self):
        schema = {
            "$ref": "#/$defs/Outer/$defs/Inner",
            "$defs": {
                "Outer": {
                    "$defs": {
                        "Inner": {"type": "string"},
                    },
                },
            },
        }
        assert is_object_schema(schema) is False

    def test_json_pointer_tilde_escape(self):
        """~0 should unescape to ~ and ~1 should unescape to /."""
        schema = {
            "$ref": "#/$defs/has~1slash~0tilde",
            "$defs": {"has/slash~tilde": {"type": "object", "properties": {}}},
        }
        assert is_object_schema(schema) is True

    def test_missing_nested_segment_returns_false(self):
        schema = {
            "$ref": "#/$defs/Outer/$defs/Missing",
            "$defs": {
                "Outer": {
                    "$defs": {},
                },
            },
        }
        assert is_object_schema(schema) is False
