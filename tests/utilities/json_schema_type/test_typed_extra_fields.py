from datetime import date
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from fastmcp.utilities.json_schema_type import json_schema_to_type


@pytest.mark.parametrize(
    ("extra_schema", "value", "expected"),
    [
        ({"type": "integer"}, 12, 12),
        ({"type": "boolean"}, True, True),
        ({"type": "string", "format": "date"}, "2026-10-10", date(2026, 10, 10)),
        ({}, {"arbitrary": [1, "two"]}, {"arbitrary": [1, "two"]}),
    ],
)
def test_typed_extra_fields_are_preserved_and_hydrated(
    extra_schema: dict[str, Any], value: Any, expected: Any
) -> None:
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": extra_schema,
    }
    result: Any = TypeAdapter(json_schema_to_type(schema)).validate_python(
        {"name": "cache", "extra": value}
    )
    assert result.name == "cache"
    assert result.extra == expected
    assert type(result.extra) is type(expected)


def test_typed_extra_fields_validate_their_schema() -> None:
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "additionalProperties": {"type": "integer", "minimum": 0},
    }
    adapter = TypeAdapter(json_schema_to_type(schema))
    with pytest.raises(ValidationError):
        adapter.validate_python({"name": "cache", "hits": -1})


def test_typed_extra_fields_resolve_references() -> None:
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "additionalProperties": {"$ref": "#/$defs/Reading"},
        "$defs": {
            "Reading": {
                "type": "object",
                "properties": {"day": {"type": "string", "format": "date"}},
                "required": ["day"],
            }
        },
    }
    result: Any = TypeAdapter(json_schema_to_type(schema)).validate_python(
        {"name": "cache", "extra": {"day": "2026-10-10"}}
    )
    assert result.extra.day == date(2026, 10, 10)


def test_declared_reserved_names_do_not_override_extra_field_validation() -> None:
    schema = {
        "type": "object",
        "properties": {"__pydantic_extra__": {"type": "string"}},
        "required": ["__pydantic_extra__"],
        "additionalProperties": {"type": "integer"},
    }
    result: Any = TypeAdapter(json_schema_to_type(schema)).validate_python(
        {"__pydantic_extra__": "declared", "hits": 12}
    )
    assert result.model_dump(by_alias=True) == {
        "__pydantic_extra__": "declared",
        "hits": 12,
    }
    assert result.hits == 12


def test_typed_extra_fields_hydrate_declared_defaults() -> None:
    schema = {
        "type": "object",
        "properties": {
            "day": {"type": "string", "format": "date", "default": "2026-01-01"},
            "reading": {
                "type": "object",
                "properties": {"day": {"type": "string", "format": "date"}},
                "required": ["day"],
                "default": {"day": "2026-01-02"},
            },
        },
        "additionalProperties": {"type": "integer"},
    }
    result: Any = TypeAdapter(json_schema_to_type(schema)).validate_python({"hits": 12})
    assert result.day == date(2026, 1, 1)
    assert result.reading.day == date(2026, 1, 2)
    assert result.hits == 12


@pytest.mark.parametrize("title", ["Node", "Order Item (v2)"])
@pytest.mark.parametrize("name", [None, "Named Node"])
def test_typed_extra_fields_resolve_root_references(
    title: str, name: str | None
) -> None:
    schema = {
        "type": "object",
        "title": title,
        "properties": {"day": {"type": "string", "format": "date"}},
        "required": ["day"],
        "additionalProperties": {"$ref": "#"},
    }
    model = json_schema_to_type(schema, name=name)
    result: Any = TypeAdapter(model).validate_python(
        {"day": "2026-01-01", "child": {"day": "2026-01-02"}}
    )
    assert result.day == date(2026, 1, 1)
    assert isinstance(result.child, model)
    assert result.child.day == date(2026, 1, 2)


@pytest.mark.parametrize("title", ["Node", "Order Item (v2)"])
@pytest.mark.parametrize("name", [None, "Named Node"])
def test_typed_extra_models_resolve_declared_root_references(
    title: str, name: str | None
) -> None:
    schema = {
        "type": "object",
        "title": title,
        "properties": {
            "day": {"type": "string", "format": "date"},
            "child": {"$ref": "#"},
        },
        "required": ["day"],
        "additionalProperties": {"type": "integer"},
    }
    model = json_schema_to_type(schema, name=name)
    result: Any = TypeAdapter(model).validate_python(
        {"day": "2026-01-01", "child": {"day": "2026-01-02"}, "hits": 12}
    )
    assert result.day == date(2026, 1, 1)
    assert isinstance(result.child, model)
    assert result.child.day == date(2026, 1, 2)
    assert result.hits == 12
