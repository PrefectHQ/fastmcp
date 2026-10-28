"""Defaults are annotations, not explicit input that must satisfy a field type."""

from datetime import date
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from fastmcp import Client, FastMCP
from fastmcp.tools import ToolResult
from fastmcp.utilities.json_schema_type import json_schema_to_type


@pytest.mark.parametrize("additional_properties", [None, False, True])
@pytest.mark.parametrize(
    "property_schema, valid_value",
    [
        ({"type": "string", "default": None}, "Alice"),
        ({"type": "integer", "minimum": 1, "default": 0}, 2),
        ({"type": "array", "items": {"type": "integer"}, "default": ["bad"]}, [1]),
    ],
)
def test_incompatible_default_preserves_explicit_validation(
    additional_properties: bool | None,
    property_schema: dict[str, Any],
    valid_value: Any,
) -> None:
    schema = {"type": "object", "properties": {"value": property_schema}}
    if additional_properties is not None:
        schema["additionalProperties"] = additional_properties
    adapter: TypeAdapter[Any] = TypeAdapter(json_schema_to_type(schema))

    assert adapter.validate_python({}).value == property_schema["default"]
    assert adapter.validate_python({"value": valid_value}).value == valid_value
    with pytest.raises(ValidationError):
        adapter.validate_python({"value": property_schema["default"]})


@pytest.mark.parametrize("additional_properties", [None, False, True])
def test_valid_default_still_hydrates(additional_properties: bool | None) -> None:
    schema = {
        "type": "object",
        "properties": {
            "day": {"type": "string", "format": "date", "default": "2026-10-13"}
        },
    }
    if additional_properties is not None:
        schema["additionalProperties"] = additional_properties
    adapter: TypeAdapter[Any] = TypeAdapter(json_schema_to_type(schema))

    assert adapter.validate_python({}).day == date(2026, 10, 13)
    assert adapter.validate_python({"day": "2026-10-14"}).day == date(2026, 10, 14)


def test_nested_aliased_incompatible_default() -> None:
    schema = {
        "type": "object",
        "properties": {
            "config": {
                "type": "object",
                "properties": {"user-name": {"type": "string", "default": None}},
                "default": {},
            }
        },
    }
    adapter: TypeAdapter[Any] = TypeAdapter(json_schema_to_type(schema))

    assert adapter.validate_python({}).config.user_name is None
    assert adapter.validate_python({"config": {}}).config.user_name is None
    assert (
        adapter.validate_python({"config": {"user-name": "Alice"}}).config.user_name
        == "Alice"
    )
    with pytest.raises(ValidationError):
        adapter.validate_python({"config": {"user-name": None}})


def test_mutable_incompatible_default_is_not_shared() -> None:
    schema = {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"type": "integer"}, "default": ["bad"]}
        },
    }
    adapter: TypeAdapter[Any] = TypeAdapter(json_schema_to_type(schema))
    first = adapter.validate_python({})
    first.items.append("changed")

    assert adapter.validate_python({}).items == ["bad"]
    assert schema["properties"]["items"]["default"] == ["bad"]


def test_invalid_nested_default_does_not_block_explicit_override() -> None:
    schema = {
        "type": "object",
        "properties": {
            "outer": {
                "type": "object",
                "default": {"inner": {}},
                "properties": {
                    "inner": {
                        "type": "object",
                        "default": None,
                        "properties": {"value": {"type": "string"}},
                    }
                },
            }
        },
    }
    adapter: TypeAdapter[Any] = TypeAdapter(json_schema_to_type(schema))

    assert (
        adapter.validate_python(
            {"outer": {"inner": {"value": "hello"}}}
        ).outer.inner.value
        == "hello"
    )


async def test_client_hydrates_missing_field_with_incompatible_default() -> None:
    server = FastMCP()

    @server.tool(
        output_schema={
            "type": "object",
            "properties": {"name": {"type": "string", "default": None}},
        }
    )
    def get_profile() -> ToolResult:
        return ToolResult(content=[], structured_content={})

    async with Client(server) as client:
        result = await client.call_tool("get_profile")

    assert result.structured_content == {}
    assert result.data is not None
    assert result.data.name is None
