"""Tests for type constraints in JSON schema conversion."""

from dataclasses import Field
from datetime import datetime, timezone

import pytest
from pydantic import AnyUrl, TypeAdapter, ValidationError

from fastmcp.utilities.json_schema_type import (
    json_schema_to_type,
)


def get_dataclass_field(type: type, field_name: str) -> Field:
    return type.__dataclass_fields__[field_name]  # ty: ignore[unresolved-attribute]


class TestStringConstraints:
    """Test suite for string constraint validation."""

    @pytest.fixture
    def min_length_string(self):
        return json_schema_to_type({"type": "string", "minLength": 3})

    @pytest.fixture
    def max_length_string(self):
        return json_schema_to_type({"type": "string", "maxLength": 5})

    @pytest.fixture
    def pattern_string(self):
        return json_schema_to_type({"type": "string", "pattern": "^[A-Z][a-z]+$"})

    @pytest.fixture
    def email_string(self):
        return json_schema_to_type({"type": "string", "format": "email"})

    def test_min_length_accepts_valid(self, min_length_string):
        validator = TypeAdapter(min_length_string)
        assert validator.validate_python("test") == "test"

    def test_min_length_rejects_short(self, min_length_string):
        validator = TypeAdapter(min_length_string)
        with pytest.raises(ValidationError):
            validator.validate_python("ab")

    def test_max_length_accepts_valid(self, max_length_string):
        validator = TypeAdapter(max_length_string)
        assert validator.validate_python("test") == "test"

    def test_max_length_rejects_long(self, max_length_string):
        validator = TypeAdapter(max_length_string)
        with pytest.raises(ValidationError):
            validator.validate_python("toolong")

    def test_pattern_accepts_valid(self, pattern_string):
        validator = TypeAdapter(pattern_string)
        assert validator.validate_python("Hello") == "Hello"

    def test_pattern_rejects_invalid(self, pattern_string):
        validator = TypeAdapter(pattern_string)
        with pytest.raises(ValidationError):
            validator.validate_python("hello")

    def test_email_accepts_valid(self, email_string):
        validator = TypeAdapter(email_string)
        result = validator.validate_python("test@example.com")
        assert result == "test@example.com"

    def test_email_rejects_invalid(self, email_string):
        validator = TypeAdapter(email_string)
        with pytest.raises(ValidationError):
            validator.validate_python("not-an-email")


class TestNumberConstraints:
    """Test suite for numeric constraint validation."""

    @pytest.fixture
    def multiple_of_number(self):
        return json_schema_to_type({"type": "number", "multipleOf": 0.5})

    @pytest.fixture
    def min_number(self):
        return json_schema_to_type({"type": "number", "minimum": 0})

    @pytest.fixture
    def exclusive_min_number(self):
        return json_schema_to_type({"type": "number", "exclusiveMinimum": 0})

    @pytest.fixture
    def max_number(self):
        return json_schema_to_type({"type": "number", "maximum": 100})

    @pytest.fixture
    def exclusive_max_number(self):
        return json_schema_to_type({"type": "number", "exclusiveMaximum": 100})

    def test_multiple_of_accepts_valid(self, multiple_of_number):
        validator = TypeAdapter(multiple_of_number)
        assert validator.validate_python(2.5) == 2.5

    def test_multiple_of_rejects_invalid(self, multiple_of_number):
        validator = TypeAdapter(multiple_of_number)
        with pytest.raises(ValidationError):
            validator.validate_python(2.7)

    def test_minimum_accepts_equal(self, min_number):
        validator = TypeAdapter(min_number)
        assert validator.validate_python(0) == 0

    def test_minimum_rejects_less(self, min_number):
        validator = TypeAdapter(min_number)
        with pytest.raises(ValidationError):
            validator.validate_python(-1)

    def test_exclusive_minimum_rejects_equal(self, exclusive_min_number):
        validator = TypeAdapter(exclusive_min_number)
        with pytest.raises(ValidationError):
            validator.validate_python(0)

    def test_maximum_accepts_equal(self, max_number):
        validator = TypeAdapter(max_number)
        assert validator.validate_python(100) == 100

    def test_maximum_rejects_greater(self, max_number):
        validator = TypeAdapter(max_number)
        with pytest.raises(ValidationError):
            validator.validate_python(101)

    def test_exclusive_maximum_rejects_equal(self, exclusive_max_number):
        validator = TypeAdapter(exclusive_max_number)
        with pytest.raises(ValidationError):
            validator.validate_python(100)


class TestDraftFourExclusiveBounds:
    """A boolean exclusiveMinimum/exclusiveMaximum tightens its numeric sibling."""

    def test_boolean_exclusive_maximum_excludes_sibling(self):
        validator = TypeAdapter(
            json_schema_to_type(
                {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "exclusiveMaximum": True,
                }
            )
        )
        assert validator.validate_python(3) == 3
        with pytest.raises(ValidationError):
            validator.validate_python(5)

    def test_boolean_exclusive_minimum_excludes_sibling(self):
        validator = TypeAdapter(
            json_schema_to_type(
                {
                    "type": "integer",
                    "minimum": 5,
                    "maximum": 9,
                    "exclusiveMinimum": True,
                }
            )
        )
        assert validator.validate_python(6) == 6
        with pytest.raises(ValidationError):
            validator.validate_python(5)

    def test_boolean_false_keeps_sibling_inclusive(self):
        validator = TypeAdapter(
            json_schema_to_type(
                {"type": "integer", "maximum": 5, "exclusiveMaximum": False}
            )
        )
        assert validator.validate_python(5) == 5
        with pytest.raises(ValidationError):
            validator.validate_python(6)

    def test_boolean_exclusive_bound_without_sibling_constrains_nothing(self):
        validator = TypeAdapter(
            json_schema_to_type({"type": "integer", "exclusiveMaximum": True})
        )
        assert validator.validate_python(1000) == 1000


class TestStringFormatConstraints:
    """String keywords apply to the raw string whatever its `format` (#4404)."""

    @pytest.mark.parametrize(
        ("schema", "valid", "invalid"),
        [
            ({"format": "phone", "maxLength": 3}, "abc", "abcd"),
            ({"format": "phone", "minLength": 3}, "abc", "ab"),
            ({"format": "uri-reference", "maxLength": 5}, "a/b", "too/long/path"),
            ({"format": "uri-reference", "pattern": "^/"}, "/a", "a"),
            ({"format": "email", "maxLength": 10}, "a@b.co", "someone@example.com"),
            (
                {"format": "uri", "maxLength": 14},
                "https://a.co",
                "https://example.com/x",
            ),
            (
                {"format": "date-time", "maxLength": 20},
                "2026-09-22T00:00:00Z",
                "2026-09-22T00:00:00.000000+00:00",
            ),
            ({"format": "json", "maxLength": 5}, '"ab"', '"abcdef"'),
        ],
    )
    def test_constraints_survive_format(self, schema, valid, invalid):
        validator = TypeAdapter(json_schema_to_type({"type": "string", **schema}))
        validator.validate_python(valid)
        with pytest.raises(ValidationError):
            validator.validate_python(invalid)

    def test_constrained_format_still_parses(self):
        schema = {"type": "string", "format": "date-time", "maxLength": 20}
        parsed = TypeAdapter(json_schema_to_type(schema)).validate_python(
            "2026-09-22T00:00:00Z"
        )
        assert parsed == datetime(2026, 9, 22, tzinfo=timezone.utc)

    def test_format_without_constraints_is_unchanged(self):
        assert (
            json_schema_to_type({"type": "string", "format": "date-time"}) is datetime
        )
        assert json_schema_to_type({"type": "string", "format": "uri"}) is AnyUrl

    def test_repeat_schema_maps_to_one_type(self):
        """The client converts a tool's output schema on every call, so repeats must hit its adapter cache."""
        schema = {"type": "string", "format": "email", "maxLength": 10}
        assert json_schema_to_type(schema) is json_schema_to_type(dict(schema))


class TestIntegralFloatCounts:
    """JSON allows counts like minLength to be written as 2.0, and they must still apply."""

    @pytest.mark.parametrize(
        ("schema", "valid", "invalid"),
        [
            ({"type": "string", "minLength": 2.0}, "ab", "a"),
            ({"type": "string", "maxLength": 2.0}, "ab", "abc"),
            (
                {"type": "string", "format": "date-time", "maxLength": 20.0},
                "2026-09-22T00:00:00Z",
                "2026-09-22T00:00:00.000000+00:00",
            ),
            (
                {"type": "array", "items": {"type": "string"}, "minItems": 1.0},
                ["a"],
                [],
            ),
        ],
    )
    def test_integral_float_counts_apply(self, schema, valid, invalid):
        validator = TypeAdapter(json_schema_to_type(schema))
        validator.validate_python(valid)
        with pytest.raises(ValidationError):
            validator.validate_python(invalid)

    def test_float_count_does_not_poison_a_later_int_count(self):
        json_schema_to_type({"type": "string", "format": "email", "maxLength": 30.0})
        validator = TypeAdapter(
            json_schema_to_type({"type": "string", "format": "email", "maxLength": 30})
        )
        validator.validate_python("a@b.co")


class TestHugeCounts:
    """Counts beyond any real length must not make a valid schema fail to convert."""

    @pytest.mark.parametrize(
        ("schema", "value"),
        [
            ({"type": "string", "maxLength": 10**20}, "abc"),
            ({"type": "string", "maxLength": 1e20}, "abc"),
            (
                {"type": "string", "format": "date-time", "maxLength": 1e20},
                "2026-09-22T00:00:00Z",
            ),
            ({"type": "array", "items": {"type": "integer"}, "maxItems": 10**20}, [1]),
        ],
    )
    def test_huge_maximum_constrains_nothing(self, schema, value):
        TypeAdapter(json_schema_to_type(schema)).validate_python(value)

    @pytest.mark.parametrize(
        ("schema", "value"),
        [
            ({"type": "string", "minLength": 10**20}, "abc"),
            ({"type": "array", "items": {"type": "integer"}, "minItems": 1e20}, [1]),
        ],
    )
    def test_huge_minimum_still_rejects(self, schema, value):
        with pytest.raises(ValidationError):
            TypeAdapter(json_schema_to_type(schema)).validate_python(value)
