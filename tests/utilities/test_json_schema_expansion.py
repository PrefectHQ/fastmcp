"""`dereference_refs` inlines references unless the result would be very large."""

from collections.abc import Callable
from typing import Any

import pytest

from fastmcp import Client, FastMCP
from fastmcp.tools import Tool
from fastmcp.utilities.json_schema import dereference_refs


def nested_union_schema(
    levels: int,
    *,
    defs_key: str = "$defs",
    pointer: str = "#/$defs/{}",
) -> dict[str, Any]:
    """Each level is a union of three references to the level below."""
    defs: dict[str, Any] = {"L0": {"type": "string"}}
    for i in range(1, levels + 1):
        defs[f"L{i}"] = {"anyOf": [{"$ref": pointer.format(f"L{i - 1}")}] * 3}
    return {
        "type": "object",
        "properties": {"x": {"$ref": pointer.format(f"L{levels}")}},
        defs_key: defs,
    }


def lists_through_refs_schema(levels: int) -> dict[str, Any]:
    """Each `P{i}` refers to `D{i}`, so `#/$defs/P{i}/x` means `D{i}["x"]`."""
    defs: dict[str, Any] = {"D0": {"x": [0]}, "P0": {"$ref": "#/$defs/D0", "x": 0}}
    for i in range(1, levels + 1):
        defs[f"D{i}"] = {"x": [{"$ref": f"#/$defs/P{i - 1}/x"}] * 3}
        defs[f"P{i}"] = {"$ref": f"#/$defs/D{i}", "x": 0}
    return {
        "type": "object",
        "properties": {"v": {"items": {"$ref": f"#/$defs/P{levels}/x"}}},
        "$defs": defs,
    }


def lists_with_tab_in_pointers_schema(levels: int) -> dict[str, Any]:
    """URL parsing removes the tab, so `#/$defs/D\\t{i}` means `D{i}`."""
    defs: dict[str, Any] = {"D0": [0]}
    for i in range(1, levels + 1):
        defs[f"D{i}"] = [{"$ref": f"#/$defs/D\t{i - 1}"}] * 3
        defs[f"D\t{i - 1}"] = 0
    return {
        "type": "object",
        "properties": {"v": {"items": {"$ref": f"#/$defs/D{levels}"}}},
        "$defs": defs,
    }


POINTER_FORMS = [
    pytest.param("$defs", "#/$defs/{}", id="defs"),
    pytest.param("definitions", "#/definitions/{}", id="definitions"),
    pytest.param("definitions", "#definitions/{}", id="no-leading-slash"),
    pytest.param("definitions", "#//definitions/{}", id="double-slash"),
    pytest.param("my defs", "#/my%20defs/{}", id="percent-encoded"),
    pytest.param("my/defs", "#/my~1defs/{}", id="tilde-encoded"),
]

LIST_SCHEMAS = [lists_through_refs_schema, lists_with_tab_in_pointers_schema]


@pytest.mark.parametrize(("defs_key", "pointer"), POINTER_FORMS)
def test_large_schema_keeps_defs(defs_key: str, pointer: str) -> None:
    schema = nested_union_schema(9, defs_key=defs_key, pointer=pointer)
    assert dereference_refs(schema) == schema


@pytest.mark.parametrize(("defs_key", "pointer"), POINTER_FORMS)
def test_schema_is_inlined(defs_key: str, pointer: str) -> None:
    schema = nested_union_schema(4, defs_key=defs_key, pointer=pointer)
    value = dereference_refs(schema)["properties"]["x"]
    for _ in range(4):
        assert len(value["anyOf"]) == 3
        value = value["anyOf"][0]
    assert value == {"type": "string"}


@pytest.mark.parametrize("build", LIST_SCHEMAS)
def test_large_list_schema_keeps_defs(
    build: Callable[[int], dict[str, Any]],
) -> None:
    schema = build(11)
    assert dereference_refs(schema) == schema


@pytest.mark.parametrize("build", LIST_SCHEMAS)
def test_list_schema_is_inlined(build: Callable[[int], dict[str, Any]]) -> None:
    value = dereference_refs(build(3))["properties"]["v"]["items"]
    for _ in range(3):
        assert len(value) == 3
        value = value[0]
    assert value == [0]


def test_large_unused_definitions_keep_defs() -> None:
    schema = nested_union_schema(9)
    schema["properties"] = {}
    assert dereference_refs(schema) == schema


def test_repeated_long_description_keeps_defs() -> None:
    schema = {
        "type": "object",
        "properties": {f"p{i}": {"$ref": "#/$defs/Leaf"} for i in range(40)},
        "$defs": {"Leaf": {"type": "string", "description": "x" * 150_000}},
    }
    assert dereference_refs(schema) == schema


def test_repeated_large_examples_keep_defs() -> None:
    schema = {
        "type": "object",
        "properties": {f"p{i}": {"$ref": "#/$defs/Leaf"} for i in range(150)},
        "$defs": {"Leaf": {"type": "integer", "examples": [10**4000] * 10}},
    }
    assert dereference_refs(schema) == schema


def test_large_discriminated_union_keeps_defs() -> None:
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


def test_long_reference_chain_keeps_defs() -> None:
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


def test_large_schema_still_resolves_root_reference() -> None:
    schema = nested_union_schema(9)
    defs = schema.pop("$defs")
    defs["Root"] = schema
    result = dereference_refs({"$ref": "#/$defs/Root", "$defs": defs})
    assert result == {**schema, "$defs": defs}


def test_sibling_keywords_come_from_the_referenced_definition() -> None:
    schema = {
        "type": "object",
        "properties": {"x": {"$ref": "#/$defs/Holder/properties/Item"}},
        "$defs": {
            "Holder": {"properties": {"Item": {"type": "integer"}}},
            "Item": {"$ref": "#/$defs/Text", "description": "A text item"},
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


async def test_list_tools_keeps_defs_for_large_schema() -> None:
    def echo(x: str) -> str:
        return x

    schema = nested_union_schema(9)
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
