"""Unpinned JSON Schema arrays validate prefix and trailing items separately."""

from datetime import date
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter, ValidationError

from fastmcp import Client, FastMCP
from fastmcp.utilities.json_schema_type import json_schema_to_type


@pytest.mark.parametrize("value", [[], ["a"], ["a", 2], ["a", 2, 3.5]])
def test_prefix_items_do_not_use_the_tail_schema(value: list[Any]) -> None:
    adapter = TypeAdapter(
        json_schema_to_type(
            {
                "type": "array",
                "prefixItems": [{"type": "string"}],
                "items": {"type": "number"},
            }
        )
    )
    result = adapter.validate_python(value)
    assert result == value
    assert type(result) is list


@pytest.mark.parametrize("tail", [{}, {"items": True}, {"items": False}])
@pytest.mark.parametrize("value", [[], ["a"], ["a", 2]])
def test_prefix_items_accept_missing_positions(
    tail: dict[str, Any], value: list[Any]
) -> None:
    adapter = TypeAdapter(
        json_schema_to_type(
            {
                "type": "array",
                "prefixItems": [{"type": "string"}, {"type": "integer"}],
                **tail,
            }
        )
    )
    assert adapter.validate_python(value) == value


@pytest.mark.parametrize("tail", [{}, {"items": True}, {"items": False}])
def test_prefix_items_reject_an_invalid_present_position(tail: dict[str, Any]) -> None:
    adapter = TypeAdapter(
        json_schema_to_type(
            {"type": "array", "prefixItems": [{"type": "string"}], **tail}
        )
    )
    with pytest.raises(ValidationError):
        adapter.validate_python([1])


@pytest.mark.parametrize("items", [False, {"type": "number"}])
def test_prefix_items_reject_invalid_tail(items: Any) -> None:
    adapter = TypeAdapter(
        json_schema_to_type(
            {
                "type": "array",
                "prefixItems": [{"type": "string"}],
                "items": items,
            }
        )
    )
    with pytest.raises(ValidationError):
        adapter.validate_python(["a", "invalid"])


def test_unpinned_prefix_and_tail_formats_keep_existing_hydration() -> None:
    adapter = TypeAdapter(
        json_schema_to_type(
            {
                "type": "array",
                "prefixItems": [{"type": "string", "format": "date"}],
                "items": {"type": "string", "format": "date"},
            }
        )
    )
    result = adapter.validate_python(["2026-10-29", "2026-10-30"])
    assert result == [date(2026, 10, 29), date(2026, 10, 30)]
    assert type(result[0]) is date
    assert type(result) is list
    assert adapter.dump_python(result) == result
    assert adapter.dump_json(result) == b'["2026-10-29","2026-10-30"]'
    assert adapter.validate_json('["2026-10-29","2026-10-30"]', strict=True) == result


@pytest.mark.parametrize("tail", [{}, {"items": True}, {"items": {}}])
def test_unrestricted_tail_preserves_existing_prefix_values(
    tail: dict[str, Any],
) -> None:
    adapter = TypeAdapter(
        json_schema_to_type(
            {
                "type": "array",
                "prefixItems": [{"type": "string", "format": "date"}],
                **tail,
            }
        )
    )
    result = adapter.validate_python(["2026-10-29", "tail"])
    assert result == ["2026-10-29", "tail"]
    assert type(result[0]) is str
    assert adapter.validate_json('["2026-10-29","tail"]', strict=True) == result


async def test_client_hydrates_date_prefix_with_date_tail() -> None:
    server = FastMCP()

    @server.tool(
        output_schema={
            "type": "object",
            "properties": {
                "value": {
                    "type": "array",
                    "prefixItems": [{"type": "string", "format": "date"}],
                    "items": {"type": "string", "format": "date"},
                }
            },
            "required": ["value"],
        }
    )
    def read_dates() -> dict[str, Any]:
        return {"value": ["2026-10-29", "2026-10-30"]}

    async with Client(server) as client:
        result = await client.call_tool("read_dates")

    assert result.structured_content == {"value": ["2026-10-29", "2026-10-30"]}
    assert result.data.value == [date(2026, 10, 29), date(2026, 10, 30)]
    assert type(result.data.value[0]) is date


@pytest.mark.parametrize("value", [[], ["a", 1, 2], ["a", "a"]])
def test_prefix_items_keep_length_and_uniqueness_constraints(value: list[Any]) -> None:
    adapter = TypeAdapter(
        json_schema_to_type(
            {
                "type": "array",
                "prefixItems": [{"type": "string"}],
                "items": True,
                "minItems": 1,
                "maxItems": 2,
                "uniqueItems": True,
            }
        )
    )
    with pytest.raises(ValidationError):
        adapter.validate_python(value)
    assert adapter.validate_python(["a", 1]) == ["a", 1]


async def test_client_data_keeps_a_valid_prefix_with_a_typed_tail() -> None:
    server = FastMCP()

    @server.tool(
        output_schema={
            "type": "object",
            "properties": {
                "value": {
                    "type": "array",
                    "prefixItems": [{"type": "string"}],
                    "items": {"type": "number"},
                }
            },
            "required": ["value"],
        }
    )
    def read() -> dict[str, Any]:
        return {"value": ["a", 2]}

    async with Client(server) as client:
        result = await client.call_tool("read")

    assert result.structured_content == {"value": ["a", 2]}
    assert result.data.value == ["a", 2]
    assert type(result.data.value) is list


@pytest.mark.parametrize("constraints", [{}, {"minItems": 1, "maxItems": 3}])
@pytest.mark.parametrize("items", [False, True, {"type": "number"}])
def test_exported_schema_agrees_with_positional_validation(
    constraints: dict[str, Any], items: Any
) -> None:
    schema = {
        "type": "array",
        "prefixItems": [{"type": "string"}],
        "items": items,
        **constraints,
    }
    adapter = TypeAdapter(json_schema_to_type(schema))
    original = Draft202012Validator(schema)
    exported = Draft202012Validator(adapter.json_schema())
    for value in ([], ["a"], ["a", 2], ["a", "tail"], [1]):
        assert exported.is_valid(value) == original.is_valid(value)


def test_prefix_items_resolve_references_and_validate_json() -> None:
    adapter = TypeAdapter(
        json_schema_to_type(
            {
                "type": "array",
                "prefixItems": [{"$ref": "#/$defs/Name"}],
                "$defs": {"Name": {"type": "string", "minLength": 2}},
                "items": False,
            }
        )
    )
    assert adapter.validate_json("[]") == []
    assert adapter.validate_json('["ok"]') == ["ok"]
    with pytest.raises(ValidationError):
        adapter.validate_json('["x"]')


def test_strict_validation_keeps_list_container_semantics() -> None:
    adapter = TypeAdapter(
        json_schema_to_type(
            {
                "type": "array",
                "prefixItems": [{"type": "string"}],
                "items": {"type": "integer"},
            }
        )
    )
    assert adapter.validate_python(["a", 2], strict=True) == ["a", 2]
    assert adapter.validate_json('["a", 2]', strict=True) == ["a", 2]
    with pytest.raises(ValidationError):
        adapter.validate_python(("a", 2), strict=True)
    with pytest.raises(ValidationError):
        adapter.validate_python(["a", "2"], strict=True)
