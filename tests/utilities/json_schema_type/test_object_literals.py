"""Literal constraints on root object schemas."""

import dataclasses
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from fastmcp.utilities.json_schema_type import (
    json_schema_to_type,
    json_schema_to_type_adapter,
)


@pytest.mark.parametrize("keyword", ["const", "enum"])
@pytest.mark.parametrize("with_properties", [False, True])
@pytest.mark.parametrize("cached", [False, True])
def test_root_object_literal(keyword: str, with_properties: bool, cached: bool) -> None:
    value = {"mode": "safe"}
    schema: dict[str, Any] = {
        "type": "object",
        keyword: value if keyword == "const" else [value, {"mode": "review"}],
    }
    if with_properties:
        schema.update(
            properties={"mode": {"type": "string"}},
            required=["mode"],
        )
    adapter = (
        json_schema_to_type_adapter(schema)
        if cached
        else TypeAdapter(json_schema_to_type(schema, name="Mode"))
    )
    result = adapter.validate_python(value)
    if with_properties:
        assert dataclasses.is_dataclass(result)
        assert not isinstance(result, type)
        assert dataclasses.asdict(result) == value
        if not cached:
            assert type(result).__name__ == "Mode"
    else:
        assert result == value

    if keyword == "enum":
        adapter.validate_python({"mode": "review"})

    for invalid in [{"mode": "other"}, {}, {"mode": "safe", "extra": True}]:
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)


@pytest.mark.parametrize("keyword", ["const", "enum"])
def test_root_object_literal_preserves_property_constraints(keyword: str) -> None:
    value = {"count": -1}
    schema = {
        "type": "object",
        "properties": {"count": {"type": "integer", "minimum": 0}},
        "required": ["count"],
        keyword: value if keyword == "const" else [value],
    }
    adapter = json_schema_to_type_adapter(schema)
    with pytest.raises(ValidationError):
        adapter.validate_python(value)


def test_root_object_empty_const() -> None:
    adapter = json_schema_to_type_adapter({"type": "object", "const": {}})
    assert adapter.validate_python({}) == {}
    with pytest.raises(ValidationError):
        adapter.validate_python({"extra": True})


def test_root_object_empty_enum() -> None:
    adapter = json_schema_to_type_adapter({"type": "object", "enum": []})
    with pytest.raises(ValidationError):
        adapter.validate_python({})


@pytest.mark.parametrize("keyword", ["const", "enum"])
def test_root_object_literal_does_not_share_mutable_values(keyword: str) -> None:
    value = {"items": [1]}
    adapter = json_schema_to_type_adapter(
        {"type": "object", keyword: value if keyword == "const" else [value]}
    )
    first = adapter.validate_python({"items": [1]})
    second = adapter.validate_python({"items": [1]})
    first["items"].append(2)
    assert second == {"items": [1]}
    assert value == {"items": [1]}
    assert adapter.validate_python({"items": [1]}) == {"items": [1]}
    with pytest.raises(ValidationError):
        adapter.validate_python({"items": [1, 2]})


@pytest.mark.parametrize("keyword", ["const", "enum"])
@pytest.mark.parametrize("additional_properties", [False, True])
def test_root_object_literal_revalidates_generated_instance(
    keyword: str, additional_properties: bool
) -> None:
    value = {"class": "safe"}
    adapter = json_schema_to_type_adapter(
        {
            "type": "object",
            "properties": {"class": {"type": "string"}},
            "required": ["class"],
            "additionalProperties": additional_properties,
            keyword: value if keyword == "const" else [value],
        }
    )
    result = adapter.validate_json('{"class": "safe"}')
    field_name = "class" if additional_properties else "class_"
    assert getattr(result, field_name) == "safe"
    assert adapter.validate_python(result) is result


@pytest.mark.parametrize("keyword", ["const", "enum"])
@pytest.mark.parametrize("additional_properties", [False, True])
def test_root_object_literal_revalidation_preserves_wire_representation(
    keyword: str, additional_properties: bool
) -> None:
    value = {"at": "2026-10-11T00:00:00+00:00"}
    adapter = json_schema_to_type_adapter(
        {
            "type": "object",
            "properties": {
                "at": {"type": "string", "format": "date-time"},
                "note": {"type": "string"},
            },
            "required": ["at"],
            "additionalProperties": additional_properties,
            keyword: value if keyword == "const" else [value],
        }
    )
    result = adapter.validate_python(value)
    assert result.at.isoformat() == value["at"]
    assert adapter.validate_python(result) is result
