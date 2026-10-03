"""Tests for container types in JSON schema conversion."""

from collections import UserDict
from dataclasses import Field, dataclass
from decimal import Decimal
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from fastmcp.utilities import json_schema_type as schema_types
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
        assert validator.validate_python(["a", "b"]) == ["a", "b"]

    def test_unique_items_rejects_duplicates(self, unique_items_array):
        validator = TypeAdapter(unique_items_array)
        with pytest.raises(ValidationError, match="Array items must be unique"):
            validator.validate_python(["a", "a", "b"])

    def test_unique_items_accepts_unique_objects(self):
        unique_objects = json_schema_to_type(
            {"type": "array", "items": {"type": "object"}, "uniqueItems": True}
        )
        validator = TypeAdapter(unique_objects)

        users = [{"id": 1, "admin": True}, {"id": 2, "admin": False}]

        assert validator.validate_python(users) == users

    def test_unique_items_rejects_duplicate_objects(self):
        unique_objects = json_schema_to_type(
            {"type": "array", "items": {"type": "object"}, "uniqueItems": True}
        )
        validator = TypeAdapter(unique_objects)

        with pytest.raises(ValidationError, match="Array items must be unique"):
            validator.validate_python(
                [{"id": 1, "admin": True}, {"admin": True, "id": 1}]
            )

    def test_unique_items_keeps_boolean_and_number_distinct(self):
        unique_items = json_schema_to_type(
            {"type": "array", "items": {}, "uniqueItems": True}
        )
        validator = TypeAdapter(unique_items)

        assert validator.validate_python([True, 1]) == [True, 1]

    def test_unique_items_is_preserved_in_generated_schema(self, unique_items_array):
        assert TypeAdapter(unique_items_array).json_schema()["uniqueItems"] is True

    @pytest.mark.parametrize(
        "values, duplicate",
        [
            ([None, None], True),
            ([True, True], True),
            (["value", "value"], True),
            ([1, 1.0], True),
            ([0, -0.0], True),
            ([True, 1], False),
            ([False, 0], False),
            ([2**53 + 1, float(2**53)], False),
            ([None, False, 0, "0", [], {}], False),
            ([[1, 2], [2, 1]], False),
            ([[True], [1]], False),
            ([{"a": 1, "b": 2}, {"b": 2, "a": 1.0}], True),
            ([{"a": [1, 2]}, {"a": [1, 2]}], True),
            ([{"a": True}, {"a": 1}], False),
            ([{"a": {"b": [None, 1]}}, {"a": {"b": [None, 1.0]}}], True),
        ],
    )
    def test_unique_items_json_equality(self, values: list[Any], duplicate: bool):
        validator = TypeAdapter(
            json_schema_to_type({"type": "array", "items": {}, "uniqueItems": True})
        )
        if duplicate:
            with pytest.raises(ValidationError, match="Array items must be unique"):
                validator.validate_python(values)
        else:
            assert validator.validate_python(values) == values

    @pytest.mark.parametrize("objects", [False, True])
    def test_unique_items_avoids_all_pairs_comparison(
        self, monkeypatch: pytest.MonkeyPatch, objects: bool
    ):
        comparisons = 0
        original = schema_types._json_values_equal

        def counted(left: Any, right: Any) -> bool:
            nonlocal comparisons
            comparisons += 1
            return original(left, right)

        monkeypatch.setattr(schema_types, "_json_values_equal", counted)
        values = [{"id": i} for i in range(200)] if objects else list(range(200))
        validator = TypeAdapter(
            json_schema_to_type({"type": "array", "items": {}, "uniqueItems": True})
        )
        assert validator.validate_python(values) == values
        # Guard against quadratic comparisons without machine-dependent timings.
        assert comparisons <= len(values)

    @pytest.mark.parametrize("objects", [False, True])
    def test_unique_items_large_array(self, objects: bool):
        values = (
            [{"id": i, "tags": ["value", False]} for i in range(4000)]
            if objects
            else list(range(4000))
        )
        validator = TypeAdapter(
            json_schema_to_type({"type": "array", "items": {}, "uniqueItems": True})
        )
        assert validator.validate_python(values) == values
        with pytest.raises(ValidationError, match="Array items must be unique"):
            validator.validate_python([*values, values[-1]])

    @pytest.mark.parametrize(
        "values, duplicate",
        [
            ([Decimal("1"), 1], True),
            ([1, Decimal("1")], True),
            ([Decimal("1"), 2], False),
            ([[1, 2], (1, 2)], True),
            ([UserDict({"a": [1]}), {"a": [1]}], True),
            ([UserDict({"a": [1]}), {"a": [2]}], False),
            ([{1: "a"}, {True: "a"}], True),
        ],
    )
    def test_unique_items_preserves_python_fallback(
        self, values: list[Any], duplicate: bool
    ):
        validator = TypeAdapter(
            json_schema_to_type({"type": "array", "items": {}, "uniqueItems": True})
        )
        if duplicate:
            with pytest.raises(ValidationError, match="Array items must be unique"):
                validator.validate_python(values)
        else:
            assert validator.validate_python(values) == values

    def test_unique_items_non_finite_numbers_preserve_python_equality(self):
        validator: TypeAdapter[Any] = TypeAdapter(
            json_schema_to_type({"type": "array", "items": {}, "uniqueItems": True})
        )
        nan = float("nan")
        assert len(validator.validate_python([nan, nan])) == 2
        with pytest.raises(ValidationError, match="Array items must be unique"):
            validator.validate_python([float("inf"), float("inf")])

    def test_unique_items_cyclic_python_value_preserves_fallback(self):
        cyclic: list[Any] = []
        cyclic.append(cyclic)
        validator: TypeAdapter[Any] = TypeAdapter(
            json_schema_to_type({"type": "array", "items": {}, "uniqueItems": True})
        )
        assert validator.validate_python([cyclic])[0] is cyclic

    @pytest.mark.parametrize("unique", [None, False])
    def test_non_unique_schema_does_not_validate_uniqueness(
        self, monkeypatch: pytest.MonkeyPatch, unique: bool | None
    ):
        def unexpected(value: Any) -> Any:
            pytest.fail("Non-unique arrays must not run uniqueness validation")

        monkeypatch.setattr(schema_types, "_validate_unique_items", unexpected)
        schema: dict[str, Any] = {"type": "array", "items": {}}
        if unique is not None:
            schema["uniqueItems"] = unique
        values = [{"id": 1}, {"id": 1}]
        assert (
            TypeAdapter(json_schema_to_type(schema)).validate_python(values) == values
        )


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

    @pytest.mark.parametrize(
        "input_type, expected_type",
        [
            # list[dict] roundtrips correctly (not list[Root()])
            (list[dict], list[dict[str, Any]]),
            # list[dict[str, Any]] stays the same
            (list[dict[str, Any]], list[dict[str, Any]]),
            # list[dict[str, str]] preserves value type
            (list[dict[str, str]], list[dict[str, str]]),
            # list[dict[str, int]] preserves value type
            (list[dict[str, int]], list[dict[str, int]]),
        ],
    )
    def test_list_of_dict_types_roundtrip(self, input_type, expected_type):
        """Ensure list[dict] schemas produce dict types, not dataclasses (issue #3867)."""
        schema = TypeAdapter(input_type).json_schema()
        generated_type = json_schema_to_type(schema)
        assert generated_type == expected_type

    def test_list_dict_validates_data(self):
        """list[dict] schema should validate actual dict data, not produce Root() (issue #3867)."""
        schema = TypeAdapter(list[dict]).json_schema()
        generated_type = json_schema_to_type(schema)
        validator = TypeAdapter(generated_type)
        result = validator.validate_python(
            [{"city": "NYC", "temp": 72}, {"city": "LA", "temp": 85}]
        )
        assert result == [{"city": "NYC", "temp": 72}, {"city": "LA", "temp": 85}]

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
