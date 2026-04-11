"""Core JSON schema type conversion tests."""

import dataclasses
from dataclasses import Field
from enum import Enum
from typing import Any, Literal

import pytest
from pydantic import TypeAdapter, ValidationError

from fastmcp.utilities.json_schema_type import (
    json_schema_to_type,
)


def get_dataclass_field(type: type, field_name: str) -> Field:
    return type.__dataclass_fields__[field_name]  # ty: ignore[unresolved-attribute]


class TestSimpleTypes:
    """Test suite for basic type validation."""

    @pytest.fixture
    def simple_string(self):
        return json_schema_to_type({"type": "string"})

    @pytest.fixture
    def simple_number(self):
        return json_schema_to_type({"type": "number"})

    @pytest.fixture
    def simple_integer(self):
        return json_schema_to_type({"type": "integer"})

    @pytest.fixture
    def simple_boolean(self):
        return json_schema_to_type({"type": "boolean"})

    @pytest.fixture
    def simple_null(self):
        return json_schema_to_type({"type": "null"})

    def test_string_accepts_string(self, simple_string):
        validator = TypeAdapter(simple_string)
        assert validator.validate_python("test") == "test"

    def test_string_rejects_number(self, simple_string):
        validator = TypeAdapter(simple_string)
        with pytest.raises(ValidationError):
            validator.validate_python(123)

    def test_number_accepts_float(self, simple_number):
        validator = TypeAdapter(simple_number)
        assert validator.validate_python(123.45) == 123.45

    def test_number_accepts_integer(self, simple_number):
        validator = TypeAdapter(simple_number)
        assert validator.validate_python(123) == 123

    def test_number_accepts_numeric_string(self, simple_number):
        validator = TypeAdapter(simple_number)
        assert validator.validate_python("123.45") == 123.45
        assert validator.validate_python("123") == 123

    def test_number_rejects_invalid_string(self, simple_number):
        validator = TypeAdapter(simple_number)
        with pytest.raises(ValidationError):
            validator.validate_python("not a number")

    def test_integer_accepts_integer(self, simple_integer):
        validator = TypeAdapter(simple_integer)
        assert validator.validate_python(123) == 123

    def test_integer_accepts_integer_string(self, simple_integer):
        validator = TypeAdapter(simple_integer)
        assert validator.validate_python("123") == 123

    def test_integer_rejects_float(self, simple_integer):
        validator = TypeAdapter(simple_integer)
        with pytest.raises(ValidationError):
            validator.validate_python(123.45)

    def test_integer_rejects_float_string(self, simple_integer):
        validator = TypeAdapter(simple_integer)
        with pytest.raises(ValidationError):
            validator.validate_python("123.45")

    def test_boolean_accepts_boolean(self, simple_boolean):
        validator = TypeAdapter(simple_boolean)
        assert validator.validate_python(True) is True
        assert validator.validate_python(False) is False

    def test_boolean_accepts_boolean_strings(self, simple_boolean):
        validator = TypeAdapter(simple_boolean)
        assert validator.validate_python("true") is True
        assert validator.validate_python("True") is True
        assert validator.validate_python("false") is False
        assert validator.validate_python("False") is False

    def test_boolean_rejects_invalid_string(self, simple_boolean):
        validator = TypeAdapter(simple_boolean)
        with pytest.raises(ValidationError):
            validator.validate_python("not a boolean")

    def test_null_accepts_none(self, simple_null):
        validator = TypeAdapter(simple_null)
        assert validator.validate_python(None) is None

    def test_null_rejects_false(self, simple_null):
        validator = TypeAdapter(simple_null)
        with pytest.raises(ValidationError):
            validator.validate_python(False)


class TestBooleanSchemas:
    """JSON Schema draft-06+ allows true/false as property schemas."""

    def test_true_property_schema_accepts_any_value(self):
        """A property with schema `true` should accept any value."""
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}, "anything": True},
            "required": ["name", "anything"],
        }
        result = json_schema_to_type(schema)
        validator = TypeAdapter(result)
        obj = validator.validate_python({"name": "test", "anything": 42})
        assert obj.name == "test"  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
        assert obj.anything == 42  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]

    def test_false_property_schema_rejects_values(self):
        """A property with schema `false` should reject any provided value."""
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}, "never": False},
            "required": ["name"],
        }
        result = json_schema_to_type(schema)
        validator = TypeAdapter(result)
        obj = validator.validate_python({"name": "test"})
        assert obj.name == "test"  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]

        with pytest.raises(ValidationError):
            validator.validate_python({"name": "test", "never": "anything"})

    def test_boolean_schema_in_object_with_additional_properties(self):
        """Boolean property schemas work alongside additionalProperties=True."""
        schema = {
            "type": "object",
            "properties": {
                "known": {"type": "string"},
                "flexible": True,
            },
            "required": ["known"],
            "additionalProperties": True,
        }
        result = json_schema_to_type(schema)
        validator = TypeAdapter(result)
        obj = validator.validate_python(
            {"known": "hello", "flexible": [1, 2, 3], "extra": "field"}
        )
        assert obj.known == "hello"  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
        assert obj.flexible == [1, 2, 3]  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]

    def test_issue_3783_boolean_property_schemas(self):
        """Regression test for GitHub issue #3783."""
        schema = {
            "type": "object",
            "properties": {
                "ts": {"type": "integer"},
                "level": True,
                "app": True,
                "tag": {"type": ["array", "null"], "items": {"type": "string"}},
            },
            "required": ["ts"],
            "additionalProperties": True,
        }
        result = json_schema_to_type(schema)
        validator = TypeAdapter(result)
        obj = validator.validate_python({"ts": 123, "level": "info", "app": "myapp"})
        assert obj.ts == 123  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
        assert obj.level == "info"  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
        assert obj.app == "myapp"  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]


class TestConstrainedTypes:
    def test_constant(self):
        validator = TypeAdapter(Literal["x"])
        schema = validator.json_schema()
        type_ = json_schema_to_type(schema)
        assert type_ == Literal["x"]
        assert TypeAdapter(type_).validate_python("x") == "x"
        with pytest.raises(ValidationError):
            TypeAdapter(type_).validate_python("y")

    def test_union_constants(self):
        validator = TypeAdapter(Literal["x"] | Literal["y"])
        schema = validator.json_schema()
        type_ = json_schema_to_type(schema)
        assert type_ == Literal["x"] | Literal["y"]
        assert TypeAdapter(type_).validate_python("x") == "x"
        assert TypeAdapter(type_).validate_python("y") == "y"
        with pytest.raises(ValidationError):
            TypeAdapter(type_).validate_python("z")

    def test_enum_str(self):
        class MyEnum(Enum):
            X = "x"
            Y = "y"

        validator = TypeAdapter(MyEnum)
        schema = validator.json_schema()
        type_ = json_schema_to_type(schema)
        assert type_ == Literal["x", "y"]
        assert TypeAdapter(type_).validate_python("x") == "x"
        assert TypeAdapter(type_).validate_python("y") == "y"
        with pytest.raises(ValidationError):
            TypeAdapter(type_).validate_python("z")

    def test_enum_int(self):
        class MyEnum(Enum):
            X = 1
            Y = 2

        validator = TypeAdapter(MyEnum)
        schema = validator.json_schema()
        type_ = json_schema_to_type(schema)
        assert type_ == Literal[1, 2]
        assert TypeAdapter(type_).validate_python(1) == 1
        assert TypeAdapter(type_).validate_python(2) == 2
        with pytest.raises(ValidationError):
            TypeAdapter(type_).validate_python(3)

    def test_choice(self):
        validator = TypeAdapter(Literal["x", "y"])
        schema = validator.json_schema()
        type_ = json_schema_to_type(schema)
        assert type_ == Literal["x", "y"]
        assert TypeAdapter(type_).validate_python("x") == "x"
        assert TypeAdapter(type_).validate_python("y") == "y"
        with pytest.raises(ValidationError):
            TypeAdapter(type_).validate_python("z")


class TestCrashPrevention:
    """Schemas that previously caused crashes should now be handled gracefully."""

    def test_boolean_schema_true(self):
        """Boolean schema True should return Any (JSON Schema draft-06+)."""
        assert json_schema_to_type(True) is Any

    def test_boolean_schema_false(self):
        """Boolean schema False should return an unsatisfiable type."""
        result = json_schema_to_type(False)
        with pytest.raises(ValidationError):
            TypeAdapter(result).validate_python("anything")

    def test_python_keyword_property_names(self):
        """Properties named after Python keywords should not crash."""
        schema = {
            "type": "object",
            "properties": {
                "class": {"type": "string"},
                "return": {"type": "integer"},
                "import": {"type": "boolean"},
            },
            "required": ["class"],
        }
        T = json_schema_to_type(schema)
        ta = TypeAdapter(T)
        result = ta.validate_python({"class": "A", "return": 1, "import": True})
        assert result.class_ == "A"  # ty:ignore[unresolved-attribute]

    def test_empty_enum(self):
        """Empty enum means no value is valid — should reject like a false schema."""
        schema = {
            "type": "object",
            "properties": {"status": {"enum": []}},
            "required": ["status"],
        }
        T = json_schema_to_type(schema)
        ta = TypeAdapter(T)
        with pytest.raises(ValidationError):
            ta.validate_python({"status": "anything"})

    def test_sanitized_name_collision(self):
        """Properties that collide after sanitization get deduplicated."""
        schema = {
            "type": "object",
            "properties": {
                "foo-bar": {"type": "string"},
                "foo_bar": {"type": "string"},
            },
        }
        T = json_schema_to_type(schema)
        field_names = [f.name for f in dataclasses.fields(T)]
        assert len(field_names) == 2
        assert len(set(field_names)) == 2

    def test_empty_property_name(self):
        """Empty and whitespace-only property names should not crash."""
        schema = {
            "type": "object",
            "properties": {
                "": {"type": "string"},
                " ": {"type": "integer"},
            },
        }
        T = json_schema_to_type(schema)
        field_names = [f.name for f in dataclasses.fields(T)]
        assert len(field_names) == 2
        assert len(set(field_names)) == 2
