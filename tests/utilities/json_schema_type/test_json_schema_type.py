"""Core JSON schema type conversion tests."""

from dataclasses import Field
from enum import Enum
from typing import Literal

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
