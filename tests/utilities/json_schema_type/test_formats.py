"""Tests for format handling in JSON schema conversion."""

from dataclasses import Field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from uuid import UUID

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
    def date_format(self):
        return json_schema_to_type({"type": "string", "format": "date"})

    @pytest.fixture
    def time_format(self):
        return json_schema_to_type({"type": "string", "format": "time"})

    @pytest.fixture
    def duration_format(self):
        return json_schema_to_type({"type": "string", "format": "duration"})

    @pytest.fixture
    def uuid_format(self):
        return json_schema_to_type({"type": "string", "format": "uuid"})

    @pytest.fixture
    def path_format(self):
        return json_schema_to_type({"type": "string", "format": "path"})

    @pytest.fixture
    def unknown_format(self):
        return json_schema_to_type({"type": "string", "format": "not-a-real-format"})

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

    def test_date_valid(self, date_format):
        validator = TypeAdapter(date_format)
        result = validator.validate_python("2024-01-01")
        assert isinstance(result, date)
        assert result == date(2024, 1, 1)

    def test_date_invalid(self, date_format):
        validator = TypeAdapter(date_format)
        with pytest.raises(ValidationError):
            validator.validate_python("not-a-date")

    def test_time_valid(self, time_format):
        validator = TypeAdapter(time_format)
        result = validator.validate_python("12:34:56")
        assert isinstance(result, time)
        assert result == time(12, 34, 56)

    def test_time_invalid(self, time_format):
        validator = TypeAdapter(time_format)
        with pytest.raises(ValidationError):
            validator.validate_python("not-a-time")

    def test_duration_valid(self, duration_format):
        validator = TypeAdapter(duration_format)
        result = validator.validate_python("P1D")
        assert isinstance(result, timedelta)
        assert result == timedelta(days=1)

    def test_duration_invalid(self, duration_format):
        validator = TypeAdapter(duration_format)
        with pytest.raises(ValidationError):
            validator.validate_python("not-a-duration")

    def test_uuid_valid(self, uuid_format):
        validator = TypeAdapter(uuid_format)
        result = validator.validate_python("00000000-0000-0000-0000-000000000007")
        assert isinstance(result, UUID)
        assert result == UUID(int=7)

    def test_uuid_invalid(self, uuid_format):
        validator = TypeAdapter(uuid_format)
        with pytest.raises(ValidationError):
            validator.validate_python("not-a-uuid")

    def test_path_valid(self, path_format):
        validator = TypeAdapter(path_format)
        result = validator.validate_python("/tmp/report.txt")
        assert isinstance(result, Path)
        assert result == Path("/tmp/report.txt")

    def test_unmapped_format_falls_back_to_string(self, unknown_format):
        validator = TypeAdapter(unknown_format)
        result = validator.validate_python("anything")
        assert isinstance(result, str)
