"""Tests for format handling in JSON schema conversion."""

from dataclasses import Field
from datetime import datetime

import pytest
from pydantic import AnyUrl, TypeAdapter, ValidationError

from fastmcp.utilities.json_schema_type import (
    json_schema_to_type,
)


def get_dataclass_field(type: type, field_name: str) -> Field:
    return type.__dataclass_fields__[field_name]  # ty: ignore[unresolved-attribute]


class TestFormatTypes:
    """Test suite for format type validation."""

    @pytest.fixture
    def datetime_format(self):
        return json_schema_to_type({"type": "string", "format": "date-time"})

    @pytest.fixture
    def email_format(self):
        return json_schema_to_type({"type": "string", "format": "email"})

    @pytest.fixture
    def uri_format(self):
        return json_schema_to_type({"type": "string", "format": "uri"})

    @pytest.fixture
    def uri_reference_format(self):
        return json_schema_to_type({"type": "string", "format": "uri-reference"})

    @pytest.fixture
    def json_format(self):
        return json_schema_to_type({"type": "string", "format": "json"})

    @pytest.fixture
    def mixed_formats_object(self):
        return json_schema_to_type(
            {
                "type": "object",
                "properties": {
                    "full_uri": {"type": "string", "format": "uri"},
                    "ref_uri": {"type": "string", "format": "uri-reference"},
                },
            }
        )

    def test_datetime_valid(self, datetime_format):
        validator = TypeAdapter(datetime_format)
        result = validator.validate_python("2024-01-17T12:34:56Z")
        assert isinstance(result, datetime)

    def test_datetime_invalid(self, datetime_format):
        validator = TypeAdapter(datetime_format)
        with pytest.raises(ValidationError):
            validator.validate_python("not-a-date")

    def test_email_valid(self, email_format):
        validator = TypeAdapter(email_format)
        result = validator.validate_python("test@example.com")
        assert isinstance(result, str)

    def test_email_invalid(self, email_format):
        validator = TypeAdapter(email_format)
        with pytest.raises(ValidationError):
            validator.validate_python("not-an-email")

    def test_uri_valid(self, uri_format):
        validator = TypeAdapter(uri_format)
        result = validator.validate_python("https://example.com")
        assert isinstance(result, AnyUrl)

    def test_uri_invalid(self, uri_format):
        validator = TypeAdapter(uri_format)
        with pytest.raises(ValidationError):
            validator.validate_python("not-a-uri")

    def test_uri_reference_valid(self, uri_reference_format):
        validator = TypeAdapter(uri_reference_format)
        result = validator.validate_python("https://example.com")
        assert isinstance(result, str)

    def test_uri_reference_relative_valid(self, uri_reference_format):
        validator = TypeAdapter(uri_reference_format)
        result = validator.validate_python("/path/to/resource")
        assert isinstance(result, str)

    def test_uri_reference_invalid(self, uri_reference_format):
        validator = TypeAdapter(uri_reference_format)
        result = validator.validate_python("not a uri")
        assert isinstance(result, str)

    def test_json_valid(self, json_format):
        validator = TypeAdapter(json_format)
        result = validator.validate_python('{"key": "value"}')
        assert isinstance(result, dict)

    def test_json_invalid(self, json_format):
        validator = TypeAdapter(json_format)
        with pytest.raises(ValidationError):
            validator.validate_python("{invalid json}")

    def test_mixed_formats_object(self, mixed_formats_object):
        validator = TypeAdapter(mixed_formats_object)
        result = validator.validate_python(
            {"full_uri": "https://example.com", "ref_uri": "/path/to/resource"}
        )
        assert isinstance(result.full_uri, AnyUrl)
        assert isinstance(result.ref_uri, str)
