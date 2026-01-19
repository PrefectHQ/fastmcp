"""Tests for union types in JSON schema conversion."""

from dataclasses import Field

import pytest
from pydantic import TypeAdapter, ValidationError

from fastmcp.utilities.json_schema_type import (
    json_schema_to_type,
)


def get_dataclass_field(type: type, field_name: str) -> Field:
    return type.__dataclass_fields__[field_name]  # ty: ignore[unresolved-attribute]


class TestUnionTypes:
    """Test suite for testing union type behaviors."""

    @pytest.fixture
    def heterogeneous_union(self):
        return json_schema_to_type({"type": ["string", "number", "boolean", "null"]})

    @pytest.fixture
    def union_with_constraints(self):
        return json_schema_to_type(
            {"type": ["string", "number"], "minLength": 3, "minimum": 0}
        )

    @pytest.fixture
    def union_with_formats(self):
        return json_schema_to_type({"type": ["string", "null"], "format": "email"})

    @pytest.fixture
    def nested_union_array(self):
        return json_schema_to_type(
            {"type": "array", "items": {"type": ["string", "number"]}}
        )

    @pytest.fixture
    def nested_union_object(self):
        return json_schema_to_type(
            {
                "type": "object",
                "properties": {
                    "id": {"type": ["string", "integer"]},
                    "data": {
                        "type": ["object", "null"],
                        "properties": {"value": {"type": "string"}},
                    },
                },
            }
        )

    def test_heterogeneous_accepts_string(self, heterogeneous_union):
        validator = TypeAdapter(heterogeneous_union)
        assert validator.validate_python("test") == "test"

    def test_heterogeneous_accepts_number(self, heterogeneous_union):
        validator = TypeAdapter(heterogeneous_union)
        assert validator.validate_python(123.45) == 123.45

    def test_heterogeneous_accepts_boolean(self, heterogeneous_union):
        validator = TypeAdapter(heterogeneous_union)
        assert validator.validate_python(True) is True

    def test_heterogeneous_accepts_null(self, heterogeneous_union):
        validator = TypeAdapter(heterogeneous_union)
        assert validator.validate_python(None) is None

    def test_heterogeneous_rejects_array(self, heterogeneous_union):
        validator = TypeAdapter(heterogeneous_union)
        with pytest.raises(ValidationError):
            validator.validate_python([])

    def test_constrained_string_valid(self, union_with_constraints):
        validator = TypeAdapter(union_with_constraints)
        assert validator.validate_python("test") == "test"

    def test_constrained_string_invalid(self, union_with_constraints):
        validator = TypeAdapter(union_with_constraints)
        with pytest.raises(ValidationError):
            validator.validate_python("ab")

    def test_constrained_number_valid(self, union_with_constraints):
        validator = TypeAdapter(union_with_constraints)
        assert validator.validate_python(10) == 10

    def test_constrained_number_invalid(self, union_with_constraints):
        validator = TypeAdapter(union_with_constraints)
        with pytest.raises(ValidationError):
            validator.validate_python(-1)

    def test_format_valid_email(self, union_with_formats):
        validator = TypeAdapter(union_with_formats)
        result = validator.validate_python("test@example.com")
        assert isinstance(result, str)

    def test_format_valid_null(self, union_with_formats):
        validator = TypeAdapter(union_with_formats)
        assert validator.validate_python(None) is None

    def test_format_invalid_email(self, union_with_formats):
        validator = TypeAdapter(union_with_formats)
        with pytest.raises(ValidationError):
            validator.validate_python("not-an-email")

    def test_nested_array_mixed_types(self, nested_union_array):
        validator = TypeAdapter(nested_union_array)
        result = validator.validate_python(["test", 123, "abc"])
        assert result == ["test", 123, "abc"]

    def test_nested_array_rejects_invalid(self, nested_union_array):
        validator = TypeAdapter(nested_union_array)
        with pytest.raises(ValidationError):
            validator.validate_python(["test", ["not", "allowed"], "abc"])

    def test_nested_object_string_id(self, nested_union_object):
        validator = TypeAdapter(nested_union_object)
        result = validator.validate_python({"id": "abc123", "data": {"value": "test"}})
        assert result.id == "abc123"
        assert result.data.value == "test"

    def test_nested_object_integer_id(self, nested_union_object):
        validator = TypeAdapter(nested_union_object)
        result = validator.validate_python({"id": 123, "data": None})
        assert result.id == 123
        assert result.data is None
