"""References inside instance values are data, not schemas to dereference."""

from copy import deepcopy
from typing import Annotated, Any
from unittest.mock import patch

import pytest
from jsonschema import Draft202012Validator
from pydantic import Field

from fastmcp import Client, FastMCP
from fastmcp.utilities import json_schema
from fastmcp.utilities.json_schema import dereference_refs


@pytest.mark.parametrize("keyword", ["default", "const", "example", "examples", "enum"])
@pytest.mark.parametrize("ref", ["#/$defs/Text", "https://example.com/customer.json"])
def test_literal_refs_are_preserved(keyword: str, ref: str) -> None:
    value = {"$ref": ref, "description": "Customer"}
    literal = [value] if keyword in {"examples", "enum"} else value
    schema = {
        "type": "object",
        "properties": {
            "config": {"type": "object", keyword: literal},
            "actual": {"$ref": "#/$defs/Text"},
        },
        "$defs": {"Text": {"type": "string"}},
    }
    original = deepcopy(schema)

    with (
        patch(
            "jsonref.requests.get", side_effect=AssertionError("Unexpected HTTP load")
        ) as http_load,
        patch(
            "jsonref.urlopen", side_effect=AssertionError("Unexpected file load")
        ) as file_load,
    ):
        result = dereference_refs(schema)

    http_load.assert_not_called()
    file_load.assert_not_called()
    assert schema == original
    assert result["properties"]["actual"] == {"type": "string"}
    assert result["properties"]["config"][keyword] == literal
    assert "$defs" not in result


def test_nested_literal_data_keeps_schema_shaped_keys() -> None:
    value = {
        "$ref": "https://example.com/customer.json",
        "$defs": {"Customer": {"$ref": "#/not-a-schema-reference"}},
        "properties": {"default": {"$ref": "file:///not-a-real-file"}},
    }
    schema = {
        "type": "object",
        "properties": {"config": {"$ref": "#/$defs/Config", "default": value}},
        "$defs": {"Config": {"type": "object", "examples": [value]}},
    }

    with (
        patch(
            "jsonref.requests.get", side_effect=AssertionError("Unexpected HTTP load")
        ) as http_load,
        patch(
            "jsonref.urlopen", side_effect=AssertionError("Unexpected file load")
        ) as file_load,
    ):
        result = dereference_refs(schema)

    http_load.assert_not_called()
    file_load.assert_not_called()
    config = result["properties"]["config"]
    assert config["default"] == value
    assert config["examples"] == [value]
    assert "$defs" not in result


@pytest.mark.parametrize("name", ["default", "const", "example", "examples", "enum"])
def test_property_names_are_not_literal_keywords(name: str) -> None:
    schema = {
        "type": "object",
        "properties": {name: {"$ref": "#/$defs/Text"}},
        "$defs": {"Text": {"type": "string"}},
    }

    result = dereference_refs(schema)

    assert result["properties"][name] == {"type": "string"}


def test_circular_schema_preserves_literal_refs_on_fallback() -> None:
    value = {"$ref": "https://example.com/customer.json"}
    schema = {
        "type": "object",
        "properties": {
            "child": {"$ref": "#"},
            "config": {"type": "object", "default": value},
        },
    }

    assert dereference_refs(schema) == schema


def test_literal_ref_strings_count_toward_expansion_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(json_schema, "_MAX_INLINED_TEXT", 150)
    schema = {
        "type": "object",
        "properties": {name: {"$ref": "#/$defs/Config"} for name in ("a", "b", "c")},
        "$defs": {
            "Config": {
                "type": "object",
                "default": {"$ref": "https://example.com/" + "x" * 100},
            }
        },
    }

    assert dereference_refs(schema) == schema


@pytest.mark.parametrize("enabled", [False, True])
async def test_advertised_default_matches_tool_call(enabled: bool) -> None:
    expected = {"$ref": "https://example.com/customer.json"}
    server = FastMCP("literal-ref", dereference_schemas=enabled)

    @server.tool
    def describe_schema(
        config: Annotated[dict[str, Any], Field(default=expected)],
    ) -> dict[str, Any]:
        return config

    async with Client(server) as client:
        tools = await client.list_tools()
        result = await client.call_tool("describe_schema", {})
        explicit = {"$ref": "#/custom/schema"}
        overridden = await client.call_tool("describe_schema", {"config": explicit})

    assert result.data == expected
    assert overridden.data == explicit
    assert tools[0].input_schema["properties"]["config"]["default"] == expected


@pytest.mark.parametrize("keyword", ["const", "enum"])
async def test_literal_output_constraint_accepts_tool_result(keyword: str) -> None:
    value = {"$ref": "https://example.com/customer.json"}
    schema = {
        "type": "object",
        "properties": {"config": {keyword: [value] if keyword == "enum" else value}},
        "required": ["config"],
    }
    server = FastMCP("literal-output")

    @server.tool(output_schema=schema)
    def get_schema() -> dict[str, Any]:
        return {"config": value}

    async with Client(server) as client:
        tools = await client.list_tools()
        result = await client.call_tool("get_schema")

    assert result.structured_content == {"config": value}
    assert tools[0].output_schema == schema
    assert Draft202012Validator(schema).is_valid(result.structured_content)
