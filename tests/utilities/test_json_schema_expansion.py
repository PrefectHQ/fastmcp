"""Limits on how far `dereference_refs` expands shared references."""

from typing import Any

import pytest

from fastmcp import Client, FastMCP
from fastmcp.tools import Tool
from fastmcp.utilities.json_schema import dereference_refs


def fanout_schema(
    depth: int,
    *,
    defs_key: str = "$defs",
    pointer: str = "#/$defs/{}",
) -> dict[str, Any]:
    """Each level refers to the level below three times, so inlining is 3**depth."""
    defs: dict[str, Any] = {"L0": {"type": "string"}}
    for i in range(1, depth + 1):
        defs[f"L{i}"] = {"anyOf": [{"$ref": pointer.format(f"L{i - 1}")}] * 3}
    return {
        "type": "object",
        "properties": {"x": {"$ref": pointer.format(f"L{depth}")}},
        defs_key: defs,
    }


POINTER_SPELLINGS = [
    pytest.param("$defs", "#/$defs/{}", id="defs"),
    pytest.param("definitions", "#/definitions/{}", id="definitions"),
    pytest.param("definitions", "#definitions/{}", id="no-leading-slash"),
    pytest.param("definitions", "#//definitions/{}", id="double-slash"),
    pytest.param("my defs", "#/my%20defs/{}", id="percent-encoded"),
    pytest.param("my/defs", "#/my~1defs/{}", id="escaped-slash"),
]


@pytest.mark.parametrize(("defs_key", "pointer"), POINTER_SPELLINGS)
def test_graph_too_large_to_inline_keeps_references(
    defs_key: str, pointer: str
) -> None:
    schema = fanout_schema(9, defs_key=defs_key, pointer=pointer)
    assert dereference_refs(schema) == schema


@pytest.mark.parametrize(("defs_key", "pointer"), POINTER_SPELLINGS)
def test_graph_within_limits_is_inlined(defs_key: str, pointer: str) -> None:
    result = dereference_refs(fanout_schema(4, defs_key=defs_key, pointer=pointer))
    value = result["properties"]["x"]
    for _ in range(4):
        assert len(value["anyOf"]) == 3
        value = value["anyOf"][0]
    assert value == {"type": "string"}


def test_unused_definitions_count_toward_the_limit() -> None:
    schema = fanout_schema(9)
    schema["properties"] = {}
    assert dereference_refs(schema) == schema


def test_repeated_long_strings_count_toward_the_limit() -> None:
    schema = {
        "type": "object",
        "properties": {f"p{i}": {"$ref": "#/$defs/Leaf"} for i in range(40)},
        "$defs": {"Leaf": {"type": "string", "description": "x" * 150_000}},
    }
    assert dereference_refs(schema) == schema


def test_discriminator_tags_count_toward_the_text_limit() -> None:
    schema = {
        "type": "object",
        "properties": {
            "x": {
                "discriminator": {"propertyName": "t" * 3000},
                "anyOf": [{"type": "object"}] * 2000,
            }
        },
        "$defs": {},
    }
    assert dereference_refs(schema) == schema


def test_reference_chain_too_deep_to_inline_keeps_references() -> None:
    defs: dict[str, Any] = {
        f"L{i}": {"$ref": f"#/$defs/L{i - 1}"} for i in range(1999, 0, -1)
    }
    defs["L0"] = {"type": "string"}
    schema = {
        "type": "object",
        "properties": {"x": {"$ref": "#/$defs/L1999"}},
        "$defs": defs,
    }
    assert dereference_refs(schema) == schema


def test_root_reference_is_still_resolved_when_graph_is_too_large() -> None:
    schema = fanout_schema(9)
    defs = schema.pop("$defs")
    defs["Root"] = schema
    result = dereference_refs({"$ref": "#/$defs/Root", "$defs": defs})
    assert result == {**schema, "$defs": defs}


def test_nested_definition_pointer_merges_siblings_from_its_own_target() -> None:
    schema = {
        "type": "object",
        "properties": {"x": {"$ref": "#/$defs/Holder/properties/Item"}},
        "$defs": {
            "Holder": {"properties": {"Item": {"type": "integer"}}},
            "Item": {"$ref": "#/$defs/Text", "description": "unrelated definition"},
            "Text": {"type": "string"},
        },
    }
    assert dereference_refs(schema)["properties"]["x"] == {"type": "integer"}


@pytest.mark.parametrize(
    ("key", "pointer"),
    [
        ("", "#/properties/"),
        ("a/b", "#/properties/a~1b"),
        ("a b", "#/properties/a%20b"),
        ("a~b", "#/properties/a~0b"),
        ("a", "#properties/a"),
    ],
)
def test_pointer_segments_resolve_like_jsonref(key: str, pointer: str) -> None:
    schema = {"properties": {key: {"type": "string"}, "copy": {"$ref": pointer}}}
    assert dereference_refs(schema)["properties"]["copy"] == {"type": "string"}


async def test_tool_listing_keeps_references_when_graph_is_too_large() -> None:
    def echo(x: str) -> str:
        return x

    schema = fanout_schema(9)
    server = FastMCP("test")
    server.add_tool(
        Tool.from_function(echo).model_copy(
            update={"parameters": schema, "output_schema": schema}
        )
    )

    async with Client(server) as client:
        tools = await client.list_tools()

    assert tools[0].input_schema == schema
    assert tools[0].output_schema == schema
