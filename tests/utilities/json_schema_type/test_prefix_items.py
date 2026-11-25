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


TREE = {
    "type": "object",
    "properties": {
        "label": {"type": "string"},
        "kids": {
            "type": "array",
            "prefixItems": [{"type": "string"}],
            "items": {"$ref": "#/$defs/Node"},
        },
    },
}


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param({**TREE, "$defs": {"Node": TREE}}, id="root-is-the-node"),
        pytest.param(
            {
                "type": "object",
                "properties": {
                    "nodes": {"type": "array", "items": {"$ref": "#/$defs/Node"}}
                },
                "$defs": {"Node": TREE},
            },
            id="node-under-object",
        ),
    ],
)
def test_recursive_reference_in_prefix_items_tail(schema: dict[str, Any]) -> None:
    """A `$ref` cycle through the tail of a positional array still resolves."""
    adapter = TypeAdapter(json_schema_to_type(schema))
    node = {"label": "a", "kids": ["edge", {"label": "b", "kids": ["leaf"]}]}
    value = node if "label" in schema["properties"] else {"nodes": [node]}
    assert Draft202012Validator(schema).is_valid(value)
    result = adapter.dump_python(adapter.validate_python(value), exclude_none=True)
    assert result == value
    adapter.validate_python({})
    with pytest.raises(ValidationError):
        bad = {"label": "a", "kids": ["edge", {"label": 1}]}
        adapter.validate_python(
            bad if "label" in schema["properties"] else {"nodes": [bad]}
        )


def test_recursive_reference_in_prefix_position() -> None:
    """A `$ref` cycle through a prefix position still resolves."""
    node = {
        "type": "object",
        "properties": {
            "label": {"type": "string"},
            "edge": {
                "type": "array",
                "prefixItems": [{"type": "string"}, {"$ref": "#/$defs/Node"}],
            },
        },
    }
    adapter = TypeAdapter(json_schema_to_type({**node, "$defs": {"Node": node}}))
    value = {"label": "a", "edge": ["e", {"label": "b"}]}
    result = adapter.dump_python(adapter.validate_python(value), exclude_none=True)
    assert result == value


PAIR = {"type": "array", "prefixItems": [{"type": "string"}, {"type": "integer"}]}


def _typed_extras(pair: dict[str, Any], **defs: Any) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "x": {
                "type": "object",
                "properties": {"pair": pair},
                "additionalProperties": {"type": "string"},
            }
        },
        **({"$defs": defs} if defs else {}),
    }


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(_typed_extras(PAIR), id="inline"),
        pytest.param(_typed_extras({"$ref": "#/$defs/Pair"}, Pair=PAIR), id="ref"),
    ],
)
@pytest.mark.parametrize("pair", [[], ["a"], ["a", 1], ["a", 1, True]])
def test_short_arrays_under_typed_additional_properties(
    schema: dict[str, Any], pair: list[Any]
) -> None:
    """Missing prefix positions stay optional when the enclosing object
    validates defaults for its typed additional properties."""
    value = {"x": {"pair": pair}}
    assert Draft202012Validator(schema).is_valid(value)
    adapter = TypeAdapter(json_schema_to_type(schema))
    assert adapter.dump_python(adapter.validate_python(value))["x"]["pair"] == pair


def test_typed_additional_properties_still_validate_positions() -> None:
    """Present positions, length bounds and extras are still checked."""
    adapter = TypeAdapter(json_schema_to_type(_typed_extras({**PAIR, "minItems": 1})))
    for invalid in ({"pair": []}, {"pair": [1]}, {"pair": ["a", "b"]}, {"k": 1}):
        with pytest.raises(ValidationError):
            adapter.validate_python({"x": invalid})
    with pytest.raises(ValidationError) as excinfo:
        adapter.validate_python({"x": {"pair": [1]}})
    assert excinfo.value.errors()[0]["loc"][-1] == 0
