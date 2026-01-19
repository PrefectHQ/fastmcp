"""Tests for container types in JSON schema conversion."""

from dataclasses import Field, dataclass
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from fastmcp.utilities.json_schema_type import (
    json_schema_to_type,
)


def get_dataclass_field(type: type, field_name: str) -> Field:
    return type.__dataclass_fields__[field_name]  # ty: ignore[unresolved-attribute]


class TestArrayTypes:
    """Test suite for array validation."""

    @pytest.fixture
    def string_array(self):
        return json_schema_to_type({"type": "array", "items": {"type": "string"}})

    @pytest.fixture
    def min_items_array(self):
        return json_schema_to_type(
            {"type": "array", "items": {"type": "string"}, "minItems": 2}
        )

    @pytest.fixture
    def max_items_array(self):
        return json_schema_to_type(
            {"type": "array", "items": {"type": "string"}, "maxItems": 3}
        )

    @pytest.fixture
    def unique_items_array(self):
        return json_schema_to_type(
            {"type": "array", "items": {"type": "string"}, "uniqueItems": True}
        )

    def test_array_accepts_valid_items(self, string_array):
        validator = TypeAdapter(string_array)
        assert validator.validate_python(["a", "b"]) == ["a", "b"]

    def test_array_rejects_invalid_items(self, string_array):
        validator = TypeAdapter(string_array)
        with pytest.raises(ValidationError):
            validator.validate_python([1, "b"])

    def test_min_items_accepts_valid(self, min_items_array):
        validator = TypeAdapter(min_items_array)
        assert validator.validate_python(["a", "b"]) == ["a", "b"]

    def test_min_items_rejects_too_few(self, min_items_array):
        validator = TypeAdapter(min_items_array)
        with pytest.raises(ValidationError):
            validator.validate_python(["a"])

    def test_max_items_accepts_valid(self, max_items_array):
        validator = TypeAdapter(max_items_array)
        assert validator.validate_python(["a", "b", "c"]) == ["a", "b", "c"]

    def test_max_items_rejects_too_many(self, max_items_array):
        validator = TypeAdapter(max_items_array)
        with pytest.raises(ValidationError):
            validator.validate_python(["a", "b", "c", "d"])

    def test_unique_items_accepts_unique(self, unique_items_array):
        validator = TypeAdapter(unique_items_array)
        assert isinstance(validator.validate_python(["a", "b"]), set)

    def test_unique_items_converts_duplicates(self, unique_items_array):
        validator = TypeAdapter(unique_items_array)
        result = validator.validate_python(["a", "a", "b"])
        assert result == {"a", "b"}


class TestObjectTypes:
    """Test suite for object validation."""

    @pytest.fixture
    def simple_object(self):
        return json_schema_to_type(
            {
                "type": "object",
                "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
            }
        )

    @pytest.fixture
    def required_object(self):
        return json_schema_to_type(
            {
                "type": "object",
                "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
                "required": ["name"],
            }
        )

    @pytest.fixture
    def nested_object(self):
        return json_schema_to_type(
            {
                "type": "object",
                "properties": {
                    "user": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "age": {"type": "integer"},
                        },
                        "required": ["name"],
                    }
                },
            }
        )

    @pytest.mark.parametrize(
        "input_type, expected_type",
        [
            # Plain dict becomes dict[str, Any] (JSON Schema accurate)
            (dict, dict[str, Any]),
            # dict[str, Any] stays the same
            (dict[str, Any], dict[str, Any]),
            # Simple typed dicts work correctly
            (dict[str, str], dict[str, str]),
            (dict[str, int], dict[str, int]),
            # Union value types work
            (dict[str, str | int], dict[str, str | int]),
            # Key types are constrained to str in JSON Schema
            (dict[int, list[str]], dict[str, list[str]]),
            # Union key types become str (JSON Schema limitation)
            (dict[str | int, str | None], dict[str, str | None]),
        ],
    )
    def test_dict_types_are_generated_correctly(self, input_type, expected_type):
        schema = TypeAdapter(input_type).json_schema()
        generated_type = json_schema_to_type(schema)
        assert generated_type == expected_type

    def test_object_accepts_valid(self, simple_object):
        validator = TypeAdapter(simple_object)
        result = validator.validate_python({"name": "test", "age": 30})
        assert result.name == "test"
        assert result.age == 30

    def test_object_accepts_extra_properties(self, simple_object):
        validator = TypeAdapter(simple_object)
        result = validator.validate_python(
            {"name": "test", "age": 30, "extra": "field"}
        )
        assert result.name == "test"
        assert result.age == 30
        assert not hasattr(result, "extra")

    def test_required_accepts_valid(self, required_object):
        validator = TypeAdapter(required_object)
        result = validator.validate_python({"name": "test"})
        assert result.name == "test"
        assert result.age is None

    def test_required_rejects_missing(self, required_object):
        validator = TypeAdapter(required_object)
        with pytest.raises(ValidationError):
            validator.validate_python({})

    def test_nested_accepts_valid(self, nested_object):
        validator = TypeAdapter(nested_object)
        result = validator.validate_python({"user": {"name": "test", "age": 30}})
        assert result.user.name == "test"
        assert result.user.age == 30

    def test_nested_rejects_invalid(self, nested_object):
        validator = TypeAdapter(nested_object)
        with pytest.raises(ValidationError):
            validator.validate_python({"user": {"age": 30}})

    def test_object_with_underscore_names(self):
        @dataclass
        class Data:
            x: int
            x_: int
            _x: int

        schema = TypeAdapter(Data).json_schema()
        assert schema == {
            "title": "Data",
            "type": "object",
            "properties": {
                "x": {"type": "integer", "title": "X"},
                "x_": {"type": "integer", "title": "X"},
                "_x": {"type": "integer", "title": "X"},
            },
            "required": ["x", "x_", "_x"],
        }

        object = json_schema_to_type(schema)
        object_schema = TypeAdapter(object).json_schema()
        assert object_schema == schema
