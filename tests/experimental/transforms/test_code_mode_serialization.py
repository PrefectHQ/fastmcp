from typing import Any

import pytest

from fastmcp import FastMCP
from fastmcp.server.transforms.search.base import (
    _schema_section,
    _schema_type,
    serialize_tools_for_output_markdown,
)

# ---------------------------------------------------------------------------
# _schema_type unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "schema,expected",
    [
        ({"type": "string"}, "string"),
        ({"type": "integer"}, "integer"),
        ({"type": "boolean"}, "boolean"),
        ({"type": "null"}, "null"),
        ({"type": "array", "items": {"type": "string"}}, "string[]"),
        ({"type": "array", "items": {"type": "integer"}}, "integer[]"),
        ({"type": "array"}, "any[]"),
        ({"$ref": "#/$defs/Foo"}, "object"),
        ({"properties": {"x": {"type": "int"}}}, "object"),
        ({}, "any"),
        (None, "any"),
        ("not a dict", "any"),
    ],
)
def test_schema_type_basic(schema: Any, expected: str) -> None:
    assert _schema_type(schema) == expected


@pytest.mark.parametrize(
    "schema,expected",
    [
        ({"anyOf": [{"type": "string"}, {"type": "null"}]}, "string?"),
        ({"anyOf": [{"type": "string"}, {"type": "integer"}]}, "string | integer"),
        (
            {"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]},
            "string | integer?",
        ),
        ({"anyOf": [{"type": "null"}]}, "null"),
        ({"anyOf": []}, "any"),
        ({"oneOf": [{"type": "string"}, {"type": "null"}]}, "string?"),
        ({"oneOf": [{"type": "string"}, {"type": "integer"}]}, "string | integer"),
        ({"allOf": [{"type": "object"}]}, "object"),
        ({"allOf": [{"$ref": "#/$defs/Foo"}, {"$ref": "#/$defs/Bar"}]}, "object"),
    ],
)
def test_schema_type_unions(schema: Any, expected: str) -> None:
    assert _schema_type(schema) == expected


# ---------------------------------------------------------------------------
# _schema_section unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "schema,expected_lines",
    [
        (None, ["**Parameters**", "- `value` (any)"]),
        ("string", ["**Parameters**", "- `value` (any)"]),
        ({"type": "string"}, ["**Parameters**", "- `value` (string)"]),
        (
            {"type": "object", "properties": {}},
            ["**Parameters**", "*(no parameters)*"],
        ),
    ],
)
def test_schema_section_fallbacks(schema: Any, expected_lines: list[str]) -> None:
    assert _schema_section(schema, "Parameters") == expected_lines


def test_schema_section_lists_fields_with_required_marker() -> None:
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
        },
        "required": ["name"],
    }
    lines = _schema_section(schema, "Parameters")
    assert lines[0] == "**Parameters**"
    assert "- `name` (string, required)" in lines
    assert "- `age` (integer)" in lines


def test_schema_section_lists_nested_field_names_through_defs() -> None:
    """Object-valued fields show their own field names one level down."""
    schema = {
        "type": "object",
        "$defs": {
            "Card": {
                "type": "object",
                "properties": {"id": {}, "url": {}, "note": {"$ref": "#/$defs/Note"}},
            },
            "Note": {"type": "object", "properties": {"text": {}}},
            "Page": {
                "type": "object",
                "properties": {"current_page": {}, "has_more": {}},
            },
        },
        "properties": {
            "items": {"type": "array", "items": {"$ref": "#/$defs/Card"}},
            "pagination": {
                "anyOf": [{"$ref": "#/$defs/Page"}, {"type": "null"}],
                "default": None,
            },
            "count": {"type": "integer"},
            "inline": {"type": "object", "properties": {"a": {}, "b": {}}},
        },
    }
    lines = _schema_section(schema, "Returns")
    assert "- `items` (object[]): `id`, `url`, `note`" in lines
    assert "- `pagination` (object?): `current_page`, `has_more`" in lines
    assert "- `count` (integer)" in lines
    assert "- `inline` (object): `a`, `b`" in lines


def test_schema_section_unions_fields_across_object_branches() -> None:
    """A | B lists the fields of both models; allOf merges inherited and inline fields."""
    schema = {
        "type": "object",
        "$defs": {
            "A": {"type": "object", "properties": {"nested": {}, "shared": {}}},
            "B": {"type": "object", "properties": {"value": {}, "shared": {}}},
            "Base": {"type": "object", "properties": {"id": {}}},
        },
        "properties": {
            "data": {"anyOf": [{"$ref": "#/$defs/A"}, {"$ref": "#/$defs/B"}]},
            "composed": {
                "allOf": [
                    {"$ref": "#/$defs/Base"},
                    {"type": "object", "properties": {"extra": {}}},
                ]
            },
        },
    }
    lines = _schema_section(schema, "Returns")
    assert "- `data` (object): `nested`, `shared`, `value`" in lines
    assert "- `composed` (object): `id`, `extra`" in lines


def test_schema_section_stops_on_recursive_refs() -> None:
    """`Json = list[Json] | int` refers to itself through anyOf and items and must not loop."""
    schema = {
        "type": "object",
        "$defs": {
            "Json": {
                "anyOf": [
                    {"type": "array", "items": {"$ref": "#/$defs/Json"}},
                    {"type": "integer"},
                ]
            },
            "Node": {
                "type": "object",
                "properties": {
                    "value": {},
                    "children": {"type": "array", "items": {"$ref": "#/$defs/Node"}},
                },
            },
        },
        "properties": {
            "payload": {"$ref": "#/$defs/Json"},
            "tree": {"$ref": "#/$defs/Node"},
        },
        "required": ["payload"],
    }
    lines = _schema_section(schema, "Parameters")
    assert "- `payload` (object, required)" in lines
    assert "- `tree` (object): `value`, `children`" in lines


def test_schema_section_truncates_long_nested_objects() -> None:
    fields = {f"f{i}": {} for i in range(20)}
    schema = {
        "type": "object",
        "properties": {"row": {"type": "object", "properties": fields}},
    }
    [_, line] = _schema_section(schema, "Returns")
    assert line.endswith("`f15`, +4 more")


def test_schema_section_ignores_unresolvable_refs() -> None:
    schema = {
        "type": "object",
        "properties": {
            "x": {"$ref": "#/$defs/Missing"},
            "y": {"$ref": "https://example.com/schema.json"},
        },
    }
    assert _schema_section(schema, "Parameters") == [
        "**Parameters**",
        "- `x` (object)",
        "- `y` (object)",
    ]


# ---------------------------------------------------------------------------
# serialize_tools_for_output_markdown unit tests
# ---------------------------------------------------------------------------


def test_serialize_tools_for_output_markdown_empty_list() -> None:
    assert serialize_tools_for_output_markdown([]) == "No tools matched the query."


async def test_serialize_tools_for_output_markdown_basic_tool() -> None:
    mcp = FastMCP("MD Basic")

    @mcp.tool
    def square(x: int) -> int:
        """Compute the square of a number."""
        return x * x

    tools = await mcp.list_tools()
    result = serialize_tools_for_output_markdown(tools)

    assert "### square" in result
    assert "Compute the square of a number." in result
    assert "**Parameters**" in result
    assert "`x` (integer, required)" in result


async def test_serialize_tools_for_output_markdown_omits_output_section_when_no_schema() -> (
    None
):
    mcp = FastMCP("MD No Output")

    @mcp.tool
    def ping() -> None:
        pass

    tools = await mcp.list_tools()
    result = serialize_tools_for_output_markdown(tools)

    assert "**Returns**" not in result


async def test_serialize_tools_for_output_markdown_includes_output_section_when_schema_present() -> (
    None
):
    mcp = FastMCP("MD With Output")

    @mcp.tool
    def double(x: int) -> int:
        return x * 2

    tools = await mcp.list_tools()
    result = serialize_tools_for_output_markdown(tools)

    assert "**Returns**" in result


async def test_serialize_tools_for_output_markdown_omits_description_when_absent() -> (
    None
):
    mcp = FastMCP("MD No Desc")

    @mcp.tool
    def ping() -> None:
        pass

    tools = await mcp.list_tools()
    result = serialize_tools_for_output_markdown(tools)

    assert "### ping" in result


async def test_serialize_tools_for_output_markdown_optional_field_uses_question_mark() -> (
    None
):
    mcp = FastMCP("MD Optional")

    @mcp.tool
    def greet(name: str, greeting: str | None = None) -> str:
        return f"{greeting or 'Hello'}, {name}!"

    tools = await mcp.list_tools()
    result = serialize_tools_for_output_markdown(tools)

    assert "`greeting` (string?)" in result


async def test_serialize_tools_for_output_markdown_multiple_tools_separated() -> None:
    mcp = FastMCP("MD Multi")

    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b

    @mcp.tool
    def subtract(a: int, b: int) -> int:
        return a - b

    tools = await mcp.list_tools()
    result = serialize_tools_for_output_markdown(tools)

    assert "### add" in result
    assert "### subtract" in result
    assert "\n\n" in result
